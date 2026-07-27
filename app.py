"""
OWN Pre-Flight Scorer
─────────────────────
Score a DRAFT reel / carousel / script BEFORE it goes out — the same content
scoring + brand-safety gate the daily report runs post-facto, but run up front.

This reuses the exact brand brief, brand-safety rule, and Gemini analysis from
own_scorer/scraper.py. The only thing it drops is the engagement half (views,
likes, median-vs-last-10) — those don't exist before a post is published.

The single highest-value check here is BRAND SAFETY: catching a named/shown
competitor BEFORE it's live, because that mistake is un-undoable once posted.

Run:  streamlit run app.py
Keys: set GEMINI_API_KEY and GROQ_API_KEY as env vars, or paste them in the
      sidebar. Same keys as the daily pipeline.
"""

import os
import json
import time
import base64
import tempfile

import requests
import streamlit as st

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

GEMINI_BASE  = "https://generativelanguage.googleapis.com"
GEMINI_MODEL = "gemini-2.5-pro"
GROQ_BASE    = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL   = "llama-3.3-70b-versatile"


def _secret(name: str) -> str:
    """Read a key from sidebar input → env var → .streamlit/secrets.toml, in that order."""
    try:
        from_secrets = st.secrets.get(name, "")  # raises if no secrets file exists
    except Exception:
        from_secrets = ""
    return (st.session_state.get(name) or os.getenv(name, "") or from_secrets).strip()


def _gemini_key() -> str:
    return _secret("GEMINI_API_KEY")


def _groq_key() -> str:
    return _secret("GROQ_API_KEY")


# ═══════════════════════════════════════════════════════════════════════════════
# BRAND BRIEF — shared across reel / carousel / script prompts
# (lifted verbatim from own_scorer/scraper.py so scoring stays identical)
# ═══════════════════════════════════════════════════════════════════════════════

BRAND_BRIEF = """BRAND IDENTITY BRIEF — Only What's Needed (@onlywhatsneeded):
- Rallying cry: "Food, powered by people." The brand is a co-creation movement, not just a product.
- Core discriminator: "The back of the pack comes to the front." Radical transparency is the METHOD, not just a claim.
- Brand personality: Calm confidence. Blunt, not brash. Transparent to the point of discomfort. Evidence-led. Challenger by default. NOT preachy, NOT academic, NOT arrogant.
- Tone: Evidence-led ("Here's the test. You can check it yourself."), Positive aggression (confident, not desperate), No gimmicks ("If it's not needed, it's not included"), Conversational but not casual.
- What they ARE: Open, factual, non-performative, grounded in science, calmly assertive.
- What they are NOT: Corporate, over-polished, preachy, wellnessy, defensive, trying-too-hard-to-be-funny.
- Emotional hooks that work for OWN: "Trust isn't a given, it's engineered." / "Clean isn't a claim. It's a consequence." / "From label padhega India to label likhega India."
- Target audience: Urban Indians who feel betrayed by food brands. Label-curious but not experts. They say: "I've been fooled for years. I want proof, not promises. And I want to be part of building it."
- Community: Called "OWNERS." They co-created the brand — 84% voted on the name."""

BRAND_SAFETY_RULE = """NON-NEGOTIABLE BRAND-SAFETY RULE (this overrides everything else, and is the single most important check):
- OWN and @foodpharmer NEVER attack, name, tag, or show another brand's product, label, or packaging. Calling out a named competitor reads as "FP is just promoting his own brand by trashing others" — it destroys the credibility that makes this account work.
- Every hook, fix, actionable, and verdict MUST stay brand-agnostic: compare against "most protein powders", "the category", "what you've been sold" — NEVER "Brand X" or a recognisable rival. Do NOT recommend a side-by-side comparison against a named competitor.
- This is un-undoable once published. Scan the ACTUAL draft (every frame/slide/overlay/spoken line AND the caption). If ANYTHING names, tags, shows, or visually features a specific rival brand's product/label/packaging, flag it as a blocker."""

