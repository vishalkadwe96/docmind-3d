import hashlib
import html
import json
import os
import re
import time

import httpx
import streamlit as st
import streamlit.components.v1 as components

API_URL = os.getenv("DOCMIND_API", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="DocMind 3D", page_icon="🧊", layout="wide")

# All styling lives here, so no separate style.css file is needed.
CSS = r'''@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap');

html, body, [class*="css"], .stApp { font-family: 'Space Grotesk', sans-serif; }

.stApp {
    background:
        radial-gradient(circle at 15% 20%, rgba(124,58,237,.35), transparent 40%),
        radial-gradient(circle at 85% 10%, rgba(6,182,212,.30), transparent 40%),
        radial-gradient(circle at 50% 100%, rgba(236,72,153,.25), transparent 45%),
        #070714;
    background-attachment: fixed;
    color: #e8e8f5;
}

header[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1rem; padding-bottom: 8rem; max-width: 980px; }

/* ---- Text colours fix ---- */
.stApp, .stApp p, .stApp li, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp h4,
[data-testid="stMarkdownContainer"], [data-testid="stMarkdownContainer"] *,
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2,
[data-testid="stSidebar"] p, [data-testid="stSidebar"] label,
[data-testid="stCaptionContainer"], [data-testid="stSidebar"] small {
    color: #e8e8f5 !important;
}
.katex, .katex * { color: #ffffff !important; }

/* keep text dark inside the uploaded file card (white box) */
[data-testid="stFileUploaderFile"] *,
[data-testid="stFileUploaderFileName"] { color: #111 !important; }
[data-testid="stFileUploader"] small { color: #b9b9d6 !important; }

/* Buttons text */
.stButton > button, .stButton > button * { color: #fff !important; }

/* Chat input dark glass */
[data-testid="stChatInput"] > div { background: transparent !important; }
[data-testid="stChatInput"] textarea {
    color: #fff !important;
    -webkit-text-fill-color: #fff !important;
    background: transparent !important;
}
[data-testid="stChatInput"] textarea::placeholder { color: #b9b9d6 !important; }

/* Hero title + subtitle (explicit colours so markdown styles don't override them) */
.hero { text-align: center; padding: 2.5rem 0 1.5rem; }
.hero h1 {
    margin: 0; font-size: 3.2rem; font-weight: 700; letter-spacing: 2px;
    color: #fff !important;
    text-shadow: 0 0 30px rgba(124,58,237,.9), 0 0 60px rgba(6,182,212,.6);
}
.hero p { margin: .6rem 0 0; color: #c9c9e6 !important; font-size: 1.1rem; }

/* Sidebar */
[data-testid="stSidebar"] {
    background: rgba(255,255,255,.04);
    backdrop-filter: blur(18px);
    border-right: 1px solid rgba(255,255,255,.08);
}
[data-testid="stFileUploader"] section {
    background: rgba(255,255,255,.05);
    border: 1.5px dashed rgba(124,58,237,.7);
    border-radius: 16px;
    transition: all .3s ease;
}
[data-testid="stFileUploader"] section:hover {
    transform: perspective(600px) rotateX(4deg) translateY(-3px);
    box-shadow: 0 14px 30px rgba(124,58,237,.35);
}

/* Chat messages as 3D glass cards */
[data-testid="stChatMessage"] {
    background: rgba(255,255,255,.06);
    border: 1px solid rgba(255,255,255,.12);
    border-radius: 18px;
    backdrop-filter: blur(14px);
    padding: 1rem 1.2rem;
    margin-bottom: 1rem;
    transform: perspective(1000px) rotateX(1.5deg);
    transition: transform .35s ease, box-shadow .35s ease;
    box-shadow: 0 10px 30px rgba(0,0,0,.35);
    animation: rise .5s ease both;
}
[data-testid="stChatMessage"]:hover {
    transform: perspective(1000px) rotateX(0deg) rotateY(-1.5deg) translateY(-4px) scale(1.01);
    box-shadow: 0 20px 45px rgba(6,182,212,.25);
}
@keyframes rise {
    from { opacity: 0; transform: perspective(1000px) rotateX(18deg) translateY(30px); }
    to   { opacity: 1; transform: perspective(1000px) rotateX(1.5deg) translateY(0); }
}

/* Chat input: solid background so messages don't show through behind it */
[data-testid="stBottom"], [data-testid="stBottom"] > div {
    background: linear-gradient(to top, #070714 65%, rgba(7,7,20,0)) !important;
}
[data-testid="stChatInput"] {
    background: #12122a;
    border: 1px solid rgba(124,58,237,.6);
    border-radius: 18px;
    box-shadow: 0 0 25px rgba(124,58,237,.25);
}

/* Buttons */
.stButton > button {
    background: linear-gradient(135deg, #7c3aed, #06b6d4);
    color: white; border: none; border-radius: 12px;
    padding: .55rem 1.2rem; font-weight: 600;
    transition: transform .25s ease, box-shadow .25s ease;
}
.stButton > button:hover {
    transform: perspective(500px) translateZ(14px) translateY(-2px);
    box-shadow: 0 12px 28px rgba(6,182,212,.45);
    color: white;
}

/* Metric cards (evaluation) */
[data-testid="stMetric"] {
    background: rgba(255,255,255,.06);
    border: 1px solid rgba(255,255,255,.12);
    border-radius: 14px;
    padding: .7rem 1rem;
}
[data-testid="stMetric"] * { color: #e8e8f5 !important; }

/* Stat cards */
.stat-grid { display: flex; gap: .8rem; margin: .6rem 0 1rem; }
.stat {
    flex: 1; text-align: center; padding: .8rem .4rem;
    background: rgba(255,255,255,.06);
    border: 1px solid rgba(255,255,255,.12);
    border-radius: 14px;
    transform: perspective(500px) rotateY(-8deg);
    transition: transform .3s ease;
}
.stat:hover { transform: perspective(500px) rotateY(0deg) scale(1.06); }
.stat b { display: block; font-size: 1.5rem;
    background: linear-gradient(135deg, #a78bfa, #22d3ee);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.stat span { font-size: .75rem; color: #b9b9d6 !important; opacity: 1; }

/* Source cards */
.src-card {
    background: rgba(255,255,255,.05);
    border-left: 3px solid #22d3ee;
    border-radius: 12px; padding: .7rem 1rem; margin-bottom: .7rem;
    transition: transform .3s ease, box-shadow .3s ease;
}
.src-card:hover {
    transform: perspective(700px) rotateX(3deg) translateZ(10px);
    box-shadow: 0 12px 28px rgba(0,0,0,.4);
}
.src-card p { margin: .4rem 0 0; font-size: .85rem; opacity: .85; }

/* ---- Thinking indicator ---- */
.thinking { display: flex; align-items: center; gap: .7rem; padding: .2rem 0; }
.think-orb {
    width: 12px; height: 12px; border-radius: 50%;
    background: linear-gradient(135deg, #7c3aed, #06b6d4);
    box-shadow: 0 0 14px rgba(6,182,212,.85);
    animation: orb 1.2s ease-in-out infinite;
}
@keyframes orb {
    0%, 100% { transform: scale(.7); opacity: .6; }
    50%      { transform: scale(1.3); opacity: 1; }
}
.think-text {
    font-weight: 600; letter-spacing: .5px;
    background: linear-gradient(90deg, #8b8bb5 0%, #ffffff 25%, #22d3ee 50%, #ffffff 75%, #8b8bb5 100%);
    background-size: 200% auto;
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent;
    animation: shimmer 2s linear infinite;
}
@keyframes shimmer { to { background-position: -200% center; } }
.dots i {
    display: inline-block; width: 6px; height: 6px; margin-left: 4px;
    border-radius: 50%; background: #22d3ee;
    animation: bounce 1.2s ease-in-out infinite;
}
.dots i:nth-child(2) { animation-delay: .15s; background: #a78bfa; }
.dots i:nth-child(3) { animation-delay: .3s;  background: #ec4899; }
@keyframes bounce {
    0%, 80%, 100% { transform: translateY(0);    opacity: .5; }
    40%           { transform: translateY(-7px); opacity: 1; }
}

/* ---- Agent route pill ---- */
.route-pill {
    display: inline-block; padding: 3px 12px; margin-bottom: .6rem;
    border-radius: 20px; font-size: .75rem; font-weight: 600;
    background: rgba(124,58,237,.25); border: 1px solid rgba(124,58,237,.6);
}
.src-card a { color: #22d3ee !important; text-decoration: underline; }

/* ---- Blinking cursor while streaming ---- */
.cursor {
    display: inline-block; width: 8px; height: 1.05em; margin-left: 2px;
    vertical-align: text-bottom; border-radius: 2px;
    background: linear-gradient(180deg, #7c3aed, #06b6d4);
    animation: blink 1s steps(2, start) infinite;
}
@keyframes blink { to { visibility: hidden; } }

.src-card mark {
    background: rgba(250, 204, 21, .35);
    color: #fff !important;
    padding: 1px 3px; border-radius: 4px;
}
.src-page {
    background: linear-gradient(135deg, #7c3aed, #06b6d4);
    padding: 2px 10px; border-radius: 20px; font-size: .72rem; font-weight: 700;
}

.empty-box {
    text-align: center; padding: 2.2rem 1rem; margin-top: 1rem;
    background: rgba(255,255,255,.05);
    border: 1px dashed rgba(255,255,255,.2);
    border-radius: 20px;
    transform: perspective(900px) rotateX(3deg);
}
.empty-box h3 { margin: 0 0 .4rem; }

/* ---- Toast: dark glass + light text ---- */
[data-testid="stToast"], [data-testid="stToastContainer"] > div {
    background: #12122a !important;
    border: 1px solid rgba(124,58,237,.6) !important;
    border-radius: 14px !important;
}
[data-testid="stToast"] *, [data-testid="stToastContainer"] * {
    color: #e8e8f5 !important;
}

/* ---- File uploader: Upload button + dropzone text ---- */
[data-testid="stFileUploader"] button,
[data-testid="stFileUploader"] button * {
    color: #111 !important;
    background-color: #fff;
}
[data-testid="stFileUploaderDropzoneInstructions"],
[data-testid="stFileUploaderDropzoneInstructions"] *,
[data-testid="stFileUploader"] section span,
[data-testid="stFileUploader"] section small {
    color: #c9c9e6 !important;
}
/* keep text dark inside the uploaded file name card (white box) */
[data-testid="stFileUploaderFile"] *,
[data-testid="stFileUploaderFileName"] { color: #111 !important; }

/* ---- Login / register forms ---- */
[data-testid="stForm"] { background: rgba(255,255,255,.05); border: 1px solid rgba(255,255,255,.12); border-radius: 18px; }
[data-testid="stFormSubmitButton"] > button { background: linear-gradient(135deg,#7c3aed,#06b6d4); border: none; border-radius: 12px; color: #fff !important; font-weight: 600; }
[data-testid="stTextInput"] input { color: #fff !important; -webkit-text-fill-color: #fff; background: rgba(255,255,255,.07) !important; }
'''


