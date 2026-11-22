"""RAGAS-style evaluation for DocMind (LLM-as-judge on Groq).

Metrics (all scored 0..1):
- faithfulness       : share of claims in the answer that are supported by the retrieved context
- answer_relevancy   : how well the answer addresses the question
                       (generate questions from the answer, compare with the original via embeddings)
- context_precision  : rank-weighted share of retrieved chunks that are relevant to the question

Note: the judge model is the same model that generates answers, so scores can be optimistic.
"""
import os
import re
import json
import time
import numpy as np

from rag import client, MODEL, EMBED, answer as rag_answer

LOG_PATH = "eval_log.jsonl"
METRICS = ["faithfulness", "answer_relevancy", "context_precision"]


# ---------------------------------------------------------------- LLM helpers
def _llm(prompt, max_tokens=2500):
    kwargs = dict(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=max_tokens,
        temperature=0,
    )
    try:  # let the reasoning model think less (falls back if unsupported)
        r = client.chat.completions.create(**kwargs, reasoning_effort="low")
    except Exception:
        r = client.chat.completions.create(**kwargs)
    return (r.choices[0].message.content or "").strip()


def _json(text):
    t = re.sub(r"```(?:json)?", "", text or "").strip()
    for open_c, close_c in (("[", "]"), ("{", "}")):
        s, e = t.find(open_c), t.rfind(close_c)
        if s != -1 and e > s:
            try:
                return json.loads(t[s : e + 1])
            except Exception:
                continue
    return None


# ---------------------------------------------------------------- metrics
def faithfulness(answer, contexts):
    """Split the answer into atomic claims, check each against the context.

    Returns (score or None, list of {"claim", "supported"}).
    """
    if not answer.strip() or not contexts:
        return None, []
    ctx = "\n\n".join(c[:1500] for c in contexts)[:9000]
    prompt = f"""Break the ANSWER into short, atomic factual claims.
Then, for each claim, decide whether it can be directly inferred from the CONTEXT.
Ignore file names, page numbers, citations and formatting.
Return ONLY valid JSON (no markdown fences) in exactly this shape:
[{{"claim": "...", "supported": true}}]

CONTEXT:
{ctx}

ANSWER:
{answer[:3000]}"""
    data = _json(_llm(prompt))
    if not isinstance(data, list):
        return None, []
    claims = []
    for x in data:
        try:
            claims.append({"claim": str(x["claim"]).strip(), "supported": bool(x["supported"])})
        except Exception:
            continue
    if not claims:
        return None, []
    return sum(c["supported"] for c in claims) / len(claims), claims


def answer_relevancy(question, answer, n=3):
    """Generate n questions the answer would fit, then average cosine similarity to the real question."""
    if not answer.strip():
        return None
    prompt = f"""Write {n} different questions that the ANSWER below would be a direct answer to.
Return ONLY a valid JSON list of {n} strings.

ANSWER:
{answer[:3000]}"""
    data = _json(_llm(prompt, max_tokens=1200))
    if not isinstance(data, list):
        return None
    qs = [str(q).strip() for q in data if str(q).strip()][:n]
    if not qs:
        return None
    vecs = np.array(EMBED.embed_documents([question] + qs))
    base, others = vecs[0], vecs[1:]
    sims = others @ base / (np.linalg.norm(others, axis=1) * np.linalg.norm(base) + 1e-9)
    return float(np.clip(sims.mean(), 0.0, 1.0))


def context_precision(question, contexts):
    """Judge each retrieved chunk as relevant or not, then compute rank-weighted precision."""
    if not contexts:
        return None
    numbered = "\n\n".join(f"[{i}] {c[:700]}" for i, c in enumerate(contexts))
    prompt = f"""For each numbered context chunk, decide whether it contains information useful for answering the QUESTION.
Return ONLY valid JSON (no markdown fences) in exactly this shape:
[{{"id": 0, "relevant": true}}]

QUESTION: {question}

CHUNKS:
{numbered}"""
    data = _json(_llm(prompt, max_tokens=1500))
    if not isinstance(data, list):
        return None
    verdict = {}
    for x in data:
        try:
            verdict[int(x["id"])] = bool(x["relevant"])
        except Exception:
            continue
    flags = [verdict.get(i, False) for i in range(len(contexts))]
    if not any(flags):
        return 0.0
    hits, precisions = 0, []
    for k, ok in enumerate(flags, 1):  # average precision over relevant positions
        if ok:
            hits += 1
            precisions.append(hits / k)
    return float(sum(precisions) / len(precisions))


def evaluate(question, answer, contexts):
    """Run all three metrics. A failed metric becomes None instead of crashing."""
    out = {"faithfulness": None, "answer_relevancy": None, "context_precision": None, "claims": []}
    try:
        out["faithfulness"], out["claims"] = faithfulness(answer, contexts)
    except Exception:
        pass
    try:
        out["answer_relevancy"] = answer_relevancy(question, answer)
    except Exception:
        pass
    try:
        out["context_precision"] = context_precision(question, contexts)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- log
def append_log(question, answer, result, source="chat"):
    rec = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "question": question,
        "answer": answer[:400],
        **{m: result.get(m) for m in METRICS},
        "claims": result.get("claims", []),
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load_log():
    if not os.path.exists(LOG_PATH):
        return []
    rows = []
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def clear_log():
    if os.path.exists(LOG_PATH):
        os.remove(LOG_PATH)


def eval_question(question, doc_ids, source="test set"):
    """Run the full RAG pipeline on a question, evaluate it, and log the result."""
    ans, docs = rag_answer(question, doc_ids)
    contexts = [d.page_content for d in docs]
    result = evaluate(question, ans, contexts)
    return append_log(question, ans, result, source)