# import os
# import re
# import hashlib
# import threading
# from functools import lru_cache

# import chromadb
# from dotenv import load_dotenv
# from langchain_community.document_loaders import PyPDFLoader
# from langchain_text_splitters import RecursiveCharacterTextSplitter
# from langchain_huggingface import HuggingFaceEmbeddings
# from langchain_chroma import Chroma
# from langchain_core.documents import Document
# from groq import Groq

# try:
#     from rank_bm25 import BM25Okapi
# except ImportError:  # pip install rank_bm25
#     BM25Okapi = None

# load_dotenv()  # reads GROQ_API_KEY from .env if present

# EMBED = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
# DB_DIR = "chroma_db"
# MODEL = "openai/gpt-oss-20b"
# client = Groq()  # uses GROQ_API_KEY from environment / .env

# _chroma = chromadb.PersistentClient(path=DB_DIR)  # one client for the whole process
# _splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)

# _bm25_cache = {}  # (user_id, tuple(sorted doc_ids)) -> (bm25, corpus_docs)
# _bm25_lock = threading.Lock()
# _reranker = None  # lazy-loaded CrossEncoder (False = load failed)
# _reranker_lock = threading.Lock()


# @lru_cache(maxsize=256)
# def _get_db(user_id: int) -> Chroma:
#     """Every user gets their own Chroma collection, so data can never mix between users."""
#     return Chroma(
#         client=_chroma,
#         collection_name=f"user_{int(user_id)}",
#         embedding_function=EMBED,
#     )


# def _invalidate_bm25(user_id):
#     with _bm25_lock:
#         for k in [k for k in _bm25_cache if k[0] == user_id]:
#             del _bm25_cache[k]


# def _hash_key(key: str) -> str:
#     """Stable id from any string key (a URL, a youtube:ID key, or file bytes as hex)."""
#     return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


# def _doc_id(path: str) -> str:
#     """Stable id based on file content (re-uploading the same PDF won't re-index it)."""
#     with open(path, "rb") as f:
#         return _hash_key(f.read().hex())


# def _store_chunks(user_id: int, doc_id: str, docs, display_name: str):
#     """Shared tail of ingest_pdf / ingest_pages: split -> tag -> store -> dedup-aware.

#     Returns (doc_id, number_of_chunks). Raises ValueError if nothing extractable.
#     """
#     store = _get_db(user_id)

#     existing = store.get(where={"doc_id": doc_id})["ids"]
#     if existing:  # already indexed for this user, reuse
#         return doc_id, len(existing)

#     chunks = [c for c in _splitter.split_documents(docs) if c.page_content.strip()]
#     if not chunks:
#         raise ValueError("No extractable text found in this document.")

#     for c in chunks:
#         c.metadata["doc_id"] = doc_id
#         c.metadata["source_name"] = display_name

#     store.add_documents(chunks)
#     _invalidate_bm25(user_id)
#     return doc_id, len(chunks)


# def ingest_pdf(path: str, user_id: int, display_name: str = None):
#     """Load PDF -> chunk -> embed -> store in this user's collection.

#     Returns (doc_id, number_of_chunks). Raises ValueError if the PDF has no text.
#     """
#     doc_id = _doc_id(path)
#     pages = PyPDFLoader(path).load()
#     name = display_name or os.path.basename(path)
#     try:
#         return _store_chunks(user_id, doc_id, pages, name)
#     except ValueError:
#         raise ValueError("No extractable text found in this PDF (is it a scanned image?)") from None


# def ingest_pages(pages, user_id: int, doc_id: str, display_name: str):
#     """Chunk -> embed -> store already-extracted (label, text) pages (web pages, YouTube transcripts).

#     pages: list of (location_label, text) tuples, e.g. from loader.py's load_url().
#     Returns (doc_id, number_of_chunks). Raises ValueError if nothing extractable.
#     """
#     docs = [
#         Document(page_content=text, metadata={"loc": label})
#         for label, text in pages
#         if text and text.strip()
#     ]
#     return _store_chunks(user_id, doc_id, docs, display_name)


