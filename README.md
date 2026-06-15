# 🛫 OWN Pre-Flight Scorer

Score a draft reel / carousel / script **before** it goes out — instead of finding
out how it did the next morning.

This is the answer to Dhyanesh's question ("why not run the scoring before you post?").
The **scoring** part of the daily report grades the *content* — hook, clarity, CTA,
brand fit — and none of that needs view/like numbers, so it can run on a draft. Only
the performance-trend and competitor-benchmark sections need real engagement data, so
those stay in the post-facto daily report.

It reuses the **exact** brand brief, brand-safety rule, and Gemini analysis from
`../own_scorer/scraper.py`, so a draft is judged by the same standard the daily report
uses after the fact.

## The point of it

The single highest-value catch is **brand safety**: a named or shown competitor is
un-undoable once it's live. The tool flags that as a hard blocker (🔴) regardless of
how good the content is, because naming a rival reads as FP trashing competitors to
promote his own brand — exactly the credibility hit we never take.

## What it gives you

For any draft:

- 🟢 **SHIP** / 🟡 **FIX THESE FIRST** / 🔴 **REWORK or HOLD** verdict
- A brand-safety gate (hard blocker if a competitor is named/shown)
- Per-dimension scores (hook, retention, proof, brand alignment, CTA, …)
- A predicted performance band (directional, not a guarantee)
- The top 3 exact fixes — actual replacement lines, not "improve the hook"

## Three modes

| Mode | Input | Engine | When |
|------|-------|--------|------|
| 🎬 Reel | `.mp4`/`.mov` + caption | Gemini 2.5 Pro (watches the video) | Reel is cut, before posting |
| 🖼️ Carousel | slide images (in order) + caption | Gemini 2.5 Pro (reads slides) | Slides designed, before posting |
| ✍️ Script / idea | hook + caption text | Groq (text only) | Earliest — before you even shoot |

## Run it

```bash
# from the repo root
cd preflight
python -m streamlit run app.py
```

Or just double-click **`run.bat`** on Windows.

## Keys

Same two keys as the daily pipeline: `GEMINI_API_KEY` and `GROQ_API_KEY`.
The app finds them in this order:

1. Pasted into the sidebar, or
2. Environment variables, or
3. `.streamlit/secrets.toml` (copy `.streamlit/secrets.toml.example` → `secrets.toml`
   and fill them in — this file is git-ignored).

## Honest caveat (worth saying out loud to the team)

The **predicted performance** band is directional. Reach is driven as much by timing,
collabs, and the algorithm as by the content itself — so treat this as a
**content-quality + brand-safety gate**, not a view oracle. The brand-safety check and
the exact-fix list are where the real, reliable value is.