# ---------- API client ----------
class ApiError(Exception):
    pass


def _auth_headers():
    tok = st.session_state.get("token")
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def _detail(r):
    try:
        d = r.json().get("detail", r.text)
    except Exception:
        return r.text or f"HTTP {r.status_code}"
    if isinstance(d, list):  # FastAPI validation errors
        return "; ".join(str(x.get("msg", x)) for x in d)
    return str(d)


def _check(r):
    if r.status_code == 401 and st.session_state.get("token"):
        st.session_state.clear()  # token expired -> back to the login screen
        st.session_state.flash = [("error", "Session expired. Please log in again.")]
        st.rerun()
    if r.status_code >= 400:
        raise ApiError(_detail(r))


def api(method, path, **kw):
    kw.setdefault("timeout", 60)
    try:
        r = httpx.request(method, API_URL + path, headers=_auth_headers(), **kw)
    except httpx.HTTPError:
        raise ApiError(f"Can't reach the API at {API_URL}. Is it running?") from None
    _check(r)
    return r.json() if r.content else None


def stream_chat(question, doc_ids, history):
    """Yield NDJSON events (meta / token / done / error) from POST /chat."""
    payload = {"question": question, "doc_ids": doc_ids, "history": history}
    try:
        with httpx.stream(
            "POST", API_URL + "/chat", json=payload, headers=_auth_headers(),
            timeout=httpx.Timeout(30.0, read=300.0),
        ) as r:
            if r.status_code >= 400:
                r.read()
            _check(r)
            for line in r.iter_lines():
                if line:
                    yield json.loads(line)
    except httpx.HTTPError:
        raise ApiError("Lost connection to the API.") from None