# def delete_doc(user_id: int, doc_id: str):
#     """Remove one document's chunks from this user's collection."""
#     _get_db(user_id).delete(where={"doc_id": doc_id})
#     _invalidate_bm25(user_id)


# def _tokenize(text: str):
#     return re.findall(r"\w+", text.lower())


# def _get_bm25(user_id, doc_ids):
#     """BM25 index over all chunks of the selected docs (cached per user)."""
#     key = (user_id, tuple(sorted(doc_ids)))
#     with _bm25_lock:
#         if key in _bm25_cache:
#             return _bm25_cache[key]

#     data = _get_db(user_id).get(where={"doc_id": {"$in": list(doc_ids)}})
#     corpus = [
#         Document(page_content=t, metadata=m)
#         for t, m in zip(data["documents"], data["metadatas"])
#     ]
#     bm25 = None
#     if corpus and BM25Okapi is not None:
#         bm25 = BM25Okapi([_tokenize(d.page_content) for d in corpus])

#     with _bm25_lock:
#         _bm25_cache[key] = (bm25, corpus)
#     return bm25, corpus


# def _get_reranker():
#     """Cross-encoder reranker, loaded on first use (~90MB download)."""
#     global _reranker
#     if _reranker is None:
#         with _reranker_lock:
#             if _reranker is None:
#                 try:
#                     from sentence_transformers import CrossEncoder

#                     _reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
#                 except Exception:
#                     _reranker = False  # load failed, don't retry
#     return _reranker or None


# def hybrid_retrieve(query: str, user_id: int, doc_ids, k: int = 6, fetch_k: int = 15):
#     """Vector + BM25 -> RRF fusion -> cross-encoder rerank -> top-k chunks."""
#     ids = list(doc_ids)

#     # 1) Vector (semantic) search
#     vec_docs = _get_db(user_id).similarity_search(
#         query, k=fetch_k, filter={"doc_id": {"$in": ids}}
#     )

#     # 2) BM25 (keyword) search
#     bm25_docs = []
#     bm25, corpus = _get_bm25(user_id, ids)
#     if bm25 is not None:
#         scores = bm25.get_scores(_tokenize(query))
#         top = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:fetch_k]
#         bm25_docs = [corpus[i] for i in top if scores[i] > 0]

#     # 3) Reciprocal Rank Fusion: merge both rankings
#     fused = {}
#     for ranked in (vec_docs, bm25_docs):
#         for rank, d in enumerate(ranked):
#             key = (d.metadata.get("doc_id"), d.page_content)
#             if key not in fused:
#                 fused[key] = [d, 0.0]
#             fused[key][1] += 1.0 / (60 + rank + 1)
#     candidates = [d for d, _ in sorted(fused.values(), key=lambda x: x[1], reverse=True)]
#     if not candidates:
#         return []

#     # 4) Rerank with the cross-encoder (falls back to RRF order if it fails)
#     reranker = _get_reranker()
#     if reranker is None:
#         return candidates[:k]
#     scores = reranker.predict([(query, d.page_content) for d in candidates])
#     ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
#     return [d for d, _ in ranked[:k]]


# def rewrite_question(question: str, history) -> str:
#     """Turn a follow-up question into a standalone question (using chat history).

#     Example: "what is its formula?" -> "What is the formula of log loss?"
#     """
#     if not history:
#         return question

#     convo = "\n".join(
#         f"{m['role'].capitalize()}: {m['content'][:500]}" for m in history[-6:]
#     )
#     prompt = f"""Given the conversation and the follow-up question, rewrite the follow-up
# as ONE standalone question that is fully understandable without the conversation.
# Keep the same language and meaning. If it is already standalone, return it unchanged.
# Return ONLY the rewritten question, nothing else.

# Conversation:
# {convo}

# Follow-up question: {question}

# Standalone question:"""

