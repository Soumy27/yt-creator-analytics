#!/usr/bin/env python3
"""
STEP 6 — Download thumbnails and extract visual features.

THIS IS YOUR DIFFERENTIATOR. Most YouTube-analytics projects stop at the
metadata the API hands them. Computer vision over the thumbnails is what
makes this look like real work rather than a tutorial follow-along.

Features and why each one earns its place:
  brightness/contrast  — "do bright thumbnails win?" is testable
  colourfulness        — Hasler-Susstrunk metric, a real published measure
  face_count           — tests the "faces get clicks" folk wisdom quantitatively
  largest_face_frac    — close-up vs wide shot
  has_text/char_count  — tests the "text overlay" folk wisdom
  edge_density         — proxy for visual busyness/clutter

Thumbnail downloads hit i.ytimg.com, NOT the API, so they cost zero quota.
They do take bandwidth and time: budget ~1-2s per thumbnail.

Run:      python scripts/step6_thumbnails.py --limit 500
Verify:   python verify/verify_step6.py
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db
from src.config import THUMBS_DIR
from src.logging_setup import get_logger

log = get_logger("step6")

try:
    import cv2
    import numpy as np
except ImportError:
    print("Requires opencv-python-headless and numpy:\n"
          "  pip install opencv-python-headless numpy")
    raise

try:
    import pytesseract
    _HAS_OCR = True
except ImportError:
    _HAS_OCR = False
    log.warning("pytesseract not available; OCR fields will be empty. "
                "Install the tesseract BINARY too: apt install tesseract-ocr")

COLS = [
    "video_id", "width", "height", "brightness", "contrast", "saturation",
    "colourfulness", "dominant_r", "dominant_g", "dominant_b", "face_count",
    "largest_face_frac", "has_text", "text_char_count", "ocr_text", "edge_density",
]

_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)


def download(url: str, dest: Path, timeout: int = 20) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return True
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "yt-analytics/0.1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 100:
            return False
        dest.write_bytes(data)
        return True
    except Exception as exc:
        log.debug("download failed %s: %s", url, str(exc)[:80])
        return False


def colourfulness(img_bgr) -> float:
    """
    Hasler & Susstrunk (2003) colourfulness metric.
    Higher = more vivid. A published measure, not something invented here —
    worth saying so when presenting.
    """
    b, g, r = cv2.split(img_bgr.astype("float"))
    rg = np.absolute(r - g)
    yb = np.absolute(0.5 * (r + g) - b)
    std_root = np.sqrt(rg.std() ** 2 + yb.std() ** 2)
    mean_root = np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    return float(std_root + 0.3 * mean_root)


def dominant_colour(img_bgr, k: int = 3) -> tuple[int, int, int]:
    """Most common colour via k-means on a downsampled image."""
    small = cv2.resize(img_bgr, (80, 45), interpolation=cv2.INTER_AREA)
    data = small.reshape((-1, 3)).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
    _, labels, centers = cv2.kmeans(data, k, None, criteria, 3, cv2.KMEANS_RANDOM_CENTERS)
    counts = np.bincount(labels.flatten())
    b, g, r = centers[counts.argmax()]
    return int(r), int(g), int(b)


def analyse(video_id: str, path: Path) -> tuple | None:
    img = cv2.imread(str(path))
    if img is None:
        return None

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    brightness = float(gray.mean())
    contrast = float(gray.std())
    saturation = float(hsv[:, :, 1].mean())
    colour = colourfulness(img)
    r, g, b = dominant_colour(img)

    faces = _CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                      minSize=(30, 30))
    face_count = len(faces)
    largest_frac = 0.0
    if face_count:
        largest_frac = float(max(fw * fh for _, _, fw, fh in faces) / (w * h))

    # Canny edge density as a busyness proxy. Cluttered thumbnails vs clean
    # ones is a real design question creators argue about.
    edges = cv2.Canny(gray, 100, 200)
    edge_density = float((edges > 0).sum() / (w * h))

    ocr_text, char_count = None, 0
    if _HAS_OCR:
        try:
            ocr_text = pytesseract.image_to_string(gray).strip()
            ocr_text = " ".join(ocr_text.split())[:500] or None
            char_count = len(ocr_text or "")
        except Exception:
            pass

    return (
        video_id, w, h, round(brightness, 2), round(contrast, 2),
        round(saturation, 2), round(colour, 2), r, g, b,
        face_count, round(largest_frac, 5),
        char_count > 2, char_count, ocr_text, round(edge_density, 5),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--all", action="store_true", help="Recompute existing")
    ap.add_argument("--delay", type=float, default=0.15,
                    help="Seconds between downloads (be polite)")
    ap.add_argument("--order", choices=("random", "recent"), default="random",
                    help="random (default, unbiased) or recent (newest first)")
    ap.add_argument("--seed", type=int, default=42,
                    help="Seed for --order random, so runs are reproducible")
    args = ap.parse_args()

    # Sample RANDOMLY, not newest-first.
    #
    # "ORDER BY published_at DESC LIMIT n" is not a sample of the dataset, it
    # is the n most recent uploads — which is dominated by whichever channels
    # publish most often. A 500-row run drew 77 of its rows (15%) from a single
    # high-frequency channel and 35% from five channels, out of 79. Every
    # thumbnail correlation it produced was really that handful of channels'
    # house style: brightness came out rho=+0.30 and "significant", then fell
    # to +0.04 and non-significant once the sample widened to 3,620 rows.
    # Contrast flipped sign outright.
    #
    # verify_step7 warns about this exact failure — "few channels = channel
    # effects masquerade as content effects" — so the sampling must not
    # manufacture the concentration the verifier is watching for.
    #
    # setseed() makes the shuffle reproducible: the same --seed re-draws the
    # same sample, so a finding can be checked later against the same rows.
    order_by = "RANDOM()" if args.order == "random" else "v.published_at DESC"
    sql = ("SELECT v.video_id, v.thumbnail_url FROM videos v "
           "{join} WHERE v.thumbnail_url IS NOT NULL {extra} "
           "ORDER BY {order} LIMIT %s")
    sql = sql.format(
        join="" if args.all else "LEFT JOIN video_thumb_features f USING (video_id)",
        extra="" if args.all else "AND f.video_id IS NULL",
        order=order_by,
    )

    if args.order == "random":
        # setseed() applies to the CURRENT SESSION ONLY, and db.execute/db.query
        # each open their own connection — so seeding via db.execute would be
        # silently discarded and the sample would not be reproducible. Both
        # statements must share one cursor.
        log.info("Sampling RANDOMLY (seed=%d) — unbiased across channels", args.seed)
        with db.cursor(dict_rows=True) as cur:
            cur.execute("SELECT setseed(%s)", ((args.seed % 1000) / 1000.0,))
            cur.execute(sql, (args.limit,))
            vids = [dict(r) for r in cur.fetchall()]
    else:
        log.info("Sampling NEWEST-FIRST — biased toward high-frequency channels")
        vids = db.query(sql, (args.limit,))
    if not vids:
        log.info("Nothing to process.")
        return 0

    log.info("Processing %d thumbnails (0 quota cost)", len(vids))
    rows, dl_fail, an_fail = [], 0, 0

    for i, v in enumerate(vids, 1):
        vid = v["video_id"]
        dest = THUMBS_DIR / f"{vid}.jpg"

        if not download(v["thumbnail_url"], dest):
            dl_fail += 1
            continue

        feats = analyse(vid, dest)
        if feats is None:
            an_fail += 1
            continue

        rows.append(feats)
        db.execute("UPDATE videos SET thumbnail_path=%s WHERE video_id=%s",
                   (str(dest), vid))

        if i % 50 == 0:
            log.info("  %d/%d processed", i, len(vids))
        time.sleep(args.delay)

    if rows:
        db.bulk_upsert("video_thumb_features", COLS, rows,
                       conflict_cols=["video_id"],
                       update_cols=[c for c in COLS if c != "video_id"])

    log.info("")
    log.info("Analysed: %d   download failures: %d   analysis failures: %d",
             len(rows), dl_fail, an_fail)
    if rows:
        with_faces = sum(1 for r in rows if r[10] > 0)
        with_text = sum(1 for r in rows if r[12])
        log.info("  %d/%d (%.0f%%) have a detectable face",
                 with_faces, len(rows), 100 * with_faces / len(rows))
        if _HAS_OCR:
            log.info("  %d/%d (%.0f%%) have text overlay",
                     with_text, len(rows), 100 * with_text / len(rows))
    log.info("Next: python verify/verify_step6.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
