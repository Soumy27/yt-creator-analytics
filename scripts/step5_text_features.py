#!/usr/bin/env python3
"""
STEP 5 — Extract title/description features.

No API calls, no quota. Pure local computation over stored rows, so it is
cheap to re-run as you invent new features.

Features chosen because they are things a creator can actually ACT on:
length, numbers, questions, caps, brackets, sentiment, emoji. "Your titles
average 72 characters and the ones under 55 do better" is advice. "Titles
have high TF-IDF variance" is not.

Run:      python scripts/step5_text_features.py
Verify:   python verify/verify_step5.py
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.logging_setup import get_logger

log = get_logger("step5")

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    _VADER = SentimentIntensityAnalyzer()
except ImportError:
    _VADER = None
    log.warning("vaderSentiment not installed; sentiment will be NULL")

COLS = [
    "video_id", "title_length", "title_word_count", "has_number", "has_question",
    "has_exclamation", "has_brackets", "caps_word_count", "caps_ratio",
    "sentiment_compound", "emoji_count", "tag_count", "description_length",
]

_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]",
    flags=re.UNICODE,
)
_WORD = re.compile(r"\b[\w']+\b", re.UNICODE)


def features(video_id: str, title: str, desc: str | None, tags: list | None) -> tuple:
    title = title or ""
    words = _WORD.findall(title)

    # A "caps word" is fully uppercase and at least 2 chars, so acronyms like
    # "AI" count but single letters like "I" don't.
    caps_words = [w for w in words if len(w) >= 2 and w.isupper()]

    letters = [c for c in title if c.isalpha()]
    caps_ratio = (sum(1 for c in letters if c.isupper()) / len(letters)) if letters else 0.0

    sentiment = None
    if _VADER and title.strip():
        sentiment = _VADER.polarity_scores(title)["compound"]

    return (
        video_id,
        len(title),
        len(words),
        bool(re.search(r"\d", title)),
        "?" in title,
        "!" in title,
        bool(re.search(r"[\[\](){}]", title)),
        len(caps_words),
        round(caps_ratio, 4),
        sentiment,
        len(_EMOJI.findall(title)),
        len(tags or []),
        len(desc or ""),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="Recompute for every video")
    args = ap.parse_args()

    sql = "SELECT v.video_id, v.title, v.description, v.tags FROM videos v"
    if not args.all:
        sql += (" LEFT JOIN video_text_features f USING (video_id)"
                " WHERE f.video_id IS NULL")

    vids = db.query(sql)
    if not vids:
        log.info("Nothing to compute (use --all to recompute).")
        return 0

    log.info("Computing text features for %d videos", len(vids))
    rows = [features(v["video_id"], v["title"], v["description"], v["tags"]) for v in vids]

    db.bulk_upsert("video_text_features", COLS, rows,
                   conflict_cols=["video_id"],
                   update_cols=[c for c in COLS if c != "video_id"])

    log.info("Done. %d rows.", len(rows))
    log.info("Next: python verify/verify_step5.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
