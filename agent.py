# """DocMind agent layer.

# Flow:  question -> router -> (qa | web | summary | quiz | flashcards) -> plan

# Graph sirf "plan" banata hai (route, retrieved chunks, final prompt, trace).
# Asli generation app.py mein hoti hai taaki streaming chalti rahe.

# LangGraph install hai to StateGraph use hota hai, warna same nodes ek simple
# fallback runner se chalte hain (app crash nahi hogi).
# """
# import os
# import re
# import json
# import random
# from typing import TypedDict

# # rag import pehle: wo load_dotenv() chalata hai, tabhi TAVILY_API_KEY milega
# from rag import (
#     client,
#     MODEL,
#     _get_db,
#     _prepare,
#     _build_prompt,
#     hybrid_retrieve,
#     rewrite_question,
# )

# TAVILY_KEY = os.getenv("TAVILY_API_KEY")
# WEB_MODE = "API key" if TAVILY_KEY else "keyless"  # keyless: free, rate-limited
# MAX_CONTEXT_CHARS = 10000  # summary/quiz/flashcards ke liye context budget


# # ---------------------------------------------------------------- state
# class AgentState(TypedDict, total=False):
#     question: str
#     history: list
#     doc_ids: list
#     doc_names: list
#     route: str
#     docs: list
#     web: list
#     prompt: str
#     n_items: int
#     notice: str
#     trace: list


# # ---------------------------------------------------------------- LLM helpers
# def _llm(prompt, max_tokens=800, temperature=0.0, low_effort=False):
#     kwargs = dict(
#         model=MODEL,
#         messages=[{"role": "user", "content": prompt}],
#         max_completion_tokens=max_tokens,
#         temperature=temperature,
#     )
#     if low_effort:  # reasoning model ko kam sochne do (supported na ho to normal call)
#         try:
#             r = client.chat.completions.create(**kwargs, reasoning_effort="low")
#             return (r.choices[0].message.content or "").strip()
#         except Exception:
#             pass
#     r = client.chat.completions.create(**kwargs)
#     return (r.choices[0].message.content or "").strip()


# def stream_text(prompt, max_tokens=2000):
#     """Token-by-token generator (st.empty ke saath use hota hai)."""
#     stream = client.chat.completions.create(
#         model=MODEL,
#         messages=[{"role": "user", "content": prompt}],
#         max_completion_tokens=max_tokens,
#         temperature=0.2,
#         stream=True,
#     )
#     for chunk in stream:
#         if not chunk.choices:
#             continue
#         delta = chunk.choices[0].delta
#         if delta and delta.content:
#             yield delta.content


# # ---------------------------------------------------------------- JSON parsing (quiz / flashcards)
# def _parse_json(text):
#     t = re.sub(r"```(?:json)?", "", text or "").strip()
#     data = None
#     for open_c, close_c in (("[", "]"), ("{", "}")):
#         s, e = t.find(open_c), t.rfind(close_c)
#         if s != -1 and e > s:
#             try:
#                 data = json.loads(t[s : e + 1])
#                 break
#             except Exception:
#                 continue
#     if isinstance(data, dict):
#         for v in data.values():
#             if isinstance(v, list):
#                 return v
#         return None
#     return data


# def _norm_quiz(data):
#     out = []
#     for x in data if isinstance(data, list) else []:
#         try:
#             opts = [str(o).strip() for o in x["options"]]
#             ai = int(x["answer_index"])
#             if len(opts) < 2 or not (0 <= ai < len(opts)):
#                 continue
#             correct = opts[ai]
#             random.shuffle(opts)  # LLM aksar correct option pehle rakhta hai
#             out.append(
#                 {
#                     "question": str(x["question"]).strip(),
#                     "options": opts,
#                     "answer_index": opts.index(correct),
#                     "explanation": str(x.get("explanation", "")).strip(),
#                 }
#             )
#         except Exception:
#             continue
#     return out