# ---------- Helpers ----------
def fix_math(t: str) -> str:
    """Convert LaTeX delimiters so Streamlit renders formulas properly."""
    t = re.sub(r"\\\[(.+?)\\\]", r"$$\1$$", t, flags=re.S)
    t = re.sub(r"\\\((.+?)\\\)", r"$\1$", t, flags=re.S)
    t = re.sub(r"^\s*\[\s*(.*\\.*?)\s*\]\s*$", r"$$\1$$", t, flags=re.M)
    return t.replace("【", "[").replace("】", "]")


def highlight_html(text: str, spans) -> str:
    """Make the chunk text HTML-safe and highlight the relevant sentences with <mark>."""
    norm = lambda s: re.sub(r"\s+", " ", s).strip()
    out = html.escape(norm(text))
    for s in spans:
        es = html.escape(norm(s))
        if es and es in out:
            out = out.replace(es, f"<mark>{es}</mark>", 1)
    return out


def safe_url(url: str) -> str:
    """Only allow http(s) links (blocks javascript: URLs coming from web results)."""
    return url if url.lower().startswith(("http://", "https://")) else "#"


def show_flash():
    for kind, text in st.session_state.pop("flash", []):
        (st.error if kind == "error" else st.success)(text)


def render_sources(sources):
    with st.expander(f"📚 Sources ({len(sources)})"):
        for s in sources:
            st.markdown(
                f"<div class='src-card'><span class='src-page'>{html.escape(s['name'])} · {html.escape(s.get('loc') or '')}</span>"
                f"<p>{highlight_html(s['text'], s.get('highlight', []))}</p></div>",
                unsafe_allow_html=True,
            )