EDUCATE_DONT_SELL_RULE = """EDUCATE, DON'T SELL (core content philosophy — second only to brand safety):
- Every piece of content must teach the viewer something they can use even if they never buy OWN — how to read a label, what an ingredient actually does, what a test result means, how the category misleads them.
- Hard-selling language is OFF-BRAND and must be flagged wherever it appears (spoken, on-screen, or in the caption): "order now", "link in bio", "grab yours", "before it sells out", discount codes, price-led urgency. The moment content reads as an ad, the trust that makes this account work is gone.
- The product may appear as EVIDENCE — its label, its test report, its ingredient list held up against the category norm — never as a pitch.
- The right CTA is educational or community-driven: "save this before your next supermarket run", "check your protein's label tonight", "tag someone who still trusts the front of the pack". NEVER recommend "order link in bio" or any purchase-push CTA as a fix.
- If the draft contains selling language, list every instance with its exact location and give the educational replacement."""

# Shared JSON tail asking for the brand-safety verdict on the DRAFT itself.
BRAND_SAFETY_FIELD = """  "brand_safety": {
    "violation": <true if the draft names/tags/shows a specific rival brand, else false>,
    "what": "If violation: name the rival and the exact frame/slide/timestamp/caption line where it appears. If clear: 'No competitor named, tagged, or shown.'",
    "severity": "<blocker | warning | clear>"
  },"""


