# Why this project — the reasoning behind the choice

Context for future-you, and preparation for the interview question
*"why did you build this?"* A candidate who can explain what they rejected
and why sounds different from one who picked the first idea they found.

---

## Target role

Data Analyst. The skills that actually gate ATS filters and interviews:

**Tier 1 — must appear verbatim on the resume**
- SQL (joins, `GROUP BY`/`HAVING`, subqueries, CTEs, window functions)
- Advanced Excel (pivot tables, XLOOKUP, INDEX-MATCH, Power Query)
- Power BI and/or Tableau
- Python: **pandas, NumPy, Matplotlib, Seaborn** (parsers match library
  names, not just "Python")

**Tier 2 — differentiators**
Statistics (distributions, correlation, hypothesis testing), data
cleaning / ETL, data modelling, DAX, PostgreSQL, cloud warehouse exposure.

This project exercises every Tier 1 item except Excel, plus most of Tier 2.

---

## Alternatives considered and rejected

| Idea | Why rejected |
|---|---|
| **Meta Ad Library creative intelligence** | Analytically the sharpest idea (inferring performance from creative longevity), but the official API only returns commercial ads for **EU/UK** — a Digital Services Act requirement. Outside those markets it serves political ads only. The Indian D2C angle, which was the whole value, is unavailable. Scraping the web UI was the alternative and sits in a terms-of-service grey area. |
| **Government tender / procurement analytics** | Strongest business case and genuinely novel findings, but data ingestion is heavy (free-text item descriptions across multiple portals). Too slow for the placement timeline. Still a good post-placement project. |
| **Quick-commerce price tracking** | Requires scraping consumer apps — ToS grey area. |
| **Judicial / political datasets** | Ruled out by preference. |
| **Fantasy cricket optimizer** | Good idea, strong data (Cricsheet), but sports domain was dropped. |
| **Mutual fund portfolio overlap** | Clean fallback; PDF/Excel ingestion across dozens of AMCs is the hard part, and portfolio analysis edges toward SEBI-regulated advice. |

## Why YouTube won

1. **Access works today.** API key, no approval, data flowing within an
   hour. Meta needs ~a week of verification and then hands back the wrong
   region.
2. **Timeline.** The only option that goes from zero to a finished dashboard
   inside two weeks.
3. **Legally clean.** Official, sanctioned API. No scraping, no grey area.
4. **Real commercial precedent.** vidIQ and TubeBuddy are large businesses
   selling exactly this; India is one of the biggest creator markets in the
   world.

---

## The honest limitation — state it before anyone else does

The public YouTube Data API exposes **no CTR, no watch time, no viewer
timezone.** Those are owner-only.

So the performance metric here is **views normalised by subscriber count**,
not true performance. That is a weaker signal than CTR would be.

**Do not oversell this in interviews.** Claiming to measure "what makes
videos succeed" when you are measuring correlates of view count is the
easiest challenge for someone who knows the API. The defensible claim is:

> "I measured what correlates with views, normalised for channel size,
> across N channels in M niches."

Normalising by subscribers is also the only honest way to compare a 50k
channel against a 5M one — raw views would just be measuring channel size.

(If you later build the paid product, OAuth *does* unlock CTR and watch
time for users who authorize. See `PRODUCT_ROADMAP.md`.)

---

## The two things worth talking about in an interview

### 1. The quota constraint and how it was solved

Naive approach: `search.list` to find videos (100 units), then fetch each
one — roughly **101 units per video**, dead after 99 videos against a
10,000/day budget.

This pipeline instead reads each channel's *uploads playlist*, whose ID
comes free with `channels.list`:

    channels.list       -> uploads playlist ID   (1u / 50 channels)
    playlistItems.list  -> video IDs             (1u / 50 videos)
    videos.list         -> full records          (1u / 50 videos)

**≈0.04 units/video — a 2500x improvement.** Same data.

This is a concrete "technical constraint I worked around" answer, and
`verify/verify_step3.py` fails the build if efficiency drifts above
0.2 u/video.

### 2. The time-series design decision

The API returns *current* view counts and has no history endpoint. Velocity
analysis ("does early traction predict the ceiling?") only exists if you
build the series yourself by sampling repeatedly.

Hence the split: `videos` holds static metadata, `video_snapshots` holds
repeated stat samples with `age_hours`. The collector tiers by age —
under-48h videos every run, older ones progressively less often — so quota
is spent where the data actually changes.

The lesson worth voicing: **elapsed time was the one input that could not be
bought, so collection started before any analysis code was written.**

---

## Methodology defences

Expect these challenges. Have the answers ready.

| Challenge | Answer |
|---|---|
| "Why log transform?" | View counts are log-normal; a few viral videos dominate the mean. Tests use `log10(views)` or rank-based methods (Spearman, Mann-Whitney) that assume no distribution. |
| "Why exclude Shorts?" | Completely different view distribution. Mixing them manufactures correlations that really just say "Shorts get views." |
| "You tested 16 features — isn't one bound to be significant?" | Yes. That's why results carry a Bonferroni-adjusted threshold alongside the raw p-value. |
| "Is this a random sample?" | No. Hand-picked channels. Findings describe these channels, not YouTube. Stated up front in the output. |
| "Correlation or causation?" | Correlation. Creators who use numbers in titles likely differ in many other ways. |
| "What about deleted videos?" | Survivorship bias — deleted and private videos are invisible. Acknowledged. |

**Null results are findings.** If day-of-week shows no effect, report that.
Slicing until something clears p<0.05 is p-hacking, and an interviewer who
knows statistics will spot it faster than you think.

---

## Resume framing

Suggested skills line (only list what you have actually built):

> **Data & Analytics:** SQL, Advanced Excel (Pivot Tables, Power Query,
> XLOOKUP), Python (pandas, NumPy, Matplotlib, Seaborn), Power BI,
> PostgreSQL, MongoDB, Statistics, Data Cleaning, Data Visualization

Bullet shape for this project — fill in real numbers once you have them,
and never before:

> Built an end-to-end analytics pipeline over the YouTube Data API v3
> (Python, PostgreSQL, Power BI); engineered a batched collection strategy
> that cut quota cost ~2500x versus the naive approach, enabling N videos
> across M channels; applied rank-based significance testing with
> Bonferroni correction to identify title and thumbnail features associated
> with view performance.

Two ATS mechanics worth remembering:
1. Mirror the job description's exact phrasing — "data visualisation" vs
   "dataviz" is a lost match.
2. Spell out abbreviations once: "Business Intelligence (BI)",
   "Extract, Transform, Load (ETL)".