ROUTE_LABELS = {
    "qa": "📄 Document Q&A",
    "web": "🌐 Web search",
    "summary": "📝 Summary",
    "quiz": "🎯 Quiz",
    "flashcards": "🃏 Flashcards",
}


def route_pill_html(route):
    return f'<span class="route-pill">🧭 Agent → {ROUTE_LABELS.get(route, route)}</span>'


def render_quiz(quiz, idx):
    for i, qz in enumerate(quiz):
        st.markdown(f"**Q{i + 1}. {qz['question']}**")
        st.radio(
            "Answer", qz["options"], index=None,
            key=f"quiz_{idx}_{i}", label_visibility="collapsed",
        )
    if st.button("✅ Check answers", key=f"quizbtn_{idx}"):
        st.session_state[f"quizchk_{idx}"] = True
    if st.session_state.get(f"quizchk_{idx}"):
        score = 0
        for i, qz in enumerate(quiz):
            picked = st.session_state.get(f"quiz_{idx}_{i}")
            correct = qz["options"][qz["answer_index"]]
            ok = picked == correct
            score += int(ok)
            note = f" — {qz['explanation']}" if qz.get("explanation") else ""
            st.markdown(f"{'✅' if ok else '❌'} **Q{i + 1}:** {correct}{note}")
        st.markdown(f"### 🏆 Score: {score}/{len(quiz)}")


FLASHCARD_TEMPLATE = """
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false}]})"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&display=swap');
body { margin: 0; font-family: 'Space Grotesk', sans-serif; color: #e8e8f5; background: transparent; }
.bar { display: flex; justify-content: space-between; align-items: center; margin: 4px 2px 12px; font-size: 13px; color: #b9b9d6; }
.bar button {
  background: linear-gradient(135deg, #7c3aed, #06b6d4); color: #fff; border: none;
  border-radius: 10px; padding: 6px 14px; font-weight: 600; cursor: pointer; font-family: inherit;
}
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.card { height: 190px; perspective: 900px; cursor: pointer; transition: transform .25s ease; }
.card:hover { transform: translateY(-4px); }
.inner { position: relative; width: 100%; height: 100%; transition: transform .6s; transform-style: preserve-3d; }
.card.flip .inner { transform: rotateY(180deg); }
.face {
  position: absolute; inset: 0; border-radius: 16px; padding: 16px 18px; box-sizing: border-box;
  display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center;
  overflow: auto; border: 1px solid rgba(255,255,255,.18);
  -webkit-backface-visibility: hidden; backface-visibility: hidden;
  box-shadow: 0 10px 26px rgba(0,0,0,.35);
}
.front { background: linear-gradient(135deg, rgba(124,58,237,.40), rgba(6,182,212,.25)); }
.back  { background: linear-gradient(135deg, rgba(6,182,212,.38), rgba(236,72,153,.25)); transform: rotateY(180deg); }
.tag { font-size: 10px; letter-spacing: 2px; opacity: .7; margin-bottom: 8px; }
.txt { font-size: 15px; line-height: 1.45; }
.num { position: absolute; top: 10px; left: 14px; font-size: 11px; opacity: .6; }
.katex { color: #fff; }
</style>
<div class="bar">
  <span>👆 Click a card to flip it</span>
  <button onclick="document.querySelectorAll('.card').forEach(c => c.classList.toggle('flip'))">Flip all</button>
</div>
<div class="grid">__CARDS__</div>
"""


