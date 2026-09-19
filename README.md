# YouTube Creator Economy Analytics

Data pipeline and analysis over the YouTube Data API v3: what title,
thumbnail and timing patterns associate with performance, whether early
view velocity predicts a video's ceiling, and whether a niche is saturating.

---

## THE ONE THING TO DO FIRST

**Get Step 4 (the snapshot collector) running on a schedule today, before
you write any analysis code.**

The API returns a video's *current* view count. There is no history
endpoint. Velocity analysis — the distinctive part of this project — is only
possible if you build the time series yourself by sampling the same videos
repeatedly.

Elapsed time is the one input you cannot buy back. Two weeks of snapshots is
a usable dataset; two days is not. Every day you delay is permanently missing
from the final analysis.

---

## Setup

```bash
git clone <your-repo> && cd yt-creator-analytics
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# OCR needs the tesseract BINARY, not just the Python package:
#   Ubuntu/Debian: sudo apt install tesseract-ocr
#   macOS:         brew install tesseract
#   Windows:       https://github.com/UB-Mannheim/tesseract/wiki

cp .env.example .env     # then fill in your API key and Postgres details
createdb yt_analytics
```

### Getting a YouTube API key
1. <https://console.cloud.google.com/> → create a project
2. APIs & Services → Library → enable **YouTube Data API v3**
3. Credentials → Create Credentials → **API key**
4. Paste into `.env` as `YOUTUBE_API_KEY`

Public data needs only an API key — no OAuth.

---

## The build sequence

Run each step, then its verifier. **Do not proceed while a verifier fails.**
Each verifier exits non-zero on failure, so you can chain them.

| # | Script | What it does | Quota |
|---|--------|--------------|-------|
| 1 | `step1_init_db.py` | Create schema + views | 0 |
| 2 | `step2_seed_channels.py` | Resolve channels from `config/niches.yaml` | ~1u/channel |
| 3 | `step3_backfill_videos.py` | Pull video metadata | ~0.04u/video |
| 4 | `step4_snapshot.py` | **Sample stats — run on a cron** | ~1u/50 videos |
| 5 | `step5_text_features.py` | Title/description features | 0 |
| 6 | `step6_thumbnails.py` | Download + CV features | 0 |
| 7 | `step7_analysis.py` | Statistical tests, findings | 0 |
| 8 | `step8_export.py` | CSVs for Power BI | 0 |

```bash
python scripts/step1_init_db.py         && python verify/verify_step1.py
# edit config/niches.yaml first — add 15-30 channels per niche
python scripts/step2_seed_channels.py   && python verify/verify_step2.py
python scripts/step3_backfill_videos.py && python verify/verify_step3.py
python scripts/step4_snapshot.py        && python verify/verify_step4.py
python scripts/step5_text_features.py   && python verify/verify_step5.py
python scripts/step6_thumbnails.py --limit 500 && python verify/verify_step6.py
python scripts/step7_analysis.py        && python verify/verify_step7.py
python scripts/step8_export.py --excel  && python verify/verify_step8.py
```

### Schedule the collector (do this after Step 3)

**Linux/macOS** — every 6 hours:
```cron
0 */6 * * * cd /path/to/yt-creator-analytics && /path/to/.venv/bin/python scripts/step4_snapshot.py >> logs/cron.log 2>&1
```

**GitHub Actions** — runs even when your laptop is off. Put your key and
database URL in repository secrets:
```yaml
name: snapshot
on:
  schedule: [{cron: "0 */6 * * *"}]
  workflow_dispatch:
jobs:
  collect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: pip install -r requirements.txt
      - run: python scripts/step4_snapshot.py
        env:
          YOUTUBE_API_KEY: ${{ secrets.YOUTUBE_API_KEY }}
          PGHOST: ${{ secrets.PGHOST }}
          PGDATABASE: ${{ secrets.PGDATABASE }}
          PGUSER: ${{ secrets.PGUSER }}
          PGPASSWORD: ${{ secrets.PGPASSWORD }}
```

Re-run `verify/verify_step4.py` every few days. It reports how many days of
history you have and whether the collector is still firing — that is how you
catch a silently dead cron before it costs you a week.

---

## The quota strategy (the technical centrepiece)

Every Google Cloud project gets **10,000 units/day**, resetting midnight
Pacific. Costs are uneven:

| Method | Cost | Returns |
|--------|------|---------|
| `search.list` | **100** | 50 results (own 100-call/day bucket) |
| `playlistItems.list` | **1** | 50 video IDs |
| `videos.list` | **1** | 50 full video records |

The naive pipeline uses `search.list` to find videos, then fetches each one:
**~101 units per video**, dead after 99 videos.

This project instead reads each channel's *uploads playlist* — every channel
has one, and its ID comes free with `channels.list`:

```
channels.list       -> uploads playlist ID   (1u per 50 channels)
playlistItems.list  -> video IDs             (1u per 50 videos)
videos.list         -> full records          (1u per 50 videos)
```

**~2 units per 50 videos = 0.04 u/video.** Same data, 2500x cheaper.
With 9,000 usable units that is 200,000+ videos a day.

Scaling further is legitimate and simple: quota is per *project*, not per
account. Create N Google Cloud projects, put the extra keys in
`YOUTUBE_API_KEYS_EXTRA`, and `QuotaPool` rotates automatically when one
runs dry.

---

## What the API does and does not give you

**Available** (any public video): views, likes, comments, duration, title,
description, tags, category, publish time, thumbnails, subscriber counts.

**NOT available** (owner-only, via the separate YouTube Analytics API, and
only for channels you control): **click-through rate, watch time, average
view duration, audience retention, viewer demographics and timezones.**

So this project's performance metric is **views normalised by subscriber
count**, not CTR. Be precise about that. Claiming to measure "what makes
videos succeed" when you are measuring correlates of view count is the
fastest way to lose credibility in an interview — and it is an easy
challenge for someone who knows the API.

Normalising by subscribers is also the only honest way to compare a 50k
channel against a 5M one; raw views would just measure channel size.

---

## Method notes you should be able to defend

- **Log transform.** View counts are log-normal; a few viral videos dominate
  the mean. Tests use `log10(views)` or rank-based methods (Spearman,
  Mann-Whitney) that assume no particular distribution.
- **Shorts excluded.** Their view distribution is completely different.
  Mixing them manufactures correlations that are really "shorts get views".
- **Multiple comparisons.** Testing 16 features at p<0.05 yields roughly one
  false positive by chance. Results carry a Bonferroni-adjusted threshold.
- **Not a random sample.** These are channels you picked. Findings describe
  these channels, not YouTube.
- **Correlation, not causation.** Creators who use numbers in titles likely
  differ in many other ways.
- **Survivorship.** Deleted and private videos are invisible.

Null results are legitimate findings. If day-of-week shows no effect, report
that. Slicing until something clears p<0.05 is p-hacking, and an interviewer
who knows statistics will spot it.

---

## Project layout

```
src/            config, db, quota, youtube client, utils, logging
scripts/        step1..step8 — the pipeline
verify/         one verifier per step
sql/            001_schema.sql, 002_views.sql
config/         niches.yaml — which channels to track
exports/        CSVs, findings.json, POWERBI_SETUP.md
data/           thumbnails, quota state
logs/           dated run logs
```

Analysis aggregation lives in SQL views (`sql/002_views.sql`), not pandas.
That is deliberate: pushing work into the database is what distinguishes an
analyst from someone who loads everything into a dataframe.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `403 quotaExceeded` | Budget spent. Resets midnight Pacific. Add keys via `YOUTUBE_API_KEYS_EXTRA`. |
| Handle `NOT FOUND` | Check spelling in `niches.yaml`; try the `UC...` channel ID instead. |
| Velocity analysis says "not enough data" | Not a bug — needs elapsed time. Keep the cron running. |
| 0% text detected on thumbnails | Tesseract *binary* missing: `apt install tesseract-ocr`. |
| 0% faces detected | Haar cascade failed to load; reinstall `opencv-python-headless`. |
| Excel mangles titles | Open via Data → From Text/CSV and pick UTF-8. |
| `connection to server ... failed` | Postgres not running, or `.env` credentials wrong. |

---

## Further reading (`docs/`)

- **`docs/PROJECT_RATIONALE.md`** — why this project over the alternatives,
  the two technical stories worth telling in an interview, the methodology
  challenges to expect and how to answer them, and resume framing.
- **`docs/PRODUCT_ROADMAP.md`** — how to turn this into something sellable:
  what OAuth unlocks from paying users (watch time, retention, demographics,
  and CTR via a *different* API), Google's verification requirements, and
  why the public benchmark dataset — not the OAuth layer — is the moat.

## Scaling this into a product (summary)

Yes, you can get CTR and watch time — but only from users who authorize your
app via OAuth, not from the public API. Watch time, retention and
demographics come from the Analytics API; **thumbnail CTR comes from the
separate Reporting API**, as bulk CSV reports, which is a second ingestion
pipeline. Sensitive-scope verification (domain ownership, privacy policy,
demo video) gates any real launch.

The product insight: OAuth alone just re-skins YouTube Studio. The value is
a creator's private numbers *benchmarked against* the public dataset this
repo builds — which is why the collector should keep running long after the
analysis ships.

Full detail in `docs/PRODUCT_ROADMAP.md`.

Ship the analysis first. A finished, honest project beats an ambitious
half-built one — especially on a placement timeline.
