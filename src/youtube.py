"""
YouTube Data API v3 client wrapper.

Every call goes through the QuotaPool, so spend is always accounted for and
keys rotate automatically. Transient failures retry with exponential backoff;
quota errors do not retry, because retrying a 403 quotaExceeded just burns time.

IMPORTANT — what this API does and does NOT give you:

    AVAILABLE (public data, any video):
        views, likes, comments, duration, title, description, tags,
        category, publish timestamp, thumbnail URLs, channel subs/video count

    NOT AVAILABLE (owner-only, via the separate YouTube Analytics API,
    and only for channels you yourself control):
        click-through rate, watch time, average view duration,
        audience retention, viewer demographics, traffic sources

So this project's performance metric is VIEWS NORMALISED BY CHANNEL SIZE,
not CTR. Be precise about that when you present it.
"""
from __future__ import annotations

import time
from typing import Any, Iterator, Sequence

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import api_keys
from src.logging_setup import get_logger
from src.quota import QuotaExceeded, QuotaPool
from src.utils import chunked

log = get_logger(__name__)


class TransientAPIError(RuntimeError):
    """A retryable API failure (5xx, rate limit, network blip)."""


def _classify(err: HttpError) -> Exception:
    """Turn an HttpError into either a quota error or a transient one."""
    status = getattr(err.resp, "status", None)
    text = str(err)

    if status == 403 and ("quotaExceeded" in text or "dailyLimitExceeded" in text):
        return QuotaExceeded(f"API reported quota exhausted: {text[:200]}")
    if status == 403 and "rateLimitExceeded" in text:
        return TransientAPIError(f"Rate limited: {text[:200]}")
    if status in (500, 502, 503, 504):
        return TransientAPIError(f"Server error {status}: {text[:200]}")
    if status == 404:
        return err  # genuinely missing resource; caller decides
    return err


class YouTubeClient:
    """
    Quota-aware wrapper over the YouTube Data API v3.

    All list methods batch to the API's 50-item maximum, which is what keeps
    quota cost at ~1 unit per 50 items instead of 1 unit per item.
    """

    def __init__(self, budget: int | None = None):
        self.pool = QuotaPool(api_keys(), budget=budget)
        self._services: dict[str, Any] = {}

    def _service(self, key: str):
        if key not in self._services:
            self._services[key] = build(
                "youtube", "v3", developerKey=key, cache_discovery=False
            )
        return self._services[key]

    @retry(
        retry=retry_if_exception_type(TransientAPIError),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _call(self, method: str, resource: str, **params) -> dict:
        """Charge quota, then execute. Retries transient errors only."""
        key = self.pool.charge(method)
        svc = self._service(key)
        endpoint = getattr(svc, resource)()
        try:
            return endpoint.list(**params).execute()
        except HttpError as err:
            raise _classify(err) from err

    # ------------------------------------------------------------ channels
    def get_channels(self, channel_ids: Sequence[str]) -> list[dict]:
        """
        Fetch channel metadata, 50 IDs per call (1 unit each).

        The critical field is contentDetails.relatedPlaylists.uploads — the
        playlist ID containing every public upload. That ID is what lets us
        avoid search.list entirely.
        """
        out: list[dict] = []
        for batch in chunked(channel_ids, 50):
            resp = self._call(
                "channels.list",
                "channels",
                part="snippet,statistics,contentDetails",
                id=",".join(batch),
                maxResults=50,
            )
            out.extend(resp.get("items", []))
        return out

    def resolve_handle(self, handle: str) -> dict | None:
        """
        Resolve an @handle to a channel. Costs 1 unit via forHandle.
        Accepts '@mkbhd' or 'mkbhd'.
        """
        h = handle if handle.startswith("@") else f"@{handle}"
        resp = self._call(
            "channels.list",
            "channels",
            part="snippet,statistics,contentDetails",
            forHandle=h,
            maxResults=1,
        )
        items = resp.get("items", [])
        return items[0] if items else None

    # ------------------------------------------------------------ playlist
    def iter_playlist_video_ids(
        self, playlist_id: str, max_items: int | None = None
    ) -> Iterator[tuple[str, str]]:
        """
        Yield (video_id, published_at) from a playlist, 50 per call, 1 unit each.

        This replaces search.list. Same result, 100x cheaper.
        """
        token = None
        seen = 0
        while True:
            resp = self._call(
                "playlistItems.list",
                "playlistItems",
                part="contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=token,
            )
            for item in resp.get("items", []):
                cd = item.get("contentDetails", {})
                vid = cd.get("videoId")
                if vid:
                    yield vid, cd.get("videoPublishedAt")
                    seen += 1
                    if max_items and seen >= max_items:
                        return
            token = resp.get("nextPageToken")
            if not token:
                return

    # ------------------------------------------------------------ videos
    def get_videos(self, video_ids: Sequence[str]) -> list[dict]:
        """
        Fetch video metadata + statistics, 50 IDs per call (1 unit each).
        """
        out: list[dict] = []
        for batch in chunked(video_ids, 50):
            resp = self._call(
                "videos.list",
                "videos",
                part="snippet,statistics,contentDetails",
                id=",".join(batch),
                maxResults=50,
            )
            out.extend(resp.get("items", []))
            time.sleep(0.05)  # be polite; well under any rate limit
        return out

    # ------------------------------------------------------------ misc
    def quota_report(self) -> str:
        return self.pool.report()

    def quota_remaining(self) -> int:
        return self.pool.total_remaining()