def render_flashcards(cards, idx):
    items = ""
    for n, c in enumerate(cards, 1):
        front = html.escape(fix_math(c["front"]))
        back = html.escape(fix_math(c["back"]))
        items += (
            "<div class='card' onclick=\"this.classList.toggle('flip')\"><div class='inner'>"
            f"<div class='face front'><span class='num'>{n}</span><div class='tag'>QUESTION</div><div class='txt'>{front}</div></div>"
            f"<div class='face back'><span class='num'>{n}</span><div class='tag'>ANSWER</div><div class='txt'>{back}</div></div>"
            "</div></div>"
        )
    rows = (len(cards) + 1) // 2
    components.html(
        FLASHCARD_TEMPLATE.replace("__CARDS__", items),
        height=rows * 204 + 70,
        scrolling=True,
    )


def render_web_sources(web):
    with st.expander(f"🌐 Web sources ({len(web)})"):
        for i, w in enumerate(web, 1):
            domain = re.sub(r"^https?://(www\.)?", "", w["url"]).split("/")[0]
            st.markdown(
                f"<div class='src-card'><span class='src-page'>[{i}] {html.escape(domain)}</span>"
                f"<p><a href='{html.escape(safe_url(w['url']))}' target='_blank'>{html.escape(w['title'] or w['url'])}</a>"
                f"<br>{html.escape(w['content'][:300])}</p></div>",
                unsafe_allow_html=True,
            )


def render_trace(trace):
    with st.expander("🧭 Agent steps"):
        for step in trace:
            st.markdown(f"- {step}")


def fmt_score(v):
    return "n/a" if v is None else f"{v:.2f}"


def render_eval_result(res):
    c1, c2, c3 = st.columns(3)
    c1.metric("Faithfulness", fmt_score(res.get("faithfulness")))
    c2.metric("Answer relevancy", fmt_score(res.get("answer_relevancy")))
    c3.metric("Context precision", fmt_score(res.get("context_precision")))
    if res.get("claims"):
        with st.expander(f"🔍 Claim check ({len(res['claims'])} claims)"):
            for c in res["claims"]:
                st.markdown(f"{'✅' if c['supported'] else '❌'} {c['claim']}")


def render_eval_block(m, idx):
    """Evaluate button + scores for a document-Q&A answer."""
    if m.get("eval"):
        render_eval_result(m["eval"])
    elif st.button("📊 Evaluate answer", key=f"evalbtn_{idx}"):
        with st.spinner("Judging faithfulness, relevancy and context precision..."):
            try:
                res = api(
                    "POST", "/evaluate",
                    json={
                        "question": m.get("question", ""),
                        "answer": m["content"],
                        "contexts": m.get("contexts", []),
                    },
                    timeout=180,
                )
            except ApiError as e:
                st.error(str(e))
                return
        m["eval"] = res
        render_eval_result(res)


def render_extras(m, idx):
    """Quiz / flashcards / sources / web sources / agent trace (used for live + history)."""
    if m.get("route") == "quiz" and m.get("data"):
        render_quiz(m["data"], idx)
    elif m.get("route") == "flashcards" and m.get("data"):
        render_flashcards(m["data"], idx)
    if m.get("sources"):
        render_sources(m["sources"])
    if m.get("route") == "qa" and m.get("contexts"):
        render_eval_block(m, idx)
    if m.get("web"):
        render_web_sources(m["web"])
    if m.get("trace"):
        render_trace(m["trace"])


def reset_chat():
    st.session_state.messages = []
    stale = [
        k for k in st.session_state
        if str(k).startswith(("quiz_", "quizbtn_", "quizchk_", "evalbtn_"))
    ]
    for k in stale:
        del st.session_state[k]