# def _norm_cards(data):
#     out = []
#     for x in data if isinstance(data, list) else []:
#         try:
#             front, back = str(x["front"]).strip(), str(x["back"]).strip()
#             if front and back:
#                 out.append({"front": front, "back": back})
#         except Exception:
#             continue
#     return out


# def generate_items(prompt, kind):
#     """Quiz/flashcards JSON generate karo. Parse fail ho to ek baar retry."""
#     norm = _norm_quiz if kind == "quiz" else _norm_cards
#     text = _llm(prompt, max_tokens=2500, temperature=0.3)
#     items = norm(_parse_json(text))
#     if not items:
#         text = _llm(
#             prompt + "\n\nIMPORTANT: reply with valid JSON only, nothing else.",
#             max_tokens=2500,
#             temperature=0.2,
#         )
#         items = norm(_parse_json(text))
#     return items, text


# # ---------------------------------------------------------------- context helpers
# def _fmt(d):
#     return (
#         f"[File: {d.metadata.get('source_name', 'unknown')} | "
#         f"Page {d.metadata.get('page', 0) + 1}]\n{d.page_content}"
#     )


# def _sample_context(doc_ids, budget=MAX_CONTEXT_CHARS):
#     """Poore doc(s) se evenly-spaced chunks lo taaki budget mein fit ho."""
#     data = _get_db().get(where={"doc_id": {"$in": list(doc_ids)}})
#     by_doc = {}
#     for text, meta in zip(data["documents"], data["metadatas"]):
#         by_doc.setdefault(meta.get("doc_id"), []).append((meta.get("page", 0), text, meta))
#     if not by_doc:
#         return "", 0, 0

#     per_doc = budget // len(by_doc)
#     parts, n_chunks = [], 0
#     for items in by_doc.values():
#         items.sort(key=lambda x: x[0])  # page order (stable)
#         total = sum(len(t) for _, t, _ in items)
#         if total > per_doc:
#             avg = max(1, total // len(items))
#             k = max(1, per_doc // avg)
#             step = len(items) / k
#             items = [items[int(i * step)] for i in range(k)]
#         for page, text, meta in items:
#             parts.append(f"[File: {meta.get('source_name', 'unknown')} | Page {page + 1}]\n{text}")
#             n_chunks += 1
#     return "\n\n".join(parts), n_chunks, len(by_doc)


# _STOP = set(
#     """make create generate give me a an the of on about for this that these those document doc docs
#     pdf pdfs paper notes file quiz mcq mcqs flashcard flashcards flash card cards summary summarize
#     summarise summarization questions question please banao bana do de dena kar karo ka ki ke ko
#     isko iska iski is pe par mein me se aur and or to some few short brief detailed full complete
#     from in my our ye yeh wo woh test practice mock multiple choice revision tl dr saar saransh
#     """.split()
# )


# def _topic(question):
#     """Request mein se filler hata ke topic nikalo ('' matlab poora document)."""
#     toks = [t for t in re.findall(r"\w+", question.lower()) if t not in _STOP and not t.isdigit()]
#     return " ".join(toks) if len(toks) >= 2 else ""


# def _count(question, default):
#     m = re.search(r"\b(\d{1,2})\b", question)
#     return max(3, min(int(m.group(1)), 15)) if m else default


# def _task_context(state):
#     topic = _topic(state["question"])
#     if topic:
#         docs = hybrid_retrieve(topic, state["doc_ids"], k=8)
#         if docs:
#             return "\n\n".join(_fmt(d) for d in docs), f"topic “{topic}” → {len(docs)} relevant chunks"
#     ctx, n_chunks, n_docs = _sample_context(state["doc_ids"])
#     return ctx, f"sampled {n_chunks} chunks across {n_docs} doc(s)"


# # ---------------------------------------------------------------- web search (Tavily)
# def web_search(query, n=5):
#     import httpx  # groq ke saath already installed