#     try:
#         resp = client.chat.completions.create(
#             model=MODEL,
#             messages=[{"role": "user", "content": prompt}],
#             max_completion_tokens=600,  # reasoning model, so leave some extra room
#             temperature=0,
#         )
#         out = (resp.choices[0].message.content or "").strip()
#         return out or question
#     except Exception:
#         return question  # if anything goes wrong, use the original question


# SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


# def _split_sentences(text: str):
#     """Split a chunk into sentences (every piece is an exact substring of the original text)."""
#     parts = SENT_SPLIT.split(text)
#     return [p.strip() for p in parts if len(p.strip()) >= 25]


# def attach_highlights(query: str, docs, top_n: int = 2):
#     """Find the top_n sentences in each chunk that are most relevant to the question.

#     The result is stored as a list of sentences in d.metadata["highlight"].
#     """
#     pairs = []  # (doc_index, sentence)
#     for i, d in enumerate(docs):
#         for s in _split_sentences(d.page_content):
#             pairs.append((i, s))
#     if not pairs:
#         return docs

#     reranker = _get_reranker()
#     if reranker is not None:
#         scores = list(reranker.predict([(query, s) for _, s in pairs]))
#     else:  # fallback: simple keyword overlap
#         q_tokens = set(_tokenize(query))
#         scores = []
#         for _, s in pairs:
#             t = set(_tokenize(s))
#             scores.append(len(q_tokens & t) / ((len(t) ** 0.5) + 1))

#     per_doc = {}
#     for (i, s), sc in zip(pairs, scores):
#         per_doc.setdefault(i, []).append((float(sc), s))

#     for i, d in enumerate(docs):
#         best = sorted(per_doc.get(i, []), key=lambda x: x[0], reverse=True)[:top_n]
#         d.metadata = {**d.metadata, "highlight": [s for _, s in best]}
#     return docs


# def _prepare(question: str, user_id: int, doc_ids, history, k: int):
#     """Rewrite -> hybrid retrieve -> highlights. Returns (standalone_question, docs)."""
#     standalone = rewrite_question(question, history)
#     docs = hybrid_retrieve(standalone, user_id, doc_ids, k=k)
#     try:
#         docs = attach_highlights(standalone, docs)
#     except Exception:
#         pass  # if highlighting fails, the answer still works
#     return standalone, docs


# def _loc_label(meta) -> str:
#     """Human-readable location for a chunk, whichever kind of source it came from."""
#     if meta.get("loc"):
#         return meta["loc"]
#     return f"Page {meta.get('page', 0) + 1}"


# def _build_prompt(standalone: str, docs) -> str:
#     context = "\n\n".join(
#         f"[File: {d.metadata.get('source_name', 'unknown')} | {_loc_label(d.metadata)}] "
#         f"{d.page_content}"
#         for d in docs
#     )
#     return f"""Answer the question using ONLY the context below.
# If the answer is not in the context, say "I couldn't find this in the document."
# Mention the file name and location when you use them.
# If the context comes from multiple files, make clear which file each point comes from.

# Context:
# {context}

# Question: {standalone}"""


# def answer(question: str, user_id: int, doc_ids=None, history=None, k: int = 6):
#     """Non-streaming version: returns (answer_text, docs)."""
#     if not doc_ids:
#         return "Please upload and select a PDF first.", []

#     standalone, docs = _prepare(question, user_id, doc_ids, history, k)
#     if not docs:
#         return "I couldn't find this in the selected documents.", []

#     resp = client.chat.completions.create(
#         model=MODEL,
#         messages=[{"role": "user", "content": _build_prompt(standalone, docs)}],
#         max_completion_tokens=2000,
#         temperature=0.2,
#     )
#     return resp.choices[0].message.content, docs

import os
import re
import hashlib
import threading
from functools import lru_cache

import chromadb
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from groq import Groq

import loaders

try:
    from rank_bm25 import BM25Okapi
except ImportError:  # pip install rank_bm25
    BM25Okapi = None

load_dotenv()  # reads GROQ_API_KEY from .env if present