def render_eval_dashboard(selected_ids):
    # No pandas here: its compiled files are blocked by this machine's Application Control policy.
    st.markdown("## 📊 RAG Evaluation Dashboard")
    st.caption(
        "RAGAS-style metrics (0–1) scored by an LLM judge. The judge is the same model that "
        "generates the answers, so treat scores as relative, not absolute."
    )
    metrics = ["faithfulness", "answer_relevancy", "context_precision"]
    labels = {
        "faithfulness": "Faithfulness",
        "answer_relevancy": "Answer relevancy",
        "context_precision": "Context precision",
    }
    colors = {"faithfulness": "#a78bfa", "answer_relevancy": "#22d3ee", "context_precision": "#ec4899"}

    try:
        log = api("GET", "/evaluations")
    except ApiError as e:
        st.error(str(e))
        return

    if not log:
        st.info("No evaluations yet. Click “📊 Evaluate answer” under a chat answer, or run a test set below.")
    else:
        def avg(key):
            vals = [r[key] for r in log if r.get(key) is not None]
            return f"{sum(vals) / len(vals):.2f}" if vals else "n/a"

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Faithfulness (avg)", avg("faithfulness"))
        c2.metric("Answer relevancy (avg)", avg("answer_relevancy"))
        c3.metric("Context precision (avg)", avg("context_precision"))
        c4.metric("Evaluated answers", len(log))

        # ---- Trend (inline SVG) ----
        st.markdown("#### Trend")
        W, H, PAD = 640, 220, 28
        n = len(log)

        def xy(i, v):
            x = PAD + (W - 2 * PAD) * (i / (n - 1) if n > 1 else 0.5)
            y = H - PAD - (H - 2 * PAD) * v
            return x, y

        svg = [f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg">']
        for g in (0, 0.25, 0.5, 0.75, 1):
            _, gy = xy(0, g)
            svg.append(f'<line x1="{PAD}" y1="{gy}" x2="{W - PAD}" y2="{gy}" stroke="rgba(255,255,255,.12)"/>')
            svg.append(f'<text x="2" y="{gy + 4}" fill="#b9b9d6" font-size="10">{g:.2f}</text>')
        for m in metrics:
            pts = [(i, r[m]) for i, r in enumerate(log) if r.get(m) is not None]
            if not pts:
                continue
            coords = [xy(i, max(0.0, min(1.0, v))) for i, v in pts]
            if len(coords) > 1:
                line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
                svg.append(f'<polyline fill="none" stroke="{colors[m]}" stroke-width="2.5" points="{line}"/>')
            for x, y in coords:
                svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{colors[m]}"/>')
        svg.append("</svg>")
        legend = " &nbsp; ".join(
            f'<span style="color:{colors[m]}">●</span> {labels[m]}' for m in metrics
        )
        st.markdown(
            "".join(svg) + f"<div style='font-size:.8rem;margin-top:4px'>{legend}</div>",
            unsafe_allow_html=True,
        )

        # ---- History (markdown table) ----
        st.markdown("#### History")
        fmt = lambda v: "n/a" if v is None else f"{v:.2f}"
        clean = lambda s: html.escape(str(s or "")).replace("|", "/").replace("\n", " ")[:90]
        rows = ["| Time | Source | Question | Faith. | Relev. | Ctx prec. |", "|---|---|---|---|---|---|"]
        for r in reversed(log[-50:]):
            rows.append(
                f"| {r['time']} | {clean(r.get('source'))} | {clean(r.get('question'))} | "
                f"{fmt(r.get('faithfulness'))} | {fmt(r.get('answer_relevancy'))} | {fmt(r.get('context_precision'))} |"
            )
        st.markdown("\n".join(rows))
        if len(log) > 50:
            st.caption(f"Showing the latest 50 of {len(log)} evaluations.")

        if st.button("🗑️ Clear evaluation log"):
            try:
                api("DELETE", "/evaluations")
            except ApiError as e:
                st.error(str(e))
            else:
                st.rerun()

    st.markdown("### 🧪 Run a test set")
    st.caption("One question per line. Each question runs through the full RAG pipeline and is evaluated.")
    qs_text = st.text_area(
        "Test questions",
        height=140,
        label_visibility="collapsed",
        placeholder="What is the sigmoid function?\nWhich cost function does logistic regression use?\nWhen is softmax used?",
    )
    if st.button("▶️ Run evaluation", disabled=not selected_ids):
        qs = [l.strip() for l in qs_text.splitlines() if l.strip()]
        if not qs:
            st.warning("Add at least one question.")
        else:
            bar = st.progress(0.0)
            failed = False
            for i, q in enumerate(qs):
                bar.progress(i / len(qs), text=f"Evaluating {i + 1}/{len(qs)}: {q[:60]}")
                try:
                    api("POST", "/evaluations/run",
                        json={"question": q, "doc_ids": selected_ids}, timeout=300)
                except ApiError as e:
                    st.error(f"“{q[:40]}”: {e}")
                    failed = True
                    break
            if not failed:
                bar.progress(1.0, text="Done")
                st.rerun()
    if not selected_ids:
        st.caption("Upload and select a document in the sidebar to run a test set.")


THINKING_HTML = (
    '<div class="thinking"><span class="think-orb"></span>'
    '<span class="think-text">Thinking</span>'
    '<span class="dots"><i></i><i></i><i></i></span></div>'
)
CURSOR_HTML = ' <span class="cursor"></span>'


# ---------- Auth screen ----------
def _do_auth(kind, username, password):
    try:
        data = api("POST", f"/auth/{kind}", json={"username": username.strip(), "password": password})
    except ApiError as e:
        st.error(str(e))
        return
    st.session_state.token = data["token"]
    st.session_state.username = data["username"]
    st.session_state.engine = data["engine"]
    st.session_state.web_mode = data["web_mode"]
    st.rerun()


def render_auth():
    show_flash()
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        tab_in, tab_up = st.tabs(["Log in", "Create account"])
        with tab_in:
            with st.form("login_form"):
                u = st.text_input("Username")
                p = st.text_input("Password", type="password")
                go = st.form_submit_button("Log in")
            if go:
                _do_auth("login", u, p)
        with tab_up:
            with st.form("register_form"):
                u2 = st.text_input("Username", help="3–32 chars: letters, numbers, . _ -")
                p2 = st.text_input("Password", type="password", help="At least 8 characters")
                p3 = st.text_input("Confirm password", type="password")
                go2 = st.form_submit_button("Create account")
            if go2:
                if p2 != p3:
                    st.error("Passwords don't match.")
                else:
                    _do_auth("register", u2, p2)


# ---------- Styling ----------
st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)

