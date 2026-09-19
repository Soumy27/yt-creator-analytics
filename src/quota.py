"""
Quota accounting for the YouTube Data API v3.

THE CORE CONSTRAINT
-------------------
Every Google Cloud project gets 10,000 quota units per day, resetting at
midnight Pacific Time. Costs are wildly uneven:

    search.list      -> 100 units   (and sits in its own 100-call/day bucket)
    playlistItems.list ->  1 unit   (returns up to 50 items)
    videos.list      ->   1 unit    (accepts up to 50 IDs per call)
    channels.list    ->   1 unit    (accepts up to 50 IDs per call)

The naive pipeline is: search for videos, then fetch each one's stats.
That costs ~101 units per video and dies after ~99 videos.

The pipeline this project uses:
    channels.list  -> get the channel's "uploads" playlist ID   (1 unit / 50 channels)
    playlistItems.list -> enumerate that playlist, 50 at a time (1 unit / 50 videos)
    videos.list    -> batch those IDs, 50 at a time             (1 unit / 50 videos)

That is ~2 units per 50 videos = 0.04 units/video, a 2500x improvement.
With 9,000 usable units you can touch well over 200,000 videos per day.

This module tracks spend on disk so the counter survives process restarts,
and resets itself automatically at midnight Pacific.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import QUOTA_DAILY_BUDGET, STATE_DIR
from src.logging_setup import get_logger

log = get_logger(__name__)

# Quota costs per API method (units per CALL, not per item returned).
COSTS = {
    "channels.list": 1,
    "playlistItems.list": 1,
    "videos.list": 1,
    "search.list": 100,
    "playlists.list": 1,
    "commentThreads.list": 1,
}

# YouTube quota resets at midnight Pacific. Pacific is UTC-8 (PST) or UTC-7 (PDT).
# Using UTC-8 is the conservative choice: it makes us think the day rolls over
# an hour LATER than it might, so we never overspend a fresh budget.
_PACIFIC_OFFSET = timedelta(hours=-8)


class QuotaExceeded(RuntimeError):
    """Raised when the configured daily budget would be exceeded."""


def _quota_day() -> str:
    """The current quota day, as YYYY-MM-DD in Pacific time."""
    return (datetime.now(timezone.utc) + _PACIFIC_OFFSET).date().isoformat()


class QuotaTracker:
    """
    Tracks spend per API key, persisted to disk.

    Usage:
        q = QuotaTracker()
        q.charge("videos.list")          # raises QuotaExceeded if over budget
        print(q.remaining())
    """

    def __init__(self, key_id: str = "default", budget: int | None = None):
        self.key_id = key_id
        self.budget = budget if budget is not None else QUOTA_DAILY_BUDGET
        self.path: Path = STATE_DIR / f"quota_{key_id}.json"
        self._load()

    # ---------------------------------------------------------- persistence
    def _load(self) -> None:
        today = _quota_day()
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                if data.get("day") == today:
                    self.spent = int(data.get("spent", 0))
                    self.calls = data.get("calls", {})
                    return
            except (json.JSONDecodeError, ValueError):
                log.warning("Corrupt quota state at %s, resetting.", self.path)
        # New day, or no/bad state file
        self.spent = 0
        self.calls = {}
        self._save()

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(
                {"day": _quota_day(), "spent": self.spent, "calls": self.calls},
                indent=2,
            )
        )

    # ---------------------------------------------------------- accounting
    def cost_of(self, method: str) -> int:
        if method not in COSTS:
            raise KeyError(f"Unknown API method {method!r}. Add it to quota.COSTS.")
        return COSTS[method]

    def can_afford(self, method: str, n_calls: int = 1) -> bool:
        return self.spent + self.cost_of(method) * n_calls <= self.budget

    def charge(self, method: str, n_calls: int = 1) -> None:
        """Record spend. Raises QuotaExceeded BEFORE the call is made."""
        cost = self.cost_of(method) * n_calls
        if self.spent + cost > self.budget:
            raise QuotaExceeded(
                f"[{self.key_id}] {method} would cost {cost}u; "
                f"spent {self.spent}/{self.budget}. Budget exhausted."
            )
        self.spent += cost
        self.calls[method] = self.calls.get(method, 0) + n_calls
        self._save()

    def remaining(self) -> int:
        return max(0, self.budget - self.spent)

    def report(self) -> str:
        lines = [
            f"Quota [{self.key_id}] day={_quota_day()} "
            f"spent={self.spent}/{self.budget} remaining={self.remaining()}"
        ]
        for method, n in sorted(self.calls.items()):
            lines.append(f"    {method:<22} {n:>6} calls  {n * COSTS[method]:>7}u")
        return "\n".join(lines)


class QuotaPool:
    """
    Manages several API keys, each with its own quota.

    This is the scaling story: one key gives you 9,000 usable units/day.
    Five keys from five Google Cloud projects give you 45,000. The pool
    rotates to the next key automatically when the current one runs dry.
    """

    def __init__(self, keys: list[str], budget: int | None = None):
        if not keys:
            raise ValueError("QuotaPool needs at least one API key.")
        self.keys = keys
        self.trackers = [
            QuotaTracker(key_id=f"key{i}", budget=budget) for i in range(len(keys))
        ]
        self.idx = 0

    @property
    def current_key(self) -> str:
        return self.keys[self.idx]

    @property
    def current_tracker(self) -> QuotaTracker:
        return self.trackers[self.idx]

    def charge(self, method: str, n_calls: int = 1) -> str:
        """
        Charge the current key, rotating to the next if it cannot afford it.
        Returns the API key that should be used for this call.
        """
        for _ in range(len(self.keys)):
            tracker = self.trackers[self.idx]
            if tracker.can_afford(method, n_calls):
                tracker.charge(method, n_calls)
                return self.keys[self.idx]
            log.warning(
                "Key %s exhausted (%d/%d). Rotating.",
                tracker.key_id, tracker.spent, tracker.budget,
            )
            self.idx = (self.idx + 1) % len(self.keys)
        raise QuotaExceeded(
            f"All {len(self.keys)} API keys exhausted for today. "
            f"Quota resets at midnight Pacific."
        )

    def total_remaining(self) -> int:
        return sum(t.remaining() for t in self.trackers)

    def report(self) -> str:
        return "\n".join(t.report() for t in self.trackers)