# Multilingual models (Hindi + 50 other languages). The old English-only ones can't match Hindi text.
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
RERANK_MODEL = os.getenv("RERANK_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-large-v3")  # "whisper-large-v3-turbo" is faster
MODEL = "openai/gpt-oss-20b"
DB_DIR = "chroma_db"
COLLECTION_TAG = "ml"  # new collections: old English-embedding data is never mixed with the new vectors

EMBED = HuggingFaceEmbeddings(model_name=EMBED_MODEL)
try:  # this model truncates at 128 tokens by default; our chunks are longer
    for _attr in ("_client", "client"):
        if hasattr(EMBED, _attr):
            getattr(EMBED, _attr).max_seq_length = 256
            break
except Exception:
    pass

client = Groq()  # uses GROQ_API_KEY from environment / .env
_chroma = chromadb.PersistentClient(path=DB_DIR)  # one client for the whole process

_bm25_cache = {}  # (user_id, tuple(sorted doc_ids)) -> (bm25, corpus_docs)
_bm25_lock = threading.Lock()
_reranker = None  # lazy-loaded CrossEncoder (False = load failed)
_reranker_lock = threading.Lock()


@lru_cache(maxsize=256)
def _get_db(user_id: int) -> Chroma:
    """Every user gets their own Chroma collection, so data can never mix between users."""
    return Chroma(
        client=_chroma,
        collection_name=f"user_{int(user_id)}_{COLLECTION_TAG}",
        embedding_function=EMBED,
    )


def _invalidate_bm25(user_id):
    with _bm25_lock:
        for k in [k for k in _bm25_cache if k[0] == user_id]:
            del _bm25_cache[k]


def _loc(meta) -> str:
    """Human-readable location of a chunk: 'Page 3', 'Slide 5', '12:30', 'Section 2'."""
    return meta.get("loc") or f"Page {meta.get('page', 0) + 1}"


# ---------------------------------------------------------------- ingestion
def _index(pages, doc_id: str, name: str, user_id: int):
    """pages: list of (location_label, text). Chunk -> embed -> store in this user's collection."""
    store = _get_db(user_id)
    existing = store.get(where={"doc_id": doc_id})["ids"]
    if existing:  # already indexed for this user, reuse
        return doc_id, len(existing)

    raw = [
        Document(page_content=text, metadata={"page": i, "loc": loc})
        for i, (loc, text) in enumerate(pages)
        if text and text.strip()
    ]
    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)
    chunks = [c for c in splitter.split_documents(raw) if c.page_content.strip()]
    if not chunks:
        raise ValueError("No extractable text found (is it a scanned image?)")

    for c in chunks:
        c.metadata["doc_id"] = doc_id
        c.metadata["source_name"] = name

    store.add_documents(chunks)
    _invalidate_bm25(user_id)
    return doc_id, len(chunks)


def _file_id(data: bytes) -> str:
    """Stable id based on file content (re-uploading the same file won't re-index it)."""
    return hashlib.sha1(data).hexdigest()[:16]


def ingest_file(path: str, user_id: int, display_name: str = None):
    """PDF / DOCX / PPTX / TXT / MD -> index. Returns (doc_id, number_of_chunks, kind)."""
    name = display_name or os.path.basename(path)
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    with open(path, "rb") as f:
        data = f.read()

    if ext == "pdf":
        loaded = PyPDFLoader(path).load()
        pages = [(f"Page {i + 1}", p.page_content) for i, p in enumerate(loaded)]
    elif ext == "docx":
        pages = loaders.load_docx(data)
    elif ext == "pptx":
        pages = loaders.load_pptx(data)
    elif ext in ("txt", "md"):
        pages = loaders.load_text(data)
    else:
        raise ValueError("Unsupported file type")

    doc_id, n = _index(pages, _file_id(data), name, user_id)
    return doc_id, n, ext


def ingest_pages(pages, user_id: int, name: str, key: str):
    """Pre-extracted pages (web page / YouTube). Returns (doc_id, number_of_chunks)."""
    text_fingerprint = "".join(t for _, t in pages)
    doc_id = _file_id((key + "\n" + text_fingerprint).encode("utf-8"))
    return _index(pages, doc_id, name, user_id)


