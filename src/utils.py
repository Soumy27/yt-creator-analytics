"""
Small dependency-free helpers shared across the project.

Kept separate from youtube.py so that tests and analysis scripts can use
them without pulling in the Google API client.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterator, Sequence


def chunked(seq: Sequence[Any], size: int) -> Iterator[list]:
    """
    Yield lists of at most `size` items.

    This is the batching primitive the whole quota strategy rests on:
    the YouTube API accepts up to 50 IDs per call at a flat 1-unit cost,
    so batching by 50 is a 50x saving over one call per item.
    """
    if size < 1:
        raise ValueError("size must be >= 1")
    seq = list(seq)
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


_ISO8601_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def parse_duration(iso: str | None) -> int | None:
    """
    Convert an ISO-8601 duration ('PT4M13S') to seconds.

    YouTube returns durations in this format. Shorts are <= 60s, which is
    how we separate them from long-form — they behave completely differently
    and mixing them ruins every correlation in the dataset.
    """
    if not iso:
        return None
    m = _ISO8601_DURATION.match(iso.strip())
    if not m:
        return None
    parts = {k: int(v) if v else 0 for k, v in m.groupdict().items()}
    total = (
        parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )
    # YouTube returns 'P0D' for live streams and active premieres — a duration
    # of literally zero. Returning 0 here would flag them as Shorts (<=60s) and
    # silently poison every duration-based comparison. Unknown, not zero.
    return total if total > 0 else None


def parse_ts(iso: str | None) -> datetime | None:
    """Parse an RFC-3339 timestamp ('2024-01-15T10:30:00Z') to aware UTC."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def safe_int(value: Any, default: int | None = None) -> int | None:
    """
    YouTube returns counts as strings, and omits them entirely when a
    creator has disabled likes or comments. Both cases must not crash.
    """
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit]
