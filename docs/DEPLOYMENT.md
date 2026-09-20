# Deploying this as a public, always-on project

Right now collection depends on one laptop being awake. On the first day of
running it, one 6-hour window was already missed to sleep — and a missed window
is view history nobody can ever reconstruct, because the API has no history
endpoint. That is the reason to deploy, more than the public URL.

After this, three things are true:

- the collector runs on GitHub's machines, every 6 hours, whether your Mac is on or not
- the database lives in the cloud, not on `localhost`
- the site rebuilds weekly and is public at a URL you can send anyone

Everything below fits inside free tiers.

---

## What you have to do yourself

I cannot create accounts or sign in on your behalf. These four steps are yours;
everything they depend on is already committed.

### 1. A hosted Postgres — [Neon](https://neon.tech) or [Supabase](https://supabase.com)

Either free tier is ample: this dataset is well under 1 GB.

Create a project, then copy the **connection string**. It looks like:

```
postgresql://user:password@ep-something.aws.neon.tech/dbname?sslmode=require
```

Keep it somewhere safe for step 3. Treat it like a password — it *is* one.

### 2. Push this repo to GitHub

```bash
cd "/Users/soumydhiran/Projects/yt-creator-analytics"
gh repo create yt-creator-analytics --public --source=. --push
```

`.env` is gitignored, so your API key does not travel with it. Check that:

```bash
git ls-files | grep -c '^\.env$'    # must print 0
```

### 3. Add the secrets

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Value |
|---|---|
| `DATABASE_URL` | the connection string from step 1 |
| `YOUTUBE_API_KEY` | your key |
| `YOUTUBE_API_KEYS_EXTRA` | optional; more keys, comma separated |

Secrets are write-only — nobody, including you, can read them back afterwards.

### 4. Turn on Pages

Repo → **Settings → Pages → Source: GitHub Actions**.

Then run the workflows once by hand: **Actions → refresh → Run workflow**. The
first run does everything from an empty database — schema, channels, backfill,
features, analysis, site — so give it 45–90 minutes. Later runs are much shorter.

Your site appears at `https://<your-username>.github.io/yt-creator-analytics/`.

---

## Moving your existing data across (optional, but do it)

You have 31,975 videos and a snapshot history already. Backfilling metadata from
scratch takes an afternoon; **the snapshot history cannot be re-collected at
all**. Move it rather than starting over:

```bash
pg_dump --no-owner --no-acl yt_analytics > /tmp/yt_dump.sql
psql "postgresql://user:password@host/dbname?sslmode=require" < /tmp/yt_dump.sql
```

Then confirm the row counts match before you rely on it:

```bash
psql "<your DATABASE_URL>" -c "SELECT
  (SELECT COUNT(*) FROM channels) channels,
  (SELECT COUNT(*) FROM videos) videos,
  (SELECT COUNT(*) FROM video_snapshots) snapshots;"
```

---

## What runs, and when

| Workflow | Schedule | Does |
|---|---|---|
| `collect.yml` | every 6h | snapshot only — small, fast, must never be blocked |
| `refresh.yml` | Mondays 03:30 UTC | new videos, features, analysis, retention sweep, rebuild + deploy |

They are separate on purpose. Collection is the part with an unrecoverable
failure mode, so nothing slow or fragile shares a job with it.

---

## After deploying, switch off the local schedulers

Otherwise two collectors write to two different databases and your history
splits in half.

```bash
launchctl unload ~/Library/LaunchAgents/com.soumydhiran.ytanalytics.snapshot.plist
rm ~/Library/LaunchAgents/com.soumydhiran.ytanalytics.snapshot.plist
```

Also delete the weekly **Scheduled** task in the Claude desktop app sidebar — the
`refresh` workflow replaces it.

To point your local tools at the hosted database, add one line to `.env`; it
overrides the `PG*` settings:

```
DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require
```

---

## Terms you are now responsible for

Going public changes what applies to you.

**The 30-day rule.** The YouTube API Services Terms require stored API data to
be refreshed or deleted within 30 days. `scripts/retention_sweep.py` runs in the
weekly workflow and deletes what has stopped being refreshed. It refuses to act
if it would remove more than half the dataset, because that is a broken
collector rather than staleness — check before overriding it.

**Attribution.** The page shows other creators' public data. Link back to the
source videos and channels; it is both correct practice and what the terms
expect.

**Quota.** A static site means visitor traffic costs nothing — people read a
file, they do not hit the API. Quota is spent only by the workflows. If you ever
add live queries, that stops being true immediately.

---

## Known limits

- **Momentum needs 12h of snapshots**, and the velocity analysis needs ~7 days.
  Both report "not yet available" until then. This is elapsed time, not a bug,
  and neither can be hurried.
- **93 channels, chosen by hand.** Findings describe these channels, not
  YouTube. Do not let a public URL quietly upgrade that into a general claim.
- **No CTR or watch time anywhere.** Those are owner-only, via a different API.
  The site says so; keep it saying so.