def delete_doc(user_id: int, doc_id: str):
    """Remove one document's chunks from this user's collection."""
    _get_db(user_id).delete(where={"doc_id": doc_id})
    _invalidate_bm25(user_id)


# ---------------------------------------------------------------- speech to text (Groq Whisper)
def transcribe(audio: bytes, filename: str = "audio.wav", language: str = None) -> str:
    """Speech -> text. `language` is an ISO code like 'hi' or 'en'; None = auto-detect."""
    kwargs = dict(
        file=(filename, audio),
        model=WHISPER_MODEL,
        response_format="json",
        temperature=0.0,
    )
    if language:
        kwargs["language"] = language
    r = client.audio.transcriptions.create(**kwargs)
    return (getattr(r, "text", "") or "").strip()


# ---------------------------------------------------------------- retrieval
def _tokenize(text: str):
    # \u0900-\u0DFF = Indic scripts. Plain \w splits Hindi words at the vowel signs (matras).
    return re.findall(r"[\w\u0900-\u0DFF]+", text.lower())


def _get_bm25(user_id, doc_ids):
    """BM25 index over all chunks of the selected docs (cached per user)."""
    key = (user_id, tuple(sorted(doc_ids)))
    with _bm25_lock:
        if key in _bm25_cache:
            return _bm25_cache[key]

    data = _get_db(user_id).get(where={"doc_id": {"$in": list(doc_ids)}})
    corpus = [
        Document(page_content=t, metadata=m)
        for t, m in zip(data["documents"], data["metadatas"])
    ]
    bm25 = None
    if corpus and BM25Okapi is not None:
        bm25 = BM25Okapi([_tokenize(d.page_content) for d in corpus])

    with _bm25_lock:
        _bm25_cache[key] = (bm25, corpus)
    return bm25, corpus


def _get_reranker():
    """Multilingual cross-encoder reranker, loaded on first use (large download the first time)."""
    global _reranker
    if _reranker is None:
        with _reranker_lock:
            if _reranker is None:
                try:
                    from sentence_transformers import CrossEncoder

                    _reranker = CrossEncoder(RERANK_MODEL)
                except Exception:
                    _reranker = False  # load failed, don't retry
    return _reranker or None


def hybrid_retrieve(query: str, user_id: int, doc_ids, k: int = 6, fetch_k: int = 15):
    """Vector + BM25 -> RRF fusion -> cross-encoder rerank -> top-k chunks."""
    ids = list(doc_ids)

    # 1) Vector (semantic) search
    vec_docs = _get_db(user_id).similarity_search(
        query, k=fetch_k, filter={"doc_id": {"$in": ids}}
    )

    # 2) BM25 (keyword) search
    bm25_docs = []
    bm25, corpus = _get_bm25(user_id, ids)
    if bm25 is not None:
        scores = bm25.get_scores(_tokenize(query))
        top = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:fetch_k]
        bm25_docs = [corpus[i] for i in top if scores[i] > 0]

    # 3) Reciprocal Rank Fusion: merge both rankings
    fused = {}
    for ranked in (vec_docs, bm25_docs):
        for rank, d in enumerate(ranked):
            key = (d.metadata.get("doc_id"), d.page_content)
            if key not in fused:
                fused[key] = [d, 0.0]
            fused[key][1] += 1.0 / (60 + rank + 1)
    candidates = [d for d, _ in sorted(fused.values(), key=lambda x: x[1], reverse=True)]
    if not candidates:
        return []

    # 4) Rerank with the cross-encoder (falls back to RRF order if it fails)
    reranker = _get_reranker()
    if reranker is None:
        return candidates[:k]
    scores = reranker.predict([(query, d.page_content) for d in candidates])
    ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
    return [d for d, _ in ranked[:k]]