# ═══════════════════════════════════════════════════════════════════════════════
# GEMINI / GROQ HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _upload_video_bytes(video_bytes: bytes, display_name: str, log) -> str:
    """Upload draft video bytes to the Gemini Files API (resumable). Returns file URI."""
    size_bytes = len(video_bytes)
    log(f"📤 Uploading {size_bytes / (1024*1024):.1f} MB to Gemini…")

    start = requests.post(
        f"{GEMINI_BASE}/upload/v1beta/files",
        params={"key": _gemini_key(), "uploadType": "resumable"},
        headers={
            "X-Goog-Upload-Protocol":              "resumable",
            "X-Goog-Upload-Command":               "start",
            "X-Goog-Upload-Header-Content-Length": str(size_bytes),
            "X-Goog-Upload-Header-Content-Type":   "video/mp4",
            "Content-Type":                        "application/json",
        },
        json={"file": {"display_name": display_name}},
        timeout=30,
    )
    start.raise_for_status()
    upload_url = start.headers["X-Goog-Upload-URL"]

    finish = requests.post(
        upload_url,
        headers={
            "Content-Length":        str(size_bytes),
            "X-Goog-Upload-Offset":  "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
        data=video_bytes,
        timeout=300,
    )
    finish.raise_for_status()
    info      = finish.json()
    file_uri  = info["file"]["uri"]
    file_name = info["file"]["name"]
    log("✓ Uploaded — waiting for Gemini to process…")

    for _ in range(40):
        time.sleep(5)
        status = requests.get(
            f"{GEMINI_BASE}/v1beta/{file_name}",
            params={"key": _gemini_key()},
            timeout=15,
        ).json()
        state = status.get("state")
        if state == "ACTIVE":
            log("✓ Video ready")
            return file_uri
        if state == "FAILED":
            raise RuntimeError("Gemini file processing failed")
    raise RuntimeError("Gemini file processing timed out")


def _call_gemini(parts: list, log) -> dict:
    """POST parts to Gemini, retry transient 5xx, parse JSON out of the response."""
    resp = None
    for attempt in range(4):
        resp = requests.post(
            f"{GEMINI_BASE}/v1beta/models/{GEMINI_MODEL}:generateContent",
            params={"key": _gemini_key()},
            json={
                "contents": [{"parts": parts}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192,
                                     "responseMimeType": "application/json"},
            },
            timeout=120,
        )
        if resp.status_code in (500, 503):
            wait = 15 * (2 ** attempt)
            log(f"Gemini {resp.status_code} — retrying in {wait}s (attempt {attempt+1}/4)…")
            time.sleep(wait)
            continue
        break
    if not resp.ok:
        raise RuntimeError(f"Gemini error {resp.status_code}: {resp.text[:300]}")

    cand = resp.json()["candidates"][0]["content"]["parts"]
    raw  = next((p["text"] for p in reversed(cand) if not p.get("thought")), cand[-1]["text"]).strip()
    return _extract_json(raw)


def _call_groq(prompt: str, log, max_tokens: int = 4096) -> dict:
    """Text-only scoring (script/idea stage) via Groq."""
    resp = None
    for attempt in range(3):
        resp = requests.post(
            GROQ_BASE,
            headers={"Authorization": f"Bearer {_groq_key()}", "Content-Type": "application/json"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            },
            timeout=90,
        )
        if resp.status_code in (429, 500, 503):
            wait = 10 * (2 ** attempt)
            log(f"Groq {resp.status_code} — retrying in {wait}s…")
            time.sleep(wait)
            continue
        break
    resp.raise_for_status()
    return _extract_json(resp.json()["choices"][0]["message"]["content"])


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        raw = raw[start:end + 1]
    else:
        raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


# ═══════════════════════════════════════════════════════════════════════════════
# PROMPTS
# ═══════════════════════════════════════════════════════════════════════════════

_DRAFT_CONTEXT = """ACCOUNT CONTEXT — THIS IS A DRAFT, NOT YET PUBLISHED:
- There are NO view or like numbers yet. Do NOT invent or assume any. Score the content purely on its merits and predict whether it WILL perform once live.
- Reach benchmark for prediction only: strong solo reels for this account clear ~3M+ views; under ~500k would be weak. Predict which band this draft is likely to land in, and why.
- Your job is to catch problems while they can still be fixed — be brutally specific."""


def reel_prompt(caption: str) -> str:
    return f"""You are Instagram's top growth specialist for D2C health brands in India, AND a brand strategist who deeply understands the Only What's Needed brand identity. You are reviewing a DRAFT reel BEFORE it is published, on two axes: (1) Instagram performance potential and (2) on-brand execution.

{BRAND_BRIEF}

WHAT MAKES OWN'S TOP REELS WORK:
- HOOK (first 2 seconds): A transparency claim or specific proof stat that triggers "finally, someone honest." e.g. "This whey has 4 ingredients. Most have 20+."
- STRUCTURE: Trust trigger → Specific proof (show, don't claim) → a takeaway the viewer can use → community moment. Zero fluff.
- EMOTION: TRUST ("this is different") or COMMUNITY PRIDE ("I helped build this").
- PRODUCT SPECIFICITY: Show actual label, actual test results, actual ingredient quantities. Vague claims destroy this brand's USP.
- TEXT OVERLAYS: Key facts (4 ingredients, 7 tests, 84% voted) must appear as on-screen text — 40%+ watch on mute.
- SUBTITLES: Since you are watching the actual video, audit the subtitles/captions directly — are they present, accurate to the audio, readable (size, contrast, not covered by the IG UI at the bottom), and in sync? Flag typos, mistimed lines, and any moment where spoken proof has no matching on-screen text.
- VISUAL EXECUTION: Judge what's on screen — framing, lighting, colour, cut rhythm, b-roll relevance, whether the label/test-report close-ups are actually legible on a phone. Cite timestamps for every visual issue.
- TRANSPARENCY FORMAT: Hold OWN's own label against the CATEGORY NORM in generic terms ("most protein powders pack in 20+ ingredients") — never name, tag, show, or single out a specific rival.
- SHAREABILITY: Must have a "tag your gym buddy" / "save this before buying supplements" moment.
- CTA: Educational or community CTAs ("save this", "check your label tonight", "tag someone who needs this") — never a purchase push.

WHAT KILLS PERFORMANCE: hook that takes >3s to land; leading with product features over emotional trust; no on-screen text for key facts; content that feels promotional rather than educational; no clear takeaway the viewer can act on; weak/generic CTA.

DO NOT PENALISE (intentional formats): long walk-throughs of OWN's own label/quantities/test reports; repeated on-screen proof numbers; slow reveal of lab results — these build trust by design.

{BRAND_SAFETY_RULE}

{EDUCATE_DONT_SELL_RULE}

{_DRAFT_CONTEXT}
- Caption: "{caption[:400]}"

Watch every second. For every weakness, give the EXACT fix — not "improve the hook" but "replace the opening line with: [exact text]".

Respond ONLY with valid JSON — no markdown:
{{
  "hook": {{"score": <1-10>, "timestamp_seconds": <int>, "what_it_is": "...", "verdict": "...", "exact_fix": "if score<8, exact replacement hook"}},
  "retention": {{"score": <1-10>, "drop_off_risk": "...", "fix": "..."}},
  "pacing": {{"rating": "<too fast | good | too slow>", "note": "..."}},
  "text_overlays": {{"rating": "<effective | missing | overwhelming | none>", "missing_facts": "ONE string", "fix": "ONE string"}},
  "subtitles": {{"score": <1-10>, "rating": "<good | hard_to_read | out_of_sync | typos | missing>", "issues": "specific problems with timestamps, or 'clean' if none", "fix": "exact fix"}},
  "visual_quality": {{"score": <1-10>, "issues": "framing/lighting/colour/cut/legibility problems with timestamps, or 'clean' if none", "fix": "exact fix"}},
  "emotion_trigger": {{"score": <1-10>, "type": "<betrayal | urgency | shock | curiosity | none>", "note": "..."}},
  "shareability": {{"score": <1-10>, "whatsapp_moment": "...", "fix": "..."}},
  "brand_proof_points": {{"score": <1-10>, "proof_shown": ["..."], "missed_opportunities": "..."}},
  "brand_alignment": {{"score": <1-10>, "on_brand": ["..."], "off_brand": ["..."], "co_creation_present": <bool>, "tone_verdict": "..."}},
  "educate_dont_sell": {{"score": <1-10 where 10 = pure education>, "viewer_takeaway": "what the viewer learns even if they never buy", "selling_language_found": ["every selling phrase with its exact location, empty if none"], "fix": "educational replacement for each selling phrase"}},
  "cta": {{"present": <bool>, "what_was_said": "...", "better_cta": "an educational/community CTA — never a purchase push"}},
{BRAND_SAFETY_FIELD}
  "overall_score": <1-10>,
  "predicted_performance": "Which view band is this likely to land in (e.g. 'likely under 500k — weak' / 'could clear 3M if the hook is fixed') and the single biggest lever to move it up",
  "what_holds_it_back": "3-4 sentences on the PRIMARY content gaps. If the reel is strong, say so plainly instead of inventing faults. Do NOT reference view counts — focus on content.",
  "top_3_actionables": ["ACTIONABLE 1 — [Category]: [exact change]", "ACTIONABLE 2 — ...", "ACTIONABLE 3 — ..."],
  "summary": "One punchy sentence — the single biggest thing that will make or break this reel"
}}"""


def carousel_prompt(caption: str, n_slides: int) -> str:
    return f"""You are Instagram's top growth specialist for D2C health brands in India, AND a brand strategist who deeply understands the Only What's Needed brand identity. You are reviewing a DRAFT carousel BEFORE it is published, on two axes: (1) Instagram performance potential and (2) on-brand execution.

{BRAND_BRIEF}

WHAT MAKES OWN'S TOP CAROUSELS WORK:
- HOOK SLIDE (slide 1): A transparency claim or proof stat that stops the scroll. "We have 4 ingredients. Most wheys have 20+." — bold, specific, verifiable.
- STRUCTURE: Trust trigger → Specific proof (show, don't claim) → a takeaway the viewer can use → community moment. Every slide must earn its swipe.
- PROOF SLIDES: Show actual label, test results, ingredient quantities. Generic claims destroy OWN's USP.
- TEXT: Key facts (4 ingredients, 7 tests, 84% voted) must appear as on-slide text.
- DESIGN EXECUTION: Since you are seeing the actual slides, audit them visually — text legibility on a phone (size, contrast, safe margins), visual hierarchy (one idea per slide), consistency of fonts/colours across slides, typos, and whether label/test-report shots are actually readable. Cite the slide number for every issue.
- LAST SLIDE CTA: Educational or community CTAs ("save this for your next supermarket run", "tag someone who needs this") — never a purchase push.
- SWIPEABILITY: Each slide must give a reason to swipe. If slide 2 can't beat slide 1 for curiosity, it's dead weight.
- CAPTION: Should deepen the trust argument, not repeat the slides.

WHAT KILLS PERFORMANCE: hook slide with no "I need to see the rest" pull; slides that feel like a brochure or an ad; caption with no proof/community angle; missing the co-creation angle; generic fitness content; no takeaway the viewer can act on.

{BRAND_SAFETY_RULE}

{EDUCATE_DONT_SELL_RULE}

{_DRAFT_CONTEXT}
- Slides provided: {n_slides}
- Caption: "{caption[:400]}"

Look at every slide. For every weakness, give the EXACT fix.

Respond ONLY with valid JSON — no markdown:
{{
  "hook_slide": {{"score": <1-10>, "what_it_shows": "...", "verdict": "...", "exact_fix": "if score<8, exact replacement hook slide"}},
  "swipeability": {{"score": <1-10>, "weakest_slide": "...", "fix": "..."}},
  "proof_shown": {{"score": <1-10>, "what_was_shown": ["..."], "missed_opportunities": "..."}},
  "design_quality": {{"score": <1-10>, "issues": "legibility/hierarchy/consistency/typo problems with slide numbers, or 'clean' if none", "fix": "exact fix"}},
  "educate_dont_sell": {{"score": <1-10 where 10 = pure education>, "viewer_takeaway": "what the viewer learns even if they never buy", "selling_language_found": ["every selling phrase with its slide/caption location, empty if none"], "fix": "educational replacement for each selling phrase"}},
  "caption_quality": {{"score": <1-10>, "verdict": "...", "fix": "exact improved opening 2 sentences"}},
  "cta": {{"present": <bool>, "what_was_said": "...", "better_cta": "an educational/community CTA — never a purchase push"}},
  "brand_alignment": {{"score": <1-10>, "on_brand": ["..."], "off_brand": ["..."], "co_creation_present": <bool>, "tone_verdict": "..."}},
{BRAND_SAFETY_FIELD}
  "overall_score": <1-10>,
  "predicted_performance": "Will this drive saves/shares once live? The single biggest lever to improve it.",
  "what_holds_it_back": "3-4 sentences on the PRIMARY content gaps. If it's strong, say so plainly.",
  "top_3_actionables": ["ACTIONABLE 1 — [Slide/Caption/CTA]: [exact change]", "ACTIONABLE 2 — ...", "ACTIONABLE 3 — ..."],
  "summary": "One punchy sentence — the single biggest make-or-break factor"
}}"""


def script_prompt(hook: str, caption: str, fmt: str) -> str:
    return f"""You are Instagram's top growth specialist for D2C health brands in India, AND a brand strategist who deeply understands the Only What's Needed brand identity. You are reviewing a DRAFT {fmt} at the SCRIPT/IDEA stage — there is no video or image yet, only the written hook and caption. Score whether this idea is worth producing, and how to sharpen it before anyone shoots it.

{BRAND_BRIEF}

WHAT WORKS FOR OWN: a hook that lands a transparency claim or proof stat in the first line; specific proof over vague claims (4 ingredients, 7 tests, 84% voted); a trust or community emotion; a takeaway the viewer can use even if they never buy; a save/share moment; the category-norm comparison kept generic.

{BRAND_SAFETY_RULE}

{EDUCATE_DONT_SELL_RULE}

{_DRAFT_CONTEXT}
- Format: {fmt}
- Draft hook / opening line: "{hook[:600]}"
- Draft caption: "{caption[:600]}"

Respond ONLY with valid JSON — no markdown:
{{
  "hook": {{"score": <1-10>, "verdict": "Will this opening line stop the scroll? Why/why not?", "exact_fix": "if score<8, the exact rewritten hook line"}},
  "idea_strength": {{"score": <1-10>, "verdict": "Is the underlying idea worth producing for this brand?", "note": "..."}},
  "proof_specificity": {{"score": <1-10>, "note": "Is it grounded in showable proof, or vague claims?", "fix": "..."}},
  "emotion_trigger": {{"score": <1-10>, "type": "<betrayal | urgency | shock | curiosity | community | none>", "note": "..."}},
  "educate_dont_sell": {{"score": <1-10 where 10 = pure education>, "viewer_takeaway": "what the viewer learns even if they never buy", "selling_language_found": ["every selling phrase in the hook/caption, empty if none"], "fix": "educational replacement for each selling phrase"}},
  "caption_quality": {{"score": <1-10>, "verdict": "...", "fix": "exact improved opening 2 sentences"}},
  "brand_alignment": {{"score": <1-10>, "on_brand": ["..."], "off_brand": ["..."], "tone_verdict": "..."}},
{BRAND_SAFETY_FIELD}
  "overall_score": <1-10>,
  "predicted_performance": "If produced well, which band could this land in, and the single biggest lever",
  "what_holds_it_back": "3-4 sentences on the primary gaps in the idea/script. If it's strong, say so plainly.",
  "top_3_actionables": ["ACTIONABLE 1 — [Category]: [exact change]", "ACTIONABLE 2 — ...", "ACTIONABLE 3 — ..."],
  "summary": "One punchy sentence — the verdict on whether to shoot this as-is, fix first, or drop it"
}}"""


# ═══════════════════════════════════════════════════════════════════════════════
# VERDICT
# ═══════════════════════════════════════════════════════════════════════════════

def compute_verdict(result: dict) -> tuple:
    """Returns (emoji, label, colour). Brand-safety violation is a hard block."""
    bs = result.get("brand_safety") or {}
    if bs.get("violation") or str(bs.get("severity", "")).lower() == "blocker":
        return ("🔴", "HOLD — brand-safety blocker", "#dc2626")

    score = result.get("overall_score")
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0
    if score >= 8:
        return ("🟢", "SHIP", "#16a34a")
    if score >= 6:
        return ("🟡", "FIX THESE FIRST", "#d97706")
    return ("🔴", "REWORK", "#dc2626")


# ═══════════════════════════════════════════════════════════════════════════════
# HISTORY — every scored draft is saved so past verdicts can be reopened.
# Durable store: a secret GitHub Gist (set GH_GIST_TOKEN + GH_GIST_ID in the
# app's Secrets — token needs only the `gist` scope). Without those it falls
# back to a local JSON file, which on Streamlit Cloud is wiped on redeploy.
# ═══════════════════════════════════════════════════════════════════════════════

HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.json")
GIST_API     = "https://api.github.com/gists"


def _gist_conf() -> tuple:
    return _secret("GH_GIST_TOKEN"), _secret("GH_GIST_ID")


@st.cache_data(ttl=60, show_spinner=False)
def _load_history_gist(gist_id: str) -> list:
    try:
        r = requests.get(
            f"{GIST_API}/{gist_id}",
            headers={"Authorization": f"Bearer {_gist_conf()[0]}",
                     "Accept": "application/vnd.github+json"},
            timeout=15,
        )
        r.raise_for_status()
        f = (r.json().get("files") or {}).get("history.json") or {}
        content = f.get("content", "[]")
        if f.get("truncated") and f.get("raw_url"):
            content = requests.get(f["raw_url"], timeout=15).text
        return json.loads(content or "[]")
    except Exception:
        return []


def _load_history() -> list:
    token, gist_id = _gist_conf()
    if token and gist_id:
        return _load_history_gist(gist_id)
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_history(hist: list):
    token, gist_id = _gist_conf()
    payload = json.dumps(hist[-100:], ensure_ascii=False)
    if token and gist_id:
        try:
            requests.patch(
                f"{GIST_API}/{gist_id}",
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json"},
                json={"files": {"history.json": {"content": payload}}},
                timeout=20,
            )
            _load_history_gist.clear()
        except Exception:
            pass
        return
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            f.write(payload)
    except Exception:
        pass


def _save_run(mode_label: str, title: str, result: dict):
    emoji, label, _ = compute_verdict(result)
    entry = {
        "ts":            time.strftime("%Y-%m-%d %H:%M"),
        "mode":          mode_label,
        "title":         (title or "untitled").strip().replace("\n", " ")[:60],
        "verdict_emoji": emoji,
        "verdict":       label,
        "score":         result.get("overall_score"),
        "result":        result,
    }
    hist = _load_history()
    hist.append(entry)
    _save_history(hist)


# ═══════════════════════════════════════════════════════════════════════════════
# UI
# ═══════════════════════════════════════════════════════════════════════════════

st.set_page_config(page_title="OWN Pre-Flight Scorer", page_icon="🛫", layout="centered")

st.title("🛫 OWN Pre-Flight Scorer")
st.caption("Score a draft **before** it goes out — same content scoring + brand-safety gate the daily report runs after the fact. "
           "The most important catch: a named or shown competitor, while it can still be pulled.")

with st.sidebar:
    st.subheader("How it works")
    st.caption("Reel + Carousel use Gemini 2.5 Pro (watches the actual media). "
               "Script stage uses Groq (text only — for checking an idea before you shoot).")
    if not _gemini_key() and not _groq_key():
        st.warning("No API keys configured. Set GEMINI_API_KEY / GROQ_API_KEY "
                   "in the app's Secrets (or as env vars locally).")

    st.divider()
    st.subheader("📜 History")
    if not all(_gist_conf()):
        st.caption("⚠️ Not durable yet — set GH_GIST_TOKEN and GH_GIST_ID in Secrets "
                   "so history survives app restarts.")
    _hist = _load_history()
    if not _hist:
        st.caption("No past scores yet — every scored draft will show up here.")
    else:
        for _i, _h in enumerate(reversed(_hist[-25:])):
            if st.button(f"{_h['verdict_emoji']} {_h.get('score','—')}/10 · {_h['ts']} · {_h['mode']} — {_h['title']}",
                         key=f"hist_{_i}", use_container_width=True):
                st.session_state["viewing_history"] = _h

mode = st.radio("What are you checking?",
                ["🎬 Reel (video)", "🖼️ Carousel (images)", "✍️ Script / idea (text only)"],
                horizontal=False)


def _render(result: dict):
    emoji, label, colour = compute_verdict(result)

    st.markdown(
        f"<div style='background:{colour};color:#fff;padding:18px 22px;border-radius:12px;"
        f"font-size:22px;font-weight:700;'>{emoji} {label}"
        f"<span style='float:right;font-size:26px;'>{result.get('overall_score','—')}/10</span></div>",
        unsafe_allow_html=True)

    # Brand-safety banner — always shown, it's the headline check
    bs = result.get("brand_safety") or {}
    if bs.get("violation") or str(bs.get("severity", "")).lower() == "blocker":
        st.error(f"🚫 **BRAND-SAFETY BLOCKER** — {bs.get('what', 'a competitor is named or shown')}. "
                 "Do not publish until this is removed. Naming/showing a rival reads as FP trashing competitors.")
    elif str(bs.get("severity", "")).lower() == "warning":
        st.warning(f"⚠️ **Brand-safety caution** — {bs.get('what', '')}")
    else:
        st.success(f"✅ Brand-safe — {bs.get('what', 'no competitor named, tagged, or shown')}")

    if result.get("summary"):
        st.markdown(f"**Verdict:** {result['summary']}")
    if result.get("predicted_performance"):
        st.info(f"📈 **Predicted:** {result['predicted_performance']}")

    if result.get("what_holds_it_back"):
        st.markdown("#### What holds it back")
        st.write(result["what_holds_it_back"])

    actionables = result.get("top_3_actionables") or []
    if actionables:
        st.markdown("#### Top 3 fixes")
        for a in actionables:
            st.markdown(f"- {a}")

    # Score grid for the per-dimension fields
    st.markdown("#### Score breakdown")
    rows = []
    for k, v in result.items():
        if isinstance(v, dict) and "score" in v:
            line = f"**{k.replace('_', ' ').title()}** — {v.get('score','—')}/10"
            for fk in ("rating", "verdict", "note", "issues", "exact_fix", "fix", "tone_verdict", "better_cta"):
                if v.get(fk):
                    line += f"\n  - *{fk.replace('_',' ')}:* {v[fk]}"
            rows.append(line)
    for r in rows:
        st.markdown(r)

    with st.expander("Raw JSON"):
        st.json(result)


# ── Live log area ──
log_box = st.empty()
_logs: list = []


def log(msg: str):
    _logs.append(msg)
    log_box.code("\n".join(_logs[-12:]))


# ── Viewing a past score? Show it instead of the scoring form ──
_viewing = st.session_state.get("viewing_history")
if _viewing:
    st.info(f"📜 Past score — {_viewing['mode']} · **{_viewing['title']}** · {_viewing['ts']}")
    if st.button("← Back to scoring"):
        st.session_state.pop("viewing_history", None)
        st.rerun()
    _render(_viewing.get("result") or {})
    st.stop()


# ── REEL ──
if mode.startswith("🎬"):
    caption = st.text_area("Caption (as it will be posted)", height=120,
                           placeholder="Paste the caption that will go with this reel…")
    video   = st.file_uploader("Draft reel (.mp4 / .mov)", type=["mp4", "mov", "m4v"])
    if st.button("Score this reel", type="primary", disabled=not video):
        if not _gemini_key():
            st.error("Set GEMINI_API_KEY in the app's Secrets (or as an env var).")
        else:
            try:
                with st.spinner("Uploading + watching the reel (60–120s)…"):
                    vbytes   = video.read()
                    file_uri = _upload_video_bytes(vbytes, video.name, log)
                    log("🤖 Scoring with Gemini 2.5 Pro…")
                    result = _call_gemini(
                        [{"text": reel_prompt(caption)},
                         {"file_data": {"mime_type": "video/mp4", "file_uri": file_uri}}],
                        log)
                log_box.empty()
                _save_run("🎬 Reel", video.name, result)
                _render(result)
            except Exception as e:
                st.error(f"Scoring failed: {e}")

# ── CAROUSEL ──
elif mode.startswith("🖼️"):
    caption = st.text_area("Caption (as it will be posted)", height=120,
                           placeholder="Paste the caption that will go with this carousel…")
    images  = st.file_uploader("Draft slides (in order)", type=["jpg", "jpeg", "png", "webp"],
                               accept_multiple_files=True)
    if st.button("Score this carousel", type="primary", disabled=not images):
        if not _gemini_key():
            st.error("Set GEMINI_API_KEY in the app's Secrets (or as an env var).")
        else:
            try:
                with st.spinner("Reading the slides…"):
                    parts = []
                    for i, img in enumerate(images[:8]):
                        data = img.read()
                        mime = img.type if img.type in ("image/jpeg", "image/png", "image/webp") else "image/jpeg"
                        parts.append({"inline_data": {"mime_type": mime,
                                                      "data": base64.b64encode(data).decode()}})
                        log(f"✓ Slide {i+1} loaded")
                    parts.append({"text": carousel_prompt(caption, len(parts))})
                    log("🤖 Scoring with Gemini 2.5 Pro…")
                    result = _call_gemini(parts, log)
                log_box.empty()
                _save_run("🖼️ Carousel", caption or f"{len(images)} slides", result)
                _render(result)
            except Exception as e:
                st.error(f"Scoring failed: {e}")

# ── SCRIPT / IDEA ──
else:
    fmt     = st.selectbox("Format this will become", ["reel", "carousel"])
    hook    = st.text_area("Hook / opening line", height=80,
                           placeholder="The first line the viewer sees or hears…")
    caption = st.text_area("Caption / script body", height=160,
                           placeholder="The caption or the rest of the script…")
    if st.button("Score this idea", type="primary", disabled=not (hook or caption)):
        if not _groq_key():
            st.error("Set GROQ_API_KEY in the app's Secrets (or as an env var).")
        else:
            try:
                with st.spinner("Scoring the idea…"):
                    log("🤖 Scoring with Groq…")
                    result = _call_groq(script_prompt(hook, caption, fmt), log)
                log_box.empty()
                _save_run("✍️ Script", hook or caption, result)
                _render(result)
            except Exception as e:
                st.error(f"Scoring failed: {e}")