#     headers = {"Content-Type": "application/json"}
#     if TAVILY_KEY:
#         headers["Authorization"] = f"Bearer {TAVILY_KEY}"
#     else:
#         headers["X-Tavily-Access-Mode"] = "keyless"
#     r = httpx.post(
#         "https://api.tavily.com/search",
#         headers=headers,
#         json={"query": query, "max_results": n, "search_depth": "basic"},
#         timeout=20,
#     )
#     r.raise_for_status()
#     return [
#         {"title": x.get("title", ""), "url": x.get("url", ""), "content": x.get("content", "")}
#         for x in r.json().get("results", [])
#     ]


# # ---------------------------------------------------------------- router
# FLASH_RE = re.compile(r"flash\s?-?cards?|revision cards?")
# QUIZ_RE = re.compile(r"\bquiz\b|\bmcqs?\b|multiple choice|test me|practice questions|mock test")
# SUMMARY_RE = re.compile(r"summar|\btl;?dr\b|saransh|\bsaar\b")
# WEB_EXPLICIT_RE = re.compile(
#     r"search (the )?(web|internet|online)|web search|google (it|this|karo)|internet (pe|par|se)|online (search|dhundh)"
# )
# WEB_HINT_RE = re.compile(
#     r"\b(latest|news|today|current|currently|recent|recently|newest|updates?|price|weather|released?|202\d)\b|who is the|kaun hai"
# )


# def _llm_route(question, doc_names):
#     prompt = f"""You route questions for a PDF study assistant.
# Uploaded documents: {', '.join(doc_names) or 'unknown'}
# Choose ONE route:
# - qa  : the answer is likely inside the uploaded documents
# - web : needs fresh or outside information (recent news, latest versions, prices, current events, facts clearly unrelated to the documents)
# Reply with exactly one word: qa or web.

# Message: {question}"""
#     try:
#         out = _llm(prompt, max_tokens=400, low_effort=True).lower()
#     except Exception:
#         return "qa"
#     words = re.findall(r"[a-z]+", out)
#     return "web" if words and words[0] == "web" else "qa"


# def router_node(state):
#     q = state["question"]
#     ql = q.lower()
#     if FLASH_RE.search(ql):
#         route, how = "flashcards", "keyword"
#     elif QUIZ_RE.search(ql):
#         route, how = "quiz", "keyword"
#     elif SUMMARY_RE.search(ql):
#         route, how = "summary", "keyword"
#     elif WEB_EXPLICIT_RE.search(ql):
#         route, how = "web", "keyword"
#     elif WEB_HINT_RE.search(ql):
#         route, how = _llm_route(q, state.get("doc_names", [])), "LLM"
#     else:
#         route, how = "qa", "default"
#     return {"route": route, "trace": state.get("trace", []) + [f"Router → {route} ({how})"]}


# # ---------------------------------------------------------------- nodes
# def qa_node(state):
#     trace = list(state.get("trace", []))
#     standalone, docs = _prepare(state["question"], state["doc_ids"], state.get("history"), 6)
#     if standalone.strip() != state["question"].strip():
#         trace.append(f"Follow-up rewritten → “{standalone}”")
#     if not docs:
#         return {"notice": "I couldn't find this in the selected documents.", "trace": trace}
#     trace.append(f"Hybrid search (vector + BM25 + rerank) → {len(docs)} chunks")
#     return {"docs": docs, "prompt": _build_prompt(standalone, docs), "trace": trace}


# def web_node(state):
#     trace = list(state.get("trace", []))
#     q = rewrite_question(state["question"], state.get("history"))
#     try:
#         results = web_search(q)
#     except Exception as e:
#         return {"notice": f"⚠️ Web search fail hua: {e}", "trace": trace + ["Web search failed"]}
#     if not results:
#         return {"notice": "Web pe koi result nahi mila.", "trace": trace + ["Web search → 0 results"]}
#     trace.append(f"Tavily web search ({WEB_MODE}) → {len(results)} results")
#     blocks = "\n\n".join(
#         f"[{i}] {r['title']} ({r['url']})\n{r['content']}" for i, r in enumerate(results, 1)
#     )
#     prompt = f"""Answer the question using ONLY the web results below.
# Cite sources inline like [1], [2]. If the results are not enough, say so.

