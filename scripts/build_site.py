#!/usr/bin/env python3
"""
Build the public static site into ./site for GitHub Pages.

Static on purpose. The page embeds its data, so serving it costs no API quota
however many people visit — traffic and quota are completely decoupled. A live
app querying the database on every page load would put a public, uncontrolled
read path in front of both the database and the daily quota.

Run:  python scripts/build_site.py
Out:  site/index.html, site/.nojekyll
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import EXPORTS_DIR, ROOT
from src.logging_setup import get_logger

import scripts.build_dashboard as bd  # noqa: E402

log = get_logger("site")
SITE = ROOT / "site"


def main() -> int:
    if bd.main() != 0:
        return 1
    SITE.mkdir(exist_ok=True)
    src = EXPORTS_DIR / "dashboard.html"
    shutil.copy2(src, SITE / "index.html")
    # Without this, Pages runs the output through Jekyll and drops files whose
    # names begin with an underscore.
    (SITE / ".nojekyll").write_text("")
    log.info("Site built at %s (%.0f KB)", SITE, (SITE / "index.html").stat().st_size / 1024)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