def rewrite_question(question: str, history) -> str:
    """Turn a follow-up question into a standalone question (using chat history).

    Example: "what is its formula?" -> "What is the formula of log loss?"
    """
    if not history:
        return question

    convo = "\n".join(
        f"{m['role'].capitalize()}: {m['content'][:500]}" for m in history[-6:]
    )
    prompt = f"""Given the conversation and the follow-up question, rewrite the follow-up
as ONE standalone question that is fully understandable without the conversation.
Keep the same language and script as the follow-up (Hindi stays Hindi). If it is already standalone, return it unchanged.
Return ONLY the rewritten question, nothing else.

Conversation:
{convo}

Follow-up question: {question}

Standalone question:"""

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=600,  # reasoning model, so leave some extra room
            temperature=0,
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or question
    except Exception:
        return question  # if anything goes wrong, use the original question


SENT_SPLIT = re.compile(r"(?<=[.!?।])\s+")  # । is the Hindi full stop


def _split_sentences(text: str):
    """Split a chunk into sentences (every piece is an exact substring of the original text)."""
    parts = SENT_SPLIT.split(text)
    return [p.strip() for p in parts if len(p.strip()) >= 25]


def attach_highlights(query: str, docs, top_n: int = 2):
    """Find the top_n sentences in each chunk that are most relevant to the question.

    The result is stored as a list of sentences in d.metadata["highlight"].
    """
    pairs = []  # (doc_index, sentence)
    for i, d in enumerate(docs):
        for s in _split_sentences(d.page_content):
            pairs.append((i, s))
    if not pairs:
        return docs

    reranker = _get_reranker()
    if reranker is not None:
        scores = list(reranker.predict([(query, s) for _, s in pairs]))
    else:  # fallback: simple keyword overlap
        q_tokens = set(_tokenize(query))
        scores = []
        for _, s in pairs:
            t = set(_tokenize(s))
            scores.append(len(q_tokens & t) / ((len(t) ** 0.5) + 1))

    per_doc = {}
    for (i, s), sc in zip(pairs, scores):
        per_doc.setdefault(i, []).append((float(sc), s))

    for i, d in enumerate(docs):
        best = sorted(per_doc.get(i, []), key=lambda x: x[0], reverse=True)[:top_n]
        d.metadata = {**d.metadata, "highlight": [s for _, s in best]}
    return docs


def _prepare(question: str, user_id: int, doc_ids, history, k: int):
    """Rewrite -> hybrid retrieve -> highlights. Returns (standalone_question, docs)."""
    standalone = rewrite_question(question, history)
    docs = hybrid_retrieve(standalone, user_id, doc_ids, k=k)
    try:
        docs = attach_highlights(standalone, docs)
    except Exception:
        pass  # if highlighting fails, the answer still works
    return standalone, docs


LANG_RULE = (
    "Reply in the same language and script as the question (a Hindi question gets a Hindi answer in Devanagari). "
    "If the question is romanized Hindi (Hinglish), reply in simple English."
)


def _build_prompt(standalone: str, docs) -> str:
    context = "\n\n".join(
        f"[File: {d.metadata.get('source_name', 'unknown')} | {_loc(d.metadata)}] "
        f"{d.page_content}"
        for d in docs
    )
    return f"""Answer the question using ONLY the context below.
If the answer is not in the context, say "I couldn't find this in the document." (in the question's language).
Mention the file name and the location (page, slide, section or timestamp) when you use them.
If the context comes from multiple files, make clear which file each point comes from.
{LANG_RULE}

Context:
{context}

Question: {standalone}"""


def answer(question: str, user_id: int, doc_ids=None, history=None, k: int = 6):
    """Non-streaming version: returns (answer_text, docs)."""
    if not doc_ids:
        return "Please upload and select a document first.", []

    standalone, docs = _prepare(question, user_id, doc_ids, history, k)
    if not docs:
        return "I couldn't find this in the selected documents.", []

    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": _build_prompt(standalone, docs)}],
        max_completion_tokens=2000,
        temperature=0.2,
    )
    return resp.choices[0].message.content, docs