# Web results:
# {blocks}

# Question: {q}"""
#     return {"web": results, "prompt": prompt, "trace": trace}


# def summary_node(state):
#     ctx, note = _task_context(state)
#     if not ctx:
#         return {"notice": "Selected documents mein text nahi mila.", "trace": state.get("trace", [])}
#     prompt = f"""You are a study assistant. Using ONLY the excerpts below (they may be sampled, so some parts can be missing),
# write a clear, well-structured summary in markdown:
# - Start with a 2-line TL;DR
# - Then "Key points" as short bullets
# - Then "Important terms / formulas" if any (write formulas in LaTeX with $...$)
# If there are multiple files, summarize each file under its own heading.
# Mention page numbers like (p. 3) where helpful.

# Excerpts:
# {ctx}

# User request: {state['question']}"""
#     return {"prompt": prompt, "trace": state.get("trace", []) + [f"Summary context: {note}"]}


# def quiz_node(state):
#     ctx, note = _task_context(state)
#     if not ctx:
#         return {"notice": "Selected documents mein text nahi mila.", "trace": state.get("trace", [])}
#     n = _count(state["question"], 5)
#     prompt = f"""You are a quiz generator for students. Using ONLY the excerpts below, write {n} multiple-choice questions.
# Rules:
# - Each question has exactly 4 options (plain text, no "A)" / "B)" prefixes) and exactly one correct option.
# - Mix easy and medium difficulty; test understanding, not trivia.
# - Add a one-sentence explanation for the correct answer.
# Return ONLY valid JSON (no markdown fences, no commentary) in exactly this shape:
# [{{"question": "...", "options": ["...", "...", "...", "..."], "answer_index": 0, "explanation": "..."}}]

# Excerpts:
# {ctx}

# User request: {state['question']}"""
#     return {
#         "prompt": prompt,
#         "n_items": n,
#         "trace": state.get("trace", []) + [f"Quiz context: {note}", f"Generating {n} MCQs"],
#     }


# def flashcards_node(state):
#     ctx, note = _task_context(state)
#     if not ctx:
#         return {"notice": "Selected documents mein text nahi mila.", "trace": state.get("trace", [])}
#     n = _count(state["question"], 8)
#     prompt = f"""You are a flashcard generator for students. Using ONLY the excerpts below, write {n} flashcards.
# Rules:
# - "front" is a short question or term; "back" is a concise answer (1-3 sentences). Formulas in LaTeX with $...$.
# - Cover the most important concepts, definitions and formulas.
# Return ONLY valid JSON (no markdown fences, no commentary) in exactly this shape:
# [{{"front": "...", "back": "..."}}]

# Excerpts:
# {ctx}

# User request: {state['question']}"""
#     return {
#         "prompt": prompt,
#         "n_items": n,
#         "trace": state.get("trace", []) + [f"Flashcards context: {note}", f"Generating {n} cards"],
#     }


# NODES = {
#     "qa": qa_node,
#     "web": web_node,
#     "summary": summary_node,
#     "quiz": quiz_node,
#     "flashcards": flashcards_node,
# }


# # ---------------------------------------------------------------- graph
# def _build_graph():
#     from langgraph.graph import StateGraph, START, END

#     g = StateGraph(AgentState)
#     g.add_node("router", router_node)
#     for name, fn in NODES.items():
#         g.add_node(f"node_{name}", fn)  # prefix: node name state key se na takraye
#     g.add_edge(START, "router")
#     g.add_conditional_edges(
#         "router", lambda s: s["route"], {name: f"node_{name}" for name in NODES}
#     )
#     for name in NODES:
#         g.add_edge(f"node_{name}", END)
#     return g.compile()


