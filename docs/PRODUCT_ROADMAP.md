# Turning this into a sellable product

> **Read this AFTER the analysis is shipped.** Nothing here should be built
> before the portfolio project is finished and the snapshot collector has
> been running for a few weeks. Every day spent on OAuth plumbing is a day
> the benchmark dataset isn't growing — and that dataset is the asset.

---

## 1. The core question: can you get CTR and watch time from paying users?

**Yes.** Once a creator signs in with Google OAuth and grants permission,
you can read *their* private analytics. The metrics that are owner-only for
the public API become available for every channel that authorizes your app.

This is exactly the vidIQ / TubeBuddy model.

### What you get per authorized user

| Metric | Available? | Which API |
|---|---|---|
| Watch time (`estimatedMinutesWatched`) | Yes | Analytics API |
| Average view duration | Yes | Analytics API |
| Audience retention (`audienceWatchRatio`) | Yes | Analytics API |
| Relative retention performance | Yes | Analytics API |
| Viewer geography, age, gender | Yes | Analytics API |
| Traffic sources | Yes | Analytics API |
| Subscribers gained / lost per video | Yes | Analytics API |
| Card & end-screen click rates | Yes | Analytics API |
| **Thumbnail impressions + CTR** | **Yes — but a different API** | **Reporting API only** |

---

## 2. The CTR gotcha — budget for this separately

`impressions` and `impressionClickThroughRate` are **not** in the YouTube
Analytics API metrics list. Developers try the obvious `reports.query`
endpoint, fail to find CTR, and wrongly conclude it is impossible. There is
a long-standing open issue tracking this gap.

CTR lives in the separate **YouTube Reporting API**, as:

    video_thumbnail_impressions
    video_thumbnail_impressions_ctr

and only inside the **Reach reports**:

    channel_reach_basic_a1
    channel_reach_combined_a1

### Why this matters architecturally

These are two different integrations with different shapes:

| | Analytics API | Reporting API |
|---|---|---|
| Model | Live targeted queries | Scheduled bulk jobs |
| Format | JSON response | Downloadable CSV files |
| Latency | Immediate | You create a reporting job, Google generates daily reports, you poll and download |
| Effort | Low | Higher — needs its own ingestion pipeline |

There is also a lag before the first bulk report is generated after a job is
created. Plan the onboarding UX around that: a new user will not see CTR
data on day one.

**Practical implication:** the CTR pipeline is a second, separate ingestion
path feeding the same Postgres. Do not assume one OAuth integration covers
everything.

---

## 3. Google verification — the gating hurdle

The YouTube analytics scopes are **sensitive**, so shipping to real users
requires verification:

    https://www.googleapis.com/auth/yt-analytics.readonly
    https://www.googleapis.com/auth/yt-analytics-monetary.readonly   (revenue, if needed)

### What Google requires

- Domain ownership verified in Google Search Console
- A published privacy policy on that domain
- Accurate OAuth consent screen (app name, logo, support email)
- A demo **video** showing how a user initiates and grants access
- Written justification for **each** scope requested

Review typically takes **3–5 business days**, though it can round-trip if
the justification is thin.

### Before verification

An unverified app is capped at a limited number of test users — commonly
cited as 100, but confirm the current figure in Google's docs when you get
there. That cap is fine for a beta; it is not fine for a launch.

**Request the minimum scopes.** Asking for monetary scopes you do not use is
a common reason verification gets bounced.

---

## 4. Quota changes shape with real users

The public-data pipeline in this repo spends quota on *your* collection.
Once users authorize, their queries also draw on your project's quota.

Plan for:
- Caching analytics responses aggressively (this data updates daily at best,
  not per request)
- Scheduling per-user refreshes rather than querying on every page load
- Monitoring per-user quota cost so one heavy account cannot starve others

The `QuotaPool` in `src/quota.py` already supports multiple keys; the same
rotation idea extends here.

---

## 5. The actual product insight

OAuth alone gives you one channel's private data. **That is not a product.**
YouTube Studio already shows a creator their own CTR, for free, with a
better UI than you will build.

The value is their private data **benchmarked against your public dataset**:

> "Your CTR is 4.2%" — worthless, Studio says that already.
>
> "Your CTR is 4.2%. The median in your niche at your subscriber tier is
> 6.1%. Here are the thumbnail traits the top quartile share, and three of
> your last ten thumbnails lack all of them." — that is a product.

The benchmark corpus is precisely what the pipeline in this repo builds.

**So the portfolio project is not separate from the product — it is the
moat.** The OAuth layer is commodity; a competent developer adds it in a
week. A multi-month cross-channel benchmark dataset cannot be bought or
rushed.

This also means: **keep the collector running even after placements.** Every
week it runs, the moat deepens.

---

## 6. Suggested build order

1. **Now → placements.** Finish the analysis. Keep the cron alive. Nothing
   else.
2. **Phase 1.** FastAPI (or Next.js API routes) over the existing Postgres.
   Public benchmark data only, no login. Proves the value without touching
   OAuth.
3. **Phase 2.** Google OAuth + Analytics API. Watch time, retention,
   demographics. Test-user cap is fine here.
4. **Phase 3.** Reporting API ingestion for thumbnail CTR. Separate pipeline,
   CSV-based.
5. **Phase 4.** Verification submission, then billing (Razorpay) and launch.

Your existing stack covers all of it — Next.js, Node, Postgres, Clerk,
Razorpay — so none of this is a rebuild.

---

## 7. Legal / compliance notes

- You are handling creators' private analytics. A real privacy policy
  describing what you store and for how long is mandatory, not optional.
- Google's API Services User Data Policy restricts what you may do with
  data obtained through these scopes — in particular, aggregating one
  user's private data into a product sold to others needs care. Benchmarks
  built from **public** data (what this repo collects) are clean; benchmarks
  built from **authorized users' private** data are not automatically so.
  Read the policy before designing that feature.
- Delete-on-request and account-deletion flows are part of verification
  expectations.

---

## Sources

- [YouTube Analytics Metrics](https://developers.google.com/youtube/analytics/metrics)
- [YouTube Reporting API — Channel Reports](https://developers.google.com/youtube/reporting/v1/reports/channel_reports)
- [Sensitive scope verification](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)
- [OAuth 2.0 scopes for Google APIs](https://developers.google.com/identity/protocols/oauth2/scopes)
- [Impressions & CTR FAQs — YouTube Help](https://support.google.com/youtube/answer/7628154)
