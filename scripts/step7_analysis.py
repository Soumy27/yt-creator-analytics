#!/usr/bin/env python3
"""
STEP 7 — Run the analysis and produce findings.

This is where the project earns its keep. Six analyses, each answering a
question a creator would actually ask, each with a significance test so you
are not just eyeballing a bar chart.

METHODOLOGICAL NOTES YOU SHOULD BE ABLE TO DEFEND IN AN INTERVIEW:

1. Views are log-normal, not normal. A handful of viral videos dominate the
   mean. Every test here works on log10(views) or uses rank-based methods
   (Spearman, Mann-Whitney) that do not assume normality.

2. Shorts are excluded by default. Their view distribution is completely
   different; mixing them manufactures correlations that are really just
   "shorts get more views".

3. Views are normalised by subscriber count. Comparing raw views across a
   50k channel and a 5M one measures channel size, not content quality.

4. Correlation is not causation, and the sample is not random — these are
   channels you chose. Say so before someone says it to you.

5. Multiple comparisons: testing many features inflates false positives.
   A Bonferroni-adjusted threshold is reported alongside the raw p-value.

Run:      python scripts/step7_analysis.py
          python scripts/step7_analysis.py --niche tech_reviews
Verify:   python verify/verify_step7.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
from scipy import stats

from src.config import EXPORTS_DIR, db_url
from src.logging_setup import get_logger

log = get_logger("step7")

MIN_SAMPLE = 30          # below this, don't report a test at all
ALPHA = 0.05


def load(niche: str | None) -> pd.DataFrame:
    """One row per long-form video with every feature joined."""
    where = "WHERE f.is_short IS NOT TRUE AND f.view_count IS NOT NULL"
    if niche:
        where += f" AND f.niche = '{niche}'"

    sql = f"""
        SELECT f.video_id, f.channel_id, f.channel_title, f.niche, f.title,
               f.published_at, f.publish_hour_utc, f.publish_dow,
               f.duration_seconds, f.view_count, f.like_count, f.comment_count,
               f.subscriber_count, f.views_per_sub, f.like_rate, f.comment_rate,
               f.age_days,
               t.title_length, t.title_word_count, t.has_number, t.has_question,
               t.has_exclamation, t.has_brackets, t.caps_word_count,
               t.caps_ratio, t.sentiment_compound, t.emoji_count, t.tag_count,
               th.brightness, th.contrast, th.saturation, th.colourfulness,
               th.face_count, th.largest_face_frac, th.has_text,
               th.text_char_count, th.edge_density
        FROM v_video_facts f
        LEFT JOIN video_text_features t  USING (video_id)
        LEFT JOIN video_thumb_features th USING (video_id)
        {where}
    """
    df = pd.read_sql(sql, db_url())
    log.info("Loaded %d long-form videos", len(df))
    return df


def prep(df: pd.DataFrame) -> pd.DataFrame:
    """Add the transformed targets every test uses."""
    df = df[df["view_count"] > 0].copy()
    # log10 because view distributions span orders of magnitude
    df["log_views"] = np.log10(df["view_count"])
    df["log_vps"] = np.log10(df["views_per_sub"].replace(0, np.nan))
    return df


def fmt_p(p: float, n_tests: int = 1) -> str:
    """p-value with a Bonferroni-corrected verdict."""
    thresh = ALPHA / max(1, n_tests)
    mark = "significant" if p < thresh else "not significant"
    return f"p={p:.4g} ({mark} at α={thresh:.4g})"


# ------------------------------------------------------------------ analyses
def a1_binary_features(df: pd.DataFrame, findings: list) -> None:
    print("\n" + "=" * 68)
    print("  1. TITLE FEATURES — does this element associate with more views?")
    print("=" * 68)
    print("  Mann-Whitney U (rank-based, no normality assumption)")
    print("  Effect = median ratio (with feature / without)\n")

    feats = ["has_number", "has_question", "has_exclamation", "has_brackets", "has_text"]
    feats = [f for f in feats if f in df.columns and df[f].notna().any()]
    n_tests = len(feats)

    for feat in feats:
        sub = df[df[feat].notna()]
        a = sub[sub[feat] == True]["log_vps"].dropna()
        b = sub[sub[feat] == False]["log_vps"].dropna()
        if len(a) < MIN_SAMPLE or len(b) < MIN_SAMPLE:
            print(f"  {feat:<18} SKIP (n={len(a)}/{len(b)}, need {MIN_SAMPLE} each)")
            continue

        u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
        ratio = 10 ** (a.median() - b.median())
        direction = "MORE" if ratio > 1 else "FEWER"
        print(f"  {feat:<18} n={len(a):>5}/{len(b):<5} "
              f"{ratio:>5.2f}x {direction:<6} {fmt_p(p, n_tests)}")

        if p < ALPHA / n_tests:
            findings.append({
                "analysis": "title_features", "feature": feat,
                "effect_ratio": round(float(ratio), 3), "p_value": float(p),
                "n_with": int(len(a)), "n_without": int(len(b)),
            })


def a2_continuous(df: pd.DataFrame, findings: list) -> None:
    print("\n" + "=" * 68)
    print("  2. CONTINUOUS FEATURES — Spearman rank correlation vs views/sub")
    print("=" * 68)
    print("  Spearman because the relationship needn't be linear.\n")

    feats = ["title_length", "title_word_count", "caps_word_count", "caps_ratio",
             "sentiment_compound", "emoji_count", "tag_count", "duration_seconds",
             "brightness", "contrast", "saturation", "colourfulness",
             "face_count", "largest_face_frac", "text_char_count", "edge_density"]
    feats = [f for f in feats if f in df.columns and df[f].notna().sum() >= MIN_SAMPLE]
    n_tests = len(feats)

    results, skipped = [], []
    for feat in feats:
        sub = df[[feat, "log_vps"]].dropna()
        if len(sub) < MIN_SAMPLE:
            continue
        # A feature with no variance yields an undefined correlation (NaN)
        # and a warning from scipy. Report it as "no variation" instead —
        # that is informative, not an error.
        if sub[feat].nunique() < 2:
            skipped.append(f"{feat} (no variation)")
            continue
        rho, p = stats.spearmanr(sub[feat], sub["log_vps"])
        if np.isnan(rho):
            skipped.append(f"{feat} (undefined)")
            continue
        results.append((feat, rho, p, len(sub)))

    results.sort(key=lambda r: abs(r[1]), reverse=True)
    for feat, rho, p, n in results:
        strength = ("strong" if abs(rho) > .5 else
                    "moderate" if abs(rho) > .3 else
                    "weak" if abs(rho) > .1 else "negligible")
        print(f"  {feat:<20} rho={rho:>+6.3f} ({strength:<10}) n={n:>5}  {fmt_p(p, n_tests)}")
        if p < ALPHA / n_tests and abs(rho) > 0.1:
            findings.append({
                "analysis": "continuous", "feature": feat,
                "spearman_rho": round(float(rho), 4),
                "p_value": float(p), "n": int(n),
            })

    if skipped:
        print(f"\n  Skipped (constant in this sample): {', '.join(skipped)}")


def a3_timing(df: pd.DataFrame, findings: list) -> None:
    print("\n" + "=" * 68)
    print("  3. PUBLISH TIMING")
    print("=" * 68)
    print("  NOTE: this is publish hour vs performance. The API does NOT")
    print("  expose viewer timezones, so this is not 'audience timezone'.\n")

    dows = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    by_dow = df.groupby("publish_dow")["log_vps"].agg(["count", "median"]).reset_index()
    by_dow = by_dow[by_dow["count"] >= 10]
    if len(by_dow) >= 3:
        print("  By day of week:")
        for _, r in by_dow.sort_values("median", ascending=False).iterrows():
            d = dows[int(r["publish_dow"])]
            print(f"    {d}  n={int(r['count']):>5}  median views/sub={10**r['median']:.4f}")

        groups = [g["log_vps"].dropna().values
                  for _, g in df.groupby("publish_dow") if len(g) >= 10]
        if len(groups) >= 3:
            h, p = stats.kruskal(*groups)
            print(f"\n    Kruskal-Wallis across days: {fmt_p(p)}")
            if p < ALPHA:
                findings.append({"analysis": "timing_dow", "p_value": float(p)})
            else:
                print("    -> No detectable day-of-week effect. That IS a finding;")
                print("       report it rather than hunting for a smaller slice.")

    # Hour buckets, because 24 individual hours slice the data too thin
    df = df.copy()
    df["hour_bucket"] = pd.cut(df["publish_hour_utc"],
                               bins=[-1, 5, 11, 17, 23],
                               labels=["00-05", "06-11", "12-17", "18-23"])
    by_h = df.groupby("hour_bucket", observed=True)["log_vps"].agg(["count", "median"])
    by_h = by_h[by_h["count"] >= 10]
    if len(by_h) >= 2:
        print("\n  By hour bucket (UTC):")
        for idx, r in by_h.sort_values("median", ascending=False).iterrows():
            print(f"    {idx}  n={int(r['count']):>5}  median views/sub={10**r['median']:.4f}")


def a4_velocity(df: pd.DataFrame, findings: list) -> None:
    print("\n" + "=" * 68)
    print("  4. EARLY VELOCITY -> EVENTUAL CEILING")
    print("=" * 68)
    print("  The distinctive analysis. Requires accumulated snapshots.\n")

    vel = pd.read_sql(
        "SELECT * FROM v_video_velocity WHERE is_short IS NOT TRUE "
        "AND views_24h IS NOT NULL AND views_latest IS NOT NULL",
        db_url(),
    )
    if len(vel) < MIN_SAMPLE:
        print(f"  NOT ENOUGH DATA: {len(vel)} videos have both a 24h reading")
        print(f"  and a later one (need {MIN_SAMPLE}).")
        print("\n  This is not a bug. It needs elapsed time: videos must be")
        print("  published WHILE your collector is running, then age past 24h.")
        print("  Keep the cron going and re-run this in a week or two.")
        return

    v = vel[(vel["views_24h"] > 0) & (vel["views_latest"] > 0)].copy()
    v["log24"] = np.log10(v["views_24h"])
    v["loglatest"] = np.log10(v["views_latest"])

    rho, p = stats.spearmanr(v["log24"], v["loglatest"])
    print(f"  24h views vs latest views: rho={rho:.3f}  n={len(v)}  {fmt_p(p)}")

    slope, intercept, r, p2, se = stats.linregress(v["log24"], v["loglatest"])
    print(f"  log-log regression: R²={r**2:.3f}  slope={slope:.3f}")
    print(f"    -> 24h views explain {100*r**2:.0f}% of variance in final views")

    if "frac_views_first_24h" in v.columns:
        fr = v["frac_views_first_24h"].dropna()
        if len(fr):
            print(f"\n  Share of views earned in first 24h:")
            print(f"    median={fr.median():.1%}  p25={fr.quantile(.25):.1%}  "
                  f"p75={fr.quantile(.75):.1%}")
            print("    Low share = sustained discovery (algorithm still serving it).")
            print("    High share = spike and die.")

    findings.append({
        "analysis": "velocity", "spearman_rho": round(float(rho), 4),
        "r_squared": round(float(r ** 2), 4), "n": int(len(v)), "p_value": float(p),
    })


def a5_saturation(findings: list) -> None:
    print("\n" + "=" * 68)
    print("  5. NICHE SATURATION")
    print("=" * 68)
    print("  Are views growing faster than channels entering?\n")

    sat = pd.read_sql(
        "SELECT * FROM v_niche_saturation WHERE month >= NOW() - INTERVAL '24 months' "
        "ORDER BY niche, month", db_url()
    )
    if sat.empty:
        print("  No data.")
        return

    for niche, g in sat.groupby("niche"):
        g = g.dropna(subset=["avg_views"])
        if len(g) < 4:
            continue
        x = np.arange(len(g))
        slope, _, r, p, _ = stats.linregress(x, np.log10(g["avg_views"].clip(lower=1)))
        trend = "SATURATING" if slope < -0.01 else "GROWING" if slope > 0.01 else "FLAT"
        pct = (10 ** slope - 1) * 100
        print(f"  {niche:<20} {trend:<11} {pct:>+6.1f}%/month in avg views  "
              f"(n={len(g)} months, {fmt_p(p)})")
        findings.append({
            "analysis": "saturation", "niche": niche,
            "monthly_pct_change": round(float(pct), 2),
            "trend": trend, "p_value": float(p),
        })


def a6_engagement(df: pd.DataFrame, findings: list) -> None:
    print("\n" + "=" * 68)
    print("  6. ENGAGEMENT vs REACH")
    print("=" * 68)
    print("  Do high-engagement videos actually get more views?\n")

    sub = df[["like_rate", "comment_rate", "log_vps"]].dropna()
    if len(sub) < MIN_SAMPLE:
        print(f"  Not enough data (n={len(sub)})")
        return

    for col in ("like_rate", "comment_rate"):
        rho, p = stats.spearmanr(sub[col], sub["log_vps"])
        print(f"  {col:<14} vs views/sub: rho={rho:+.3f}  n={len(sub)}  {fmt_p(p, 2)}")
        if p < ALPHA / 2:
            findings.append({"analysis": "engagement", "feature": col,
                             "spearman_rho": round(float(rho), 4), "p_value": float(p)})

    print(f"\n  Typical rates in this dataset:")
    print(f"    like rate    median={sub['like_rate'].median():.2%}")
    print(f"    comment rate median={sub['comment_rate'].median():.3%}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", help="Restrict to one niche")
    ap.add_argument("--out", default=str(EXPORTS_DIR / "findings.json"))
    args = ap.parse_args()

    df = prep(load(args.niche))
    if len(df) < MIN_SAMPLE:
        log.error("Only %d videos. Need at least %d. Run step3 on more channels.",
                  len(df), MIN_SAMPLE)
        return 1

    print("\n" + "#" * 68)
    print(f"#  YOUTUBE CREATOR ANALYTICS — FINDINGS")
    print(f"#  {len(df):,} long-form videos | "
          f"{df['channel_id'].nunique()} channels | "
          f"{df['niche'].nunique()} niches")
    print("#" * 68)

    findings: list = []
    a1_binary_features(df, findings)
    a2_continuous(df, findings)
    a3_timing(df, findings)
    a4_velocity(df, findings)
    a5_saturation(findings)
    a6_engagement(df, findings)

    print("\n" + "=" * 68)
    print("  CAVEATS — state these before anyone else does")
    print("=" * 68)
    print("  - Channels were hand-picked, so this is not a random sample of")
    print("    YouTube. Findings describe THESE channels.")
    print("  - Correlation, not causation. Creators who use numbers in titles")
    print("    may differ in a dozen other ways.")
    print("  - The API exposes no CTR or watch time, so 'performance' here")
    print("    means views normalised by subscriber count.")
    print("  - Survivorship: deleted and private videos are invisible.")

    out = Path(args.out)
    out.write_text(json.dumps({
        "n_videos": int(len(df)),
        "n_channels": int(df["channel_id"].nunique()),
        "significant_findings": findings,
    }, indent=2))
    print(f"\n  {len(findings)} significant findings -> {out}")
    print("  Next: python verify/verify_step7.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