# try:
#     _GRAPH = _build_graph()
#     ENGINE = "LangGraph"
# except Exception:  # langgraph install/load nahi hua (e.g. python -m pip install langgraph)
#     _GRAPH = None
#     ENGINE = "fallback"


# def run_agent(question, history, doc_ids, doc_names=None):
#     state = {
#         "question": question,
#         "history": history or [],
#         "doc_ids": list(doc_ids),
#         "doc_names": doc_names or [],
#         "trace": [f"Engine: {ENGINE}"],
#     }
#     if _GRAPH is not None:
#         return _GRAPH.invoke(state)
#     state.update(router_node(state))
#     state.update(NODES[state["route"]](state))
#     return state

"""DocMind agent layer.

Flow:  question -> router -> (qa | web | summary | quiz | flashcards) -> plan

The graph only builds a "plan" (route, retrieved chunks, final prompt, trace).
The actual generation happens in api.py so that streaming keeps working.
Every retrieval is scoped to a user_id, so users never see each other's documents.

If LangGraph is installed, a StateGraph is used; otherwise the same nodes run
through a simple fallback runner.
"""
import os
import re
import json
import random
from typing import TypedDict

# import rag first: it calls load_dotenv(), which is needed to pick up TAVILY_API_KEY
from rag import (
    client,
    MODEL,
    _get_db,
    _prepare,
    _build_prompt,
    hybrid_retrieve,
    rewrite_question,
)

TAVILY_KEY = os.getenv("TAVILY_API_KEY")
WEB_MODE = "API key" if TAVILY_KEY else "keyless"  # keyless: free, rate-limited
MAX_CONTEXT_CHARS = 10000  # context budget for summary / quiz / flashcards


# ---------------------------------------------------------------- state
class AgentState(TypedDict, total=False):
    question: str
    standalone: str
    history: list
    user_id: int
    doc_ids: list
    doc_names: list
    route: str
    docs: list
    web: list
    prompt: str
    n_items: int
    notice: str
    trace: list


# ---------------------------------------------------------------- LLM helpers
def _llm(prompt, max_tokens=800, temperature=0.0, low_effort=False):
    kwargs = dict(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=max_tokens,
        temperature=temperature,
    )
    if low_effort:  # let the reasoning model think less (falls back to a normal call if unsupported)
        try:
            r = client.chat.completions.create(**kwargs, reasoning_effort="low")
            return (r.choices[0].message.content or "").strip()
        except Exception:
            pass
    r = client.chat.completions.create(**kwargs)
    return (r.choices[0].message.content or "").strip()


def stream_text(prompt, max_tokens=2000):
    """Token-by-token generator."""
    stream = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=max_tokens,
        temperature=0.2,
        stream=True,
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content


# ---------------------------------------------------------------- JSON parsing (quiz / flashcards)
def _parse_json(text):
    t = re.sub(r"```(?:json)?", "", text or "").strip()
    data = None
    for open_c, close_c in (("[", "]"), ("{", "}")):
        s, e = t.find(open_c), t.rfind(close_c)
        if s != -1 and e > s:
            try:
                data = json.loads(t[s : e + 1])
                break
            except Exception:
                continue
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return v
        return None
    return data


def _norm_quiz(data):
    out = []
    for x in data if isinstance(data, list) else []:
        try:
            opts = [str(o).strip() for o in x["options"]]
            ai = int(x["answer_index"])
            if len(opts) < 2 or not (0 <= ai < len(opts)):
                continue
            correct = opts[ai]
            random.shuffle(opts)  # the LLM often puts the correct option first
            out.append(
                {
                    "question": str(x["question"]).strip(),
                    "options": opts,
                    "answer_index": opts.index(correct),
                    "explanation": str(x.get("explanation", "")).strip(),
                }
            )
        except Exception:
            continue
    return out


def _norm_cards(data):
    out = []
    for x in data if isinstance(data, list) else []:
        try:
            front, back = str(x["front"]).strip(), str(x["back"]).strip()
            if front and back:
                out.append({"front": front, "back": back})
        except Exception:
            continue
    return out