# ---------- Hero ----------
st.markdown(
    """
    <div class="hero">
      <h1>DocMind 3D</h1>
      <p>Upload documents or paste a link. Ask by text or voice. Get grounded answers.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------- Login gate ----------
if "token" not in st.session_state:
    render_auth()
    st.stop()

st.session_state.setdefault("messages", [])
st.session_state.setdefault("up_key", 0)

# ---------- Sidebar ----------
KIND_ICONS = {"pdf": "📄", "docx": "📝", "pptx": "📊", "txt": "📃", "md": "📃", "web": "🌐", "youtube": "▶️"}
VOICE_LANGS = {
    "Auto-detect": "", "English": "en", "Hindi": "hi", "Marathi": "mr",
    "Bengali": "bn", "Tamil": "ta", "Telugu": "te", "Gujarati": "gu",
}
voice_q = None  # text transcribed from the microphone (if any) in this run

with st.sidebar:
    st.markdown("## 🧊 DocMind 3D")
    st.caption("RAG-powered document assistant")
    st.caption(
        f"🧭 Agent: {st.session_state.get('engine', '?')} · 🌐 Web: {st.session_state.get('web_mode', '?')}"
    )
    c1, c2 = st.columns([3, 2])
    c1.caption(f"👤 {st.session_state.username}")
    if c2.button("Log out"):
        st.session_state.clear()
        st.rerun()

    show_flash()
    view = st.radio("View", ["💬 Chat", "📊 Evaluation"], horizontal=True)

    # The uploader key changes after every upload, which clears the widget
    files = st.file_uploader(
        "Upload documents", type=["pdf", "docx", "pptx", "txt", "md"], accept_multiple_files=True,
        key=f"up_{st.session_state.up_key}",
    )
    if files:
        flash = []
        for f in files:
            with st.spinner(f"Indexing {f.name}..."):
                try:
                    api("POST", "/documents",
                        files={"file": (f.name, f.getvalue(), f.type or "application/octet-stream")},
                        timeout=600)
                    flash.append(("success", f"{f.name} indexed!"))
                except ApiError as e:
                    flash.append(("error", f"{f.name}: {e}"))
        st.session_state.flash = flash
        st.session_state.up_key += 1
        st.rerun()

    with st.form("link_form", clear_on_submit=True):
        link = st.text_input("Web page or YouTube link", placeholder="https://...")
        add_link = st.form_submit_button("➕ Add link")
    if add_link and link.strip():
        with st.spinner("Fetching and indexing..."):
            try:
                res = api("POST", "/documents/url", json={"url": link.strip()}, timeout=300)
                st.session_state.flash = [("success", f"{res['name']} indexed!")]
            except ApiError as e:
                st.session_state.flash = [("error", str(e))]
        st.rerun()

    with st.expander("🎤 Ask by voice"):
        if not hasattr(st, "audio_input"):
            st.caption("Update Streamlit to use this: python -m pip install -U streamlit")
        else:
            lang_label = st.selectbox("Spoken language", list(VOICE_LANGS), key="voice_lang")
            audio = st.audio_input("Record your question", key="voice_rec")
            if audio is not None:
                data = audio.getvalue()
                h = hashlib.md5(data).hexdigest()
                if h != st.session_state.get("last_audio"):  # the widget returns the same clip on every rerun
                    st.session_state.last_audio = h
                    with st.spinner("Transcribing..."):
                        try:
                            res = api(
                                "POST", "/transcribe",
                                files={"file": ("audio.wav", data, "audio/wav")},
                                data={"language": VOICE_LANGS[lang_label]},
                                timeout=120,
                            )
                            text = ((res or {}).get("text") or "").strip()
                        except ApiError as e:
                            st.error(str(e))
                            text = ""
                    if text:
                        voice_q = text
                    else:
                        st.warning("Couldn't hear anything. Please try again.")

    try:
        docs = api("GET", "/documents")
    except ApiError as e:
        st.error(str(e))
        docs = []

    by_id = {d["doc_id"]: d for d in docs}
    selected_ids = list(by_id)
    if len(docs) > 1:
        selected_ids = st.multiselect(
            "Search in", list(by_id), default=list(by_id),
            format_func=lambda d: by_id[d]["name"],
        )

    if docs:
        total_chunks = sum(d["chunks"] for d in docs)
        st.markdown(
            f"""
            <div class="stat-grid">
              <div class="stat"><b>{len(docs)}</b><span>Docs</span></div>
              <div class="stat"><b>{total_chunks}</b><span>Chunks</span></div>
              <div class="stat"><b>{len(st.session_state.messages)//2}</b><span>Questions</span></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        for d in docs:
            n1, n2 = st.columns([5, 1])
            n1.caption(f"{KIND_ICONS.get(d.get('kind', 'pdf'), '📄')} {d['name']}")
            if n2.button("🗑", key=f"del_{d['doc_id']}", help="Delete this document"):
                try:
                    api("DELETE", f"/documents/{d['doc_id']}")
                except ApiError as e:
                    st.error(str(e))
                else:
                    st.rerun()

    if st.button("🗑️ Clear chat"):
        reset_chat()
        st.rerun()

# ---------- Evaluation view ----------
if view == "📊 Evaluation":
    render_eval_dashboard(selected_ids)
    st.stop()

# ---------- Main ----------
ready = len(selected_ids) > 0

if not ready:
    st.markdown(
        """
        <div class="empty-box">
          <h3>📄 No document selected</h3>
          <p>Upload PDF, DOCX, PPTX or TXT files, or paste a web page / YouTube link in the sidebar.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

for i, m in enumerate(st.session_state.messages):
    with st.chat_message(m["role"], avatar="🧑‍💻" if m["role"] == "user" else "🧊"):
        if m.get("route"):
            st.markdown(route_pill_html(m["route"]), unsafe_allow_html=True)
        st.markdown(m["content"])
        render_extras(m, i)

typed = st.chat_input(
    "Ask anything, or try: “summarize”, “quiz me”, “make flashcards”..."
    if ready else "Upload a document first",
    disabled=not ready,
)
q = typed or (voice_q if ready else None)  # a voice question is used like a typed one
if voice_q and not ready:
    st.warning("Upload a document first, then ask by voice.")

if q:
    # Previous conversation (last 6 messages) so follow-up questions make sense
    history = [
        {"role": m["role"], "content": m["content"][:2000]}
        for m in st.session_state.messages[-6:]
    ]
    st.session_state.messages.append({"role": "user", "content": q})
    assistant_idx = len(st.session_state.messages)  # index of this message (used for widget keys)
    with st.chat_message("user", avatar="🧑‍💻"):
        st.markdown(q)

    with st.chat_message("assistant", avatar="🧊"):
        pill_slot = st.empty()
        placeholder = st.empty()
        placeholder.markdown(THINKING_HTML, unsafe_allow_html=True)
        msg = {"role": "assistant", "content": ""}
        raw, last, final = "", 0.0, None
        try:
            for ev in stream_chat(q, selected_ids, history):
                kind = ev.get("type")
                if kind == "meta":
                    msg["route"] = ev["route"]
                    msg["trace"] = ev.get("trace", [])
                    pill_slot.markdown(route_pill_html(ev["route"]), unsafe_allow_html=True)
                elif kind == "token":
                    raw += ev["text"]
                    # don't re-render on every token, cap at ~20 updates/sec
                    if time.monotonic() - last > 0.05:
                        placeholder.markdown(fix_math(raw) + CURSOR_HTML, unsafe_allow_html=True)
                        last = time.monotonic()
                elif kind == "done":
                    final = ev
                elif kind == "error":
                    raise ApiError(ev.get("message", "Unknown error"))

            if final is None:
                ans = fix_math(raw) if raw else "⚠️ No response from the API."
            else:
                ans = fix_math(final.get("content") or raw) or "⚠️ The model returned an empty response."
                for key in ("data", "sources", "web", "question", "contexts"):
                    if final.get(key) is not None:
                        msg[key] = final[key]
        except ApiError as e:
            ans = f"⚠️ {e}"
        placeholder.markdown(ans)
        msg["content"] = ans
        render_extras(msg, assistant_idx)

    st.session_state.messages.append(msg)