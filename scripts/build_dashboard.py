#!/usr/bin/env python3
"""
Rebuild the dashboard page from the live database.

The dashboard embeds its data rather than querying anything. The page is served
as a static file, while Postgres lives elsewhere, and a statically hosted page
cannot reach a database directly. So "refreshing" the dashboard means re-running
this and redeploying the output.

Run:    python scripts/build_dashboard.py
Out:    exports/dashboard.html   (publish this to the artifact URL)
"""
from __future__ import annotations

import datetime
import decimal
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from src import db
from src.config import EXPORTS_DIR, ROOT, db_url
from src.logging_setup import get_logger

log = get_logger("dashboard")
TEMPLATE = ROOT / "dashboard" / "template.html"
NICHE_ORDER = ["education_india", "finance_india", "food_india", "tech_india"]


def collect() -> dict:
    U = db_url()
    out: dict = {}

    out["kpi"] = db.query_one("""
        SELECT (SELECT COUNT(*) FROM channels) ch,
               (SELECT COUNT(*) FROM videos) vids,
               (SELECT COUNT(*) FROM videos WHERE is_short IS NOT TRUE) longform,
               (SELECT COUNT(*) FROM video_snapshots) snaps,
               (SELECT COUNT(*) FROM niches) niches,
               (SELECT COUNT(*) FROM video_thumb_features) thumbs""")

    out["niches"] = db.query("""
        SELECT n.name niche, COUNT(*) channels,
               COUNT(*) FILTER (WHERE c.subscriber_count>=1000000) large,
               COUNT(*) FILTER (WHERE c.subscriber_count BETWEEN 100000 AND 999999) mid,
               COUNT(*) FILTER (WHERE c.subscriber_count<100000) small,
               SUM(c.subscriber_count) subs
        FROM channels c JOIN niches n USING (niche_id) GROUP BY 1 ORDER BY 1""")

    out["dow"] = db.query("""
        SELECT publish_dow dow, COUNT(*) n,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY views_per_sub) med
        FROM v_video_facts WHERE is_short IS NOT TRUE AND views_per_sub IS NOT NULL
        GROUP BY 1 ORDER BY 1""")

    out["heat"] = db.query("""
        SELECT publish_dow dow, publish_hour_utc h, COUNT(*) n,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY views_per_sub) med
        FROM v_video_facts WHERE is_short IS NOT TRUE AND views_per_sub IS NOT NULL
        GROUP BY 1,2""")

    out["sat"] = [
        {"n": r["niche"], "m": r["ym"], "a": round(float(r["avg_views"] or 0)),
         "v": int(r["videos_published"] or 0)}
        for r in db.query("""
            SELECT niche, to_char(month,'YYYY-MM') AS ym, avg_views,
                   videos_published, active_channels
            FROM v_niche_saturation
            WHERE month >= NOW()-INTERVAL '24 months' AND avg_views IS NOT NULL
            ORDER BY niche, month""")]

    out["channels"] = [
        {"t": r["title"][:28], "n": r["niche"], "s": int(r["subs"] or 0),
         "v": round(float(r["vps"]), 5), "c": int(r["nvid"])}
        for r in db.query("""
            SELECT c.title, n.name niche, c.subscriber_count subs,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY f.views_per_sub) vps,
                   COUNT(f.video_id) nvid
            FROM channels c JOIN niches n USING (niche_id)
            JOIN v_video_facts f ON f.channel_id=c.channel_id AND f.is_short IS NOT TRUE
            WHERE f.views_per_sub IS NOT NULL
            GROUP BY 1,2,3 HAVING COUNT(f.video_id)>=5 ORDER BY 3 DESC""")]

    fpath = EXPORTS_DIR / "findings.json"
    out["findings"] = (json.loads(fpath.read_text()).get("significant_findings", [])
                       if fpath.exists() else [])

    # Age-stratified title effects — the same six-bracket test step7 runs, so the
    # page's verdicts always agree with findings.json rather than drifting from it.
    d = pd.read_sql("""
        SELECT f.views_per_sub vps, f.age_days,
               t.has_number, t.has_question, t.has_exclamation, t.has_brackets
        FROM v_video_facts f JOIN video_text_features t USING (video_id)
        WHERE f.is_short IS NOT TRUE AND f.views_per_sub>0""", U)
    d["bucket"] = pd.qcut(d.age_days, 6, labels=False, duplicates="drop")
    strata = {}
    for feat in ["has_number", "has_question", "has_exclamation", "has_brackets"]:
        rows = []
        for b in sorted(d.bucket.dropna().unique()):
            s = d[d.bucket == b]
            a, c = s[s[feat] == True].vps, s[s[feat] == False].vps
            if len(a) < 30 or len(c) < 30:
                continue
            rows.append({"lo": round(float(s.age_days.min())),
                         "hi": round(float(s.age_days.max())),
                         "ratio": round(float(a.median() / c.median()), 3),
                         "n": int(len(a))})
        A, B = d[d[feat] == True].vps, d[d[feat] == False].vps
        strata[feat] = {
            "overall": round(float(A.median() / B.median()), 3),
            "n_with": int(len(A)), "n_without": int(len(B)), "strata": rows,
            "robust": bool(rows and (all(r["ratio"] > 1 for r in rows)
                                     or all(r["ratio"] < 1 for r in rows))),
        }
    out["strata"] = strata

    v = pd.read_sql("SELECT view_count FROM v_video_facts "
                    "WHERE is_short IS NOT TRUE AND view_count>0", U)
    out["dist"] = {"median": float(v.view_count.median()),
                   "mean": float(v.view_count.mean()),
                   "p90": float(v.view_count.quantile(.9)),
                   "max": int(v.view_count.max())}
    hist, edges = np.histogram(np.log10(v.view_count), bins=22)
    out["loghist"] = {"counts": hist.tolist(), "edges": [round(e, 2) for e in edges]}

    # Creator guidance. Only the top few per niche are embedded — the view holds
    # ~2,000 rows and the page needs a shortlist, not a dictionary.
    kw = {}
    for kind in ("hashtag", "tag"):
        for order, key in (("lift_vs_niche DESC", "by_lift"),
                           ("used_last_7d DESC, n_videos DESC", "by_recent")):
            for r in db.query(f"""
                SELECT niche, word, n_videos, n_channels, n_channels_up,
                       used_last_7d, lift_vs_niche
                FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY niche ORDER BY {order}) rn
                      FROM v_niche_keywords WHERE kind = %s) x
                WHERE rn <= 6 ORDER BY niche, rn""", (kind,)):
                kw.setdefault(r["niche"], {}).setdefault(kind, {}).setdefault(key, []).append({
                    "w": r["word"], "n": int(r["n_videos"]), "ch": int(r["n_channels"]),
                    "up": int(r["n_channels_up"]), "r7": int(r["used_last_7d"]),
                    "lift": round(float(r["lift_vs_niche"] or 0), 2)})
    out["keywords"] = kw

    out["besttime"] = [
        {"n": r["niche"], "dow": int(r["publish_dow"]), "h": int(r["publish_hour_utc"]),
         "vids": int(r["n_videos"]), "med": round(float(r["median_views_per_sub"] or 0), 5)}
        for r in db.query("SELECT * FROM v_niche_best_time WHERE rank_in_niche <= 3 "
                          "ORDER BY niche, rank_in_niche")]

    out["heat"] = out.get("heat")  # keep the day/hour grid under its own key
    out["nicheheat"] = [
        {"n": r["niche"], "moving": int(r["videos_moving"] or 0),
         "vph": round(float(r["avg_views_per_hour"] or 0), 1),
         "mps": round(float(r["med_momentum_per_sub"] or 0), 9)}
        for r in db.query("SELECT * FROM v_niche_heat ORDER BY med_momentum_per_sub DESC NULLS LAST")]

    # Momentum — the trending signal. Empty until snapshots span 12h+, which is
    # correct rather than broken: a growth RATE cannot exist without two
    # readings far enough apart to measure between.
    out["momentum"] = [
        {"t": r["title"][:64], "c": r["channel_title"][:28], "n": r["niche"],
         "vid": r["video_id"], "g": int(r["views_gained"] or 0),
         "vph": round(float(r["views_per_hour"] or 0), 1),
         "mps": round(float(r["momentum_per_sub"] or 0), 8),
         "now": int(r["views_now"] or 0),
         "age": round(float(r["age_hours"] or 0)),
         "subs": int(r["subscriber_count"] or 0)}
        for r in db.query("""
            SELECT video_id, title, channel_title, niche, views_gained,
                   views_per_hour, momentum_per_sub, views_now, age_hours,
                   subscriber_count, rank_in_niche
            FROM v_niche_momentum ORDER BY niche, rank_in_niche""")
    ] if db.table_exists("videos") else []

    out["velocity"] = db.query_one("""
        WITH s AS (SELECT MIN(started_at) t0 FROM collection_runs WHERE script LIKE '%step4%')
        SELECT to_char((SELECT t0 FROM s),'Mon DD') started,
               ROUND(EXTRACT(EPOCH FROM (NOW()-(SELECT t0 FROM s)))/3600.0,1) hrs,
               to_char((SELECT t0 FROM s)+INTERVAL '72 hours','Mon DD') d72,
               to_char((SELECT t0 FROM s)+INTERVAL '168 hours','Mon DD') d168,
               (SELECT COUNT(*) FROM videos
                WHERE published_at>(SELECT t0 FROM s)) cand,
               (SELECT COUNT(DISTINCT DATE(captured_at)) FROM video_snapshots) days""")
    return out


def _json_default(o):
    if isinstance(o, decimal.Decimal):
        return float(o)
    if isinstance(o, (datetime.date, datetime.datetime)):
        return str(o)
    raise TypeError(str(type(o)))


def main() -> int:
    if not TEMPLATE.exists():
        log.error("Missing template at %s", TEMPLATE)
        return 1
    data = collect()
    payload = json.dumps(data, separators=(",", ":"), default=_json_default)
    html = TEMPLATE.read_text()
    if "__DASHBOARD_DATA__" not in html:
        log.error("Template has no __DASHBOARD_DATA__ placeholder")
        return 1
    out = EXPORTS_DIR / "dashboard.html"
    out.write_text(html.replace("__DASHBOARD_DATA__", payload))
    k = data["kpi"]
    log.info("Built %s (%.0f KB)", out, out.stat().st_size / 1024)
    log.info("  %s channels · %s videos · %s snapshots · %s thumbnails",
             f"{k['ch']:,}", f"{k['vids']:,}", f"{k['snaps']:,}", f"{k['thumbs']:,}")
    log.info("  %s days of snapshot history", data["velocity"]["days"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