def generate_items(prompt, kind):
    """Generate quiz / flashcards JSON. Retry once if parsing fails."""
    norm = _norm_quiz if kind == "quiz" else _norm_cards
    text = _llm(prompt, max_tokens=2500, temperature=0.3)
    items = norm(_parse_json(text))
    if not items:
        text = _llm(
            prompt + "\n\nIMPORTANT: reply with valid JSON only, nothing else.",
            max_tokens=2500,
            temperature=0.2,
        )
        items = norm(_parse_json(text))
    return items, text


# ---------------------------------------------------------------- context helpers
def _fmt(d):
    return (
        f"[File: {d.metadata.get('source_name', 'unknown')} | "
        f"Page {d.metadata.get('page', 0) + 1}]\n{d.page_content}"
    )


def _sample_context(user_id, doc_ids, budget=MAX_CONTEXT_CHARS):
    """Take evenly spaced chunks across the whole document(s) so the context fits the budget."""
    data = _get_db(user_id).get(where={"doc_id": {"$in": list(doc_ids)}})
    by_doc = {}
    for text, meta in zip(data["documents"], data["metadatas"]):
        by_doc.setdefault(meta.get("doc_id"), []).append((meta.get("page", 0), text, meta))
    if not by_doc:
        return "", 0, 0

    per_doc = budget // len(by_doc)
    parts, n_chunks = [], 0
    for items in by_doc.values():
        items.sort(key=lambda x: x[0])  # page order (stable)
        total = sum(len(t) for _, t, _ in items)
        if total > per_doc:
            avg = max(1, total // len(items))
            k = max(1, per_doc // avg)
            step = len(items) / k
            items = [items[int(i * step)] for i in range(k)]
        for page, text, meta in items:
            parts.append(f"[File: {meta.get('source_name', 'unknown')} | Page {page + 1}]\n{text}")
            n_chunks += 1
    return "\n\n".join(parts), n_chunks, len(by_doc)


# Filler words stripped from a request to find its topic.
# Hinglish words are kept on purpose so requests like "quiz banao" still work.
_STOP = set(
    """make create generate give me a an the of on about for this that these those document doc docs
    pdf pdfs paper notes file quiz mcq mcqs flashcard flashcards flash card cards summary summarize
    summarise summarization questions question please banao bana do de dena kar karo ka ki ke ko
    isko iska iski is pe par mein me se aur and or to some few short brief detailed full complete
    from in my our ye yeh wo woh test practice mock multiple choice revision tl dr saar saransh
    """.split()
)


def _topic(question):
    """Strip filler from the request to get the topic ('' means the whole document)."""
    toks = [t for t in re.findall(r"\w+", question.lower()) if t not in _STOP and not t.isdigit()]
    return " ".join(toks) if len(toks) >= 2 else ""


def _count(question, default):
    m = re.search(r"\b(\d{1,2})\b", question)
    return max(3, min(int(m.group(1)), 15)) if m else default


def _task_context(state):
    topic = _topic(state["question"])
    if topic:
        docs = hybrid_retrieve(topic, state["user_id"], state["doc_ids"], k=8)
        if docs:
            return "\n\n".join(_fmt(d) for d in docs), f"topic “{topic}” → {len(docs)} relevant chunks"
    ctx, n_chunks, n_docs = _sample_context(state["user_id"], state["doc_ids"])
    return ctx, f"sampled {n_chunks} chunks across {n_docs} doc(s)"


# ---------------------------------------------------------------- web search (Tavily)
def web_search(query, n=5):
    import httpx  # already installed together with groq

    headers = {"Content-Type": "application/json"}
    if TAVILY_KEY:
        headers["Authorization"] = f"Bearer {TAVILY_KEY}"
    else:
        headers["X-Tavily-Access-Mode"] = "keyless"
    r = httpx.post(
        "https://api.tavily.com/search",
        headers=headers,
        json={"query": query, "max_results": n, "search_depth": "basic"},
        timeout=20,
    )
    r.raise_for_status()
    return [
        {"title": x.get("title", ""), "url": x.get("url", ""), "content": x.get("content", "")}
        for x in r.json().get("results", [])
    ]


# ---------------------------------------------------------------- router
FLASH_RE = re.compile(r"flash\s?-?cards?|revision cards?")
QUIZ_RE = re.compile(r"\bquiz\b|\bmcqs?\b|multiple choice|test me|practice questions|mock test")
SUMMARY_RE = re.compile(r"summar|\btl;?dr\b|saransh|\bsaar\b")
WEB_EXPLICIT_RE = re.compile(
    r"search (the )?(web|internet|online)|web search|google (it|this|karo)|internet (pe|par|se)|online (search|dhundh)"
)
WEB_HINT_RE = re.compile(
    r"\b(latest|news|today|current|currently|recent|recently|newest|updates?|price|weather|released?|202\d)\b|who is the|kaun hai"
)


def _llm_route(question, doc_names):
    prompt = f"""You route questions for a PDF study assistant.
Uploaded documents: {', '.join(doc_names) or 'unknown'}
Choose ONE route:
- qa  : the answer is likely inside the uploaded documents
- web : needs fresh or outside information (recent news, latest versions, prices, current events, facts clearly unrelated to the documents)
Reply with exactly one word: qa or web.

Message: {question}"""
    try:
        out = _llm(prompt, max_tokens=400, low_effort=True).lower()
    except Exception:
        return "qa"
    words = re.findall(r"[a-z]+", out)
    return "web" if words and words[0] == "web" else "qa"


def router_node(state):
    q = state["question"]
    ql = q.lower()
    if FLASH_RE.search(ql):
        route, how = "flashcards", "keyword"
    elif QUIZ_RE.search(ql):
        route, how = "quiz", "keyword"
    elif SUMMARY_RE.search(ql):
        route, how = "summary", "keyword"
    elif WEB_EXPLICIT_RE.search(ql):
        route, how = "web", "keyword"
    elif WEB_HINT_RE.search(ql):
        route, how = _llm_route(q, state.get("doc_names", [])), "LLM"
    else:
        route, how = "qa", "default"
    return {"route": route, "trace": state.get("trace", []) + [f"Router → {route} ({how})"]}


# ---------------------------------------------------------------- nodes
def qa_node(state):
    trace = list(state.get("trace", []))
    standalone, docs = _prepare(
        state["question"], state["user_id"], state["doc_ids"], state.get("history"), 6
    )
    if standalone.strip() != state["question"].strip():
        trace.append(f"Follow-up rewritten → “{standalone}”")
    if not docs:
        return {
            "notice": "I couldn't find this in the selected documents.",
            "standalone": standalone,
            "trace": trace,
        }
    trace.append(f"Hybrid search (vector + BM25 + rerank) → {len(docs)} chunks")
    return {
        "docs": docs,
        "standalone": standalone,
        "prompt": _build_prompt(standalone, docs),
        "trace": trace,
    }


def web_node(state):
    trace = list(state.get("trace", []))
    q = rewrite_question(state["question"], state.get("history"))
    try:
        results = web_search(q)
    except Exception as e:
        return {"notice": f"⚠️ Web search failed: {e}", "trace": trace + ["Web search failed"]}
    if not results:
        return {"notice": "No web results found.", "trace": trace + ["Web search → 0 results"]}
    trace.append(f"Tavily web search ({WEB_MODE}) → {len(results)} results")
    blocks = "\n\n".join(
        f"[{i}] {r['title']} ({r['url']})\n{r['content']}" for i, r in enumerate(results, 1)
    )
    prompt = f"""Answer the question using ONLY the web results below.
Cite sources inline like [1], [2]. If the results are not enough, say so.

Web results:
{blocks}

Question: {q}"""
    return {"web": results, "prompt": prompt, "trace": trace}


def summary_node(state):
    ctx, note = _task_context(state)
    if not ctx:
        return {"notice": "No text found in the selected documents.", "trace": state.get("trace", [])}
    prompt = f"""You are a study assistant. Using ONLY the excerpts below (they may be sampled, so some parts can be missing),
write a clear, well-structured summary in markdown:
- Start with a 2-line TL;DR
- Then "Key points" as short bullets
- Then "Important terms / formulas" if any (write formulas in LaTeX with $...$)
If there are multiple files, summarize each file under its own heading.
Mention page numbers like (p. 3) where helpful.

Excerpts:
{ctx}

User request: {state['question']}"""
    return {"prompt": prompt, "trace": state.get("trace", []) + [f"Summary context: {note}"]}


def quiz_node(state):
    ctx, note = _task_context(state)
    if not ctx:
        return {"notice": "No text found in the selected documents.", "trace": state.get("trace", [])}
    n = _count(state["question"], 5)
    prompt = f"""You are a quiz generator for students. Using ONLY the excerpts below, write {n} multiple-choice questions.
Rules:
- Each question has exactly 4 options (plain text, no "A)" / "B)" prefixes) and exactly one correct option.
- Mix easy and medium difficulty; test understanding, not trivia.
- Add a one-sentence explanation for the correct answer.
Return ONLY valid JSON (no markdown fences, no commentary) in exactly this shape:
[{{"question": "...", "options": ["...", "...", "...", "..."], "answer_index": 0, "explanation": "..."}}]

Excerpts:
{ctx}

User request: {state['question']}"""
    return {
        "prompt": prompt,
        "n_items": n,
        "trace": state.get("trace", []) + [f"Quiz context: {note}", f"Generating {n} MCQs"],
    }


def flashcards_node(state):
    ctx, note = _task_context(state)
    if not ctx:
        return {"notice": "No text found in the selected documents.", "trace": state.get("trace", [])}
    n = _count(state["question"], 8)
    prompt = f"""You are a flashcard generator for students. Using ONLY the excerpts below, write {n} flashcards.
Rules:
- "front" is a short question or term; "back" is a concise answer (1-3 sentences). Formulas in LaTeX with $...$.
- Cover the most important concepts, definitions and formulas.
Return ONLY valid JSON (no markdown fences, no commentary) in exactly this shape:
[{{"front": "...", "back": "..."}}]

Excerpts:
{ctx}

User request: {state['question']}"""
    return {
        "prompt": prompt,
        "n_items": n,
        "trace": state.get("trace", []) + [f"Flashcards context: {note}", f"Generating {n} cards"],
    }


NODES = {
    "qa": qa_node,
    "web": web_node,
    "summary": summary_node,
    "quiz": quiz_node,
    "flashcards": flashcards_node,
}


# ---------------------------------------------------------------- graph
def _build_graph():
    from langgraph.graph import StateGraph, START, END

    g = StateGraph(AgentState)
    g.add_node("router", router_node)
    for name, fn in NODES.items():
        g.add_node(f"node_{name}", fn)  # prefix so node names don't clash with state keys
    g.add_edge(START, "router")
    g.add_conditional_edges(
        "router", lambda s: s["route"], {name: f"node_{name}" for name in NODES}
    )
    for name in NODES:
        g.add_edge(f"node_{name}", END)
    return g.compile()


try:
    _GRAPH = _build_graph()
    ENGINE = "LangGraph"
except Exception:  # langgraph not installed / failed to load
    _GRAPH = None
    ENGINE = "fallback"


def run_agent(question, history, user_id, doc_ids, doc_names=None):
    state = {
        "question": question,
        "history": history or [],
        "user_id": user_id,
        "doc_ids": list(doc_ids),
        "doc_names": doc_names or [],
        "trace": [f"Engine: {ENGINE}"],
    }
    if _GRAPH is not None:
        return _GRAPH.invoke(state)
    state.update(router_node(state))
    state.update(NODES[state["route"]](state))
    return state