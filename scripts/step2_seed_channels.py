#!/usr/bin/env python3
"""
STEP 2 — Resolve channels and store them.

Reads config/niches.yaml, resolves every @handle to a channel ID, fetches
metadata, and stores it along with the uploads-playlist ID that Step 3 needs.

Quota: ~1 unit per handle resolved + 1 unit per 50 channels refreshed.
A 100-channel config costs about 102 units — 1% of your daily budget.

Run:      python scripts/step2_seed_channels.py
          python scripts/step2_seed_channels.py --dry-run
Verify:   python verify/verify_step2.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.config import ROOT
from src.logging_setup import get_logger
from src.quota import QuotaExceeded
from src.utils import parse_ts, safe_int, truncate
from src.youtube import YouTubeClient

log = get_logger("step2")

CONFIG_PATH = ROOT / "config" / "niches.yaml"


def load_config() -> list[dict]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing {CONFIG_PATH}")
    data = yaml.safe_load(CONFIG_PATH.read_text())
    niches = data.get("niches", [])
    if not niches:
        raise ValueError("config/niches.yaml has no niches defined")
    return niches


def upsert_niche(name: str, description: str | None) -> int:
    row = db.query_one(
        "INSERT INTO niches (name, description) VALUES (%s, %s) "
        "ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description "
        "RETURNING niche_id",
        (name, description),
    )
    return int(row["niche_id"])


def start_run(script: str) -> int:
    row = db.query_one(
        "INSERT INTO collection_runs (script) VALUES (%s) RETURNING run_id",
        (script,),
    )
    return int(row["run_id"])


def finish_run(run_id: int, status: str, rows: int, quota: int, err: str | None = None) -> None:
    db.execute(
        "UPDATE collection_runs SET finished_at=NOW(), status=%s, "
        "rows_written=%s, quota_spent=%s, error_message=%s WHERE run_id=%s",
        (status, rows, quota, err, run_id),
    )


def channel_row(item: dict, niche_id: int) -> tuple:
    sn = item.get("snippet", {})
    st = item.get("statistics", {})
    cd = item.get("contentDetails", {})
    uploads = cd.get("relatedPlaylists", {}).get("uploads")
    if not uploads:
        raise ValueError(f"Channel {item.get('id')} has no uploads playlist")
    return (
        item["id"],
        niche_id,
        sn.get("title", "(untitled)"),
        sn.get("customUrl"),
        truncate(sn.get("description"), 5000),
        sn.get("country"),
        parse_ts(sn.get("publishedAt")),
        uploads,
        safe_int(st.get("subscriberCount")),
        safe_int(st.get("videoCount")),
        safe_int(st.get("viewCount")),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Resolve and print, but write nothing")
    args = ap.parse_args()

    niches = load_config()
    total_channels = sum(len(n.get("channels", [])) for n in niches)
    log.info("Config: %d niches, %d channels", len(niches), total_channels)

    yt = YouTubeClient()
    run_id = None if args.dry_run else start_run("step2_seed_channels")
    written = 0
    start_quota = yt.quota_remaining()

    try:
        for niche in niches:
            name = niche["name"]
            entries = niche.get("channels", [])
            if not entries:
                log.warning("Niche %s has no channels, skipping", name)
                continue

            niche_id = 0 if args.dry_run else upsert_niche(name, niche.get("description"))
            log.info("--- niche %s (%d channels) ---", name, len(entries))

            # Split handles from raw IDs. Raw IDs batch 50-at-a-time (cheap);
            # handles must be resolved one at a time (1 unit each).
            raw_ids = [e for e in entries if e.startswith("UC")]
            handles = [e for e in entries if not e.startswith("UC")]

            items: list[dict] = []

            for h in handles:
                try:
                    item = yt.resolve_handle(h)
                except QuotaExceeded:
                    raise
                except Exception as exc:
                    log.error("  %s -> error: %s", h, str(exc)[:120])
                    continue
                if not item:
                    log.warning("  %s -> NOT FOUND (check spelling)", h)
                    continue
                items.append(item)
                log.info("  %s -> %s (%s subs)", h, item["snippet"]["title"],
                         item.get("statistics", {}).get("subscriberCount", "?"))

            if raw_ids:
                found = yt.get_channels(raw_ids)
                missing = set(raw_ids) - {i["id"] for i in found}
                for m in missing:
                    log.warning("  %s -> NOT FOUND", m)
                items.extend(found)
                for i in found:
                    log.info("  %s -> %s", i["id"], i["snippet"]["title"])

            if args.dry_run:
                continue

            rows = []
            for item in items:
                try:
                    rows.append(channel_row(item, niche_id))
                except ValueError as exc:
                    log.error("  %s", exc)

            if rows:
                db.bulk_upsert(
                    "channels",
                    ["channel_id", "niche_id", "title", "handle", "description",
                     "country", "published_at", "uploads_playlist",
                     "subscriber_count", "video_count", "view_count"],
                    rows,
                    conflict_cols=["channel_id"],
                    update_cols=["niche_id", "title", "handle", "description",
                                 "country", "uploads_playlist", "subscriber_count",
                                 "video_count", "view_count"],
                )
                # Also record a channel-size snapshot, so later analysis can
                # normalise by subs AT THE TIME rather than today's number.
                db.bulk_upsert(
                    "channel_snapshots",
                    ["channel_id", "subscriber_count", "video_count", "view_count"],
                    [(r[0], r[8], r[9], r[10]) for r in rows],
                    conflict_cols=["channel_id", "captured_at"],
                    update_cols=[],
                )
                db.execute(
                    "UPDATE channels SET last_refreshed_at = NOW() "
                    "WHERE channel_id = ANY(%s)",
                    ([r[0] for r in rows],),
                )
                written += len(rows)

    except QuotaExceeded as exc:
        log.error("Quota exhausted: %s", exc)
        if run_id:
            finish_run(run_id, "failed", written, start_quota - yt.quota_remaining(), str(exc))
        return 2
    except Exception as exc:
        log.exception("Failed: %s", exc)
        if run_id:
            finish_run(run_id, "failed", written, start_quota - yt.quota_remaining(), str(exc))
        return 1

    spent = start_quota - yt.quota_remaining()
    if run_id:
        finish_run(run_id, "ok", written, spent)

    log.info("")
    log.info("Channels stored: %d", written)
    log.info("Quota spent: %d units", spent)
    log.info(yt.quota_report())
    log.info("Next: python verify/verify_step2.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
