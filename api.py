# """DocMind API (FastAPI). All business logic lives here; the Streamlit app is only a client.

# Run:  uvicorn api:app --port 8000
# Docs: http://localhost:8000/docs  (Swagger UI, has an Authorize button for the bearer token)
# """
# import json
# import logging
# import os
# import tempfile
# from typing import List, Literal

# from fastapi import Depends, FastAPI, File, HTTPException, Response, UploadFile
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.responses import StreamingResponse
# from pydantic import BaseModel, Field

# import auth
# import db
# from loader import load_url, LoadError
# from rag import ingest_pdf, ingest_pages, delete_doc, _hash_key
# from agent import run_agent, stream_text, generate_items, ENGINE, WEB_MODE
# from evaluator import evaluate, eval_question

# log = logging.getLogger("docmind")
# MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
# MAX_DOCS_PER_USER = int(os.getenv("MAX_DOCS_PER_USER", "20"))

# db.init()
# app = FastAPI(title="DocMind API", version="1.0")
# app.add_middleware(
#     CORSMiddleware,  # only needed if a browser frontend (React etc.) calls the API directly
#     allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:8501,http://localhost:3000").split(","),
#     allow_methods=["*"],
#     allow_headers=["*"],
# )


# # ---------------------------------------------------------------- schemas
# class Credentials(BaseModel):
#     username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
#     password: str = Field(min_length=8, max_length=128)


# class HistoryMsg(BaseModel):
#     role: Literal["user", "assistant"]
#     content: str


# class ChatRequest(BaseModel):
#     question: str = Field(min_length=1, max_length=2000)
#     doc_ids: List[str] = Field(min_length=1, max_length=50)
#     history: List[HistoryMsg] = []


# class EvaluateRequest(BaseModel):
#     question: str = Field(max_length=2000)
#     answer: str = Field(max_length=20000)
#     contexts: List[str] = Field(max_length=20)


# class RunEvalRequest(BaseModel):
#     question: str = Field(min_length=1, max_length=2000)
#     doc_ids: List[str] = Field(min_length=1, max_length=50)


# class UrlRequest(BaseModel):
#     url: str = Field(min_length=1, max_length=2000)


# # ---------------------------------------------------------------- helpers
# def _token_response(user):
#     return {
#         "token": auth.make_token(user["id"]),
#         "username": user["username"],
#         "engine": ENGINE,
#         "web_mode": WEB_MODE,
#     }


# def _owned(user, doc_ids):
#     """Return the user's documents for these ids, or 404 if any id isn't theirs."""
#     ids = list(dict.fromkeys(doc_ids))
#     rows = db.get_documents(user["id"], ids)
#     if len(rows) != len(ids):
#         raise HTTPException(404, "Document not found")
#     return rows


# def _event(**kw):
#     return json.dumps(kw, ensure_ascii=False) + "\n"


# def _loc_of(meta) -> str:
#     """Human-readable location for a chunk, whichever kind of source it came from."""
#     if meta.get("loc"):
#         return meta["loc"]
#     return f"Page {meta.get('page', 0) + 1}"


# # ---------------------------------------------------------------- auth
# @app.get("/health")
# def health():
#     return {"status": "ok"}


# @app.post("/auth/register", status_code=201)
# def register(c: Credentials):
#     uid = db.create_user(c.username, auth.hash_password(c.password))
#     if uid is None:
#         raise HTTPException(409, "Username already taken")
#     return _token_response(db.get_user(uid))


# @app.post("/auth/login")
# def login(c: Credentials):
#     user = db.get_user_by_name(c.username)
#     if not auth.verify_password(c.password, user["password_hash"] if user else None):
#         raise HTTPException(401, "Invalid username or password")
#     return _token_response(user)


# # ---------------------------------------------------------------- documents
# @app.get("/documents")
# def list_documents(user=Depends(auth.current_user)):
#     return db.list_documents(user["id"])


# @app.post("/documents", status_code=201)
# def upload_document(file: UploadFile = File(...), user=Depends(auth.current_user)):
#     name = os.path.basename(file.filename or "")
#     if not name.lower().endswith(".pdf"):
#         raise HTTPException(400, "Only PDF files are supported")
#     if len(db.list_documents(user["id"])) >= MAX_DOCS_PER_USER:
#         raise HTTPException(409, f"Document limit reached ({MAX_DOCS_PER_USER}). Delete one first.")

#     fd, tmp = tempfile.mkstemp(suffix=".pdf")
#     try:
#         size = 0
#         with os.fdopen(fd, "wb") as out:
#             while True:
#                 chunk = file.file.read(1024 * 1024)
#                 if not chunk:
#                     break
#                 size += len(chunk)
#                 if size > MAX_UPLOAD_MB * 1024 * 1024:
#                     raise HTTPException(413, f"File too large (max {MAX_UPLOAD_MB} MB)")
#                 out.write(chunk)
#         with open(tmp, "rb") as f:
#             if f.read(5) != b"%PDF-":
#                 raise HTTPException(400, "This file is not a valid PDF")
#         doc_id, n = ingest_pdf(tmp, user["id"], display_name=name)
#     except HTTPException:
#         raise
#     except ValueError as e:
#         raise HTTPException(422, str(e))
#     except Exception:
#         log.exception("PDF ingest failed")
#         raise HTTPException(422, "Could not read this PDF")
#     finally:
#         if os.path.exists(tmp):
#             os.remove(tmp)

#     db.add_document(user["id"], doc_id, name, n)
#     return {"doc_id": doc_id, "name": name, "chunks": n}


# @app.post("/documents/url", status_code=201)
# def upload_url(req: UrlRequest, user=Depends(auth.current_user)):
#     if len(db.list_documents(user["id"])) >= MAX_DOCS_PER_USER:
#         raise HTTPException(409, f"Document limit reached ({MAX_DOCS_PER_USER}). Delete one first.")

#     try:
#         result = load_url(req.url)
#     except LoadError as e:
#         raise HTTPException(400, str(e))
#     except Exception:
#         log.exception("URL fetch failed")
#         raise HTTPException(422, "Could not read that link")

#     name = result["name"]

#     if result["kind"] == "pdf":
#         fd, tmp = tempfile.mkstemp(suffix=".pdf")
#         try:
#             with os.fdopen(fd, "wb") as out:
#                 out.write(result["data"])
#             doc_id, n = ingest_pdf(tmp, user["id"], display_name=name)
#         except ValueError as e:
#             raise HTTPException(422, str(e))
#         except Exception:
#             log.exception("PDF ingest (from URL) failed")
#             raise HTTPException(422, "Could not read this PDF")
#         finally:
#             if os.path.exists(tmp):
#                 os.remove(tmp)
#     else:
#         doc_id = _hash_key(result["key"])
#         try:
#             doc_id, n = ingest_pages(result["pages"], user["id"], doc_id, name)
#         except ValueError as e:
#             raise HTTPException(422, str(e))
#         except Exception:
#             log.exception("Page ingest (from URL) failed")
#             raise HTTPException(422, "Could not index that content")

#     db.add_document(user["id"], doc_id, name, n, kind=result["kind"])
#     return {"doc_id": doc_id, "name": name, "chunks": n}


# @app.delete("/documents/{doc_id}", status_code=204)
# def remove_document(doc_id: str, user=Depends(auth.current_user)):
#     _owned(user, [doc_id])
#     delete_doc(user["id"], doc_id)
#     db.remove_document(user["id"], doc_id)
#     return Response(status_code=204)


# # ---------------------------------------------------------------- chat (NDJSON stream)
# @app.post("/chat")
# def chat(req: ChatRequest, user=Depends(auth.current_user)):
#     docs = _owned(user, req.doc_ids)
#     names = [d["name"] for d in docs]
#     history = [{"role": m.role, "content": m.content[:2000]} for m in req.history[-6:]]
#     doc_ids = list(dict.fromkeys(req.doc_ids))

#     def gen():
#         try:
#             plan = run_agent(req.question, history, user["id"], doc_ids, names)
#             route = plan["route"]
#             yield _event(type="meta", route=route, trace=plan.get("trace", []))

#             if plan.get("notice"):
#                 yield _event(type="done", content=plan["notice"])
#                 return

#             if route in ("quiz", "flashcards"):
#                 items, _raw = generate_items(plan["prompt"], route)
#                 if items:
#                     text = (
#                         "📝 Quiz ready! Pick your answers and press **Check answers**."
#                         if route == "quiz"
#                         else "🃏 Flashcards ready! Click a card to flip it."
#                     )
#                     yield _event(type="done", content=text, data=items)
#                 else:
#                     yield _event(
#                         type="done",
#                         content="⚠️ The model didn't return valid JSON. Please try again.",
#                     )
#                 return

#             raw = ""
#             for token in stream_text(plan["prompt"]):
#                 raw += token
#                 yield _event(type="token", text=token)

#             final = {
#                 "type": "done",
#                 "content": raw or "⚠️ The model returned an empty response.",
#             }
#             if route == "qa":
#                 final["sources"] = [
#                     {
#                         "name": d.metadata.get("source_name", "unknown"),
#                         "loc": _loc_of(d.metadata),
#                         "text": d.page_content,
#                         "highlight": d.metadata.get("highlight", []),
#                     }
#                     for d in plan.get("docs", [])
#                 ]
#                 final["question"] = plan.get("standalone", req.question)
#                 final["contexts"] = [d.page_content for d in plan.get("docs", [])]
#             elif route == "web":
#                 final["web"] = plan.get("web", [])
#             yield _event(**final)
#         except Exception:
#             log.exception("chat failed")
#             yield _event(type="error", message="Something went wrong while answering. Please try again.")

#     return StreamingResponse(
#         gen(),
#         media_type="application/x-ndjson; charset=utf-8",
#         headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
#     )


# # ---------------------------------------------------------------- evaluation
# @app.post("/evaluate")
# def evaluate_answer(req: EvaluateRequest, user=Depends(auth.current_user)):
#     result = evaluate(req.question, req.answer, req.contexts)
#     db.add_eval(user["id"], "chat", req.question, req.answer, result)
#     return result


# @app.post("/evaluations/run")
# def run_evaluation(req: RunEvalRequest, user=Depends(auth.current_user)):
#     """Run one test-set question through the full pipeline and evaluate it."""
#     _owned(user, req.doc_ids)
#     return eval_question(req.question, user["id"], list(dict.fromkeys(req.doc_ids)), "test set")


# @app.get("/evaluations")
# def list_evaluations(user=Depends(auth.current_user)):
#     return db.list_evals(user["id"])


# @app.delete("/evaluations", status_code=204)
# def clear_evaluations(user=Depends(auth.current_user)):
#     db.clear_evals(user["id"])
#     return Response(status_code=204)

"""DocMind API (FastAPI). All business logic lives here; the Streamlit app is only a client.

Run:  python -m uvicorn api:app --port 8000
Docs: http://localhost:8000/docs  (Swagger UI, has an Authorize button for the bearer token)
"""
import json
import logging
import os
import re
import tempfile
from typing import List, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import auth
import db
import loaders
from rag import ingest_file, ingest_pages, delete_doc, transcribe, _loc
from agent import run_agent, stream_text, generate_items, ENGINE, WEB_MODE
from evaluator import evaluate, eval_question

log = logging.getLogger("docmind")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
MAX_AUDIO_MB = int(os.getenv("MAX_AUDIO_MB", "20"))  # Groq's Whisper limit is 25 MB
MAX_DOCS_PER_USER = int(os.getenv("MAX_DOCS_PER_USER", "20"))

ALLOWED_EXT = {".pdf", ".docx", ".pptx", ".txt", ".md"}

db.init()
app = FastAPI(title="DocMind API", version="1.1")
app.add_middleware(
    CORSMiddleware,  # only needed if a browser frontend (React etc.) calls the API directly
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:8501,http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- schemas
class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)


class HistoryMsg(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    doc_ids: List[str] = Field(min_length=1, max_length=50)
    history: List[HistoryMsg] = []


class EvaluateRequest(BaseModel):
    question: str = Field(max_length=2000)
    answer: str = Field(max_length=20000)
    contexts: List[str] = Field(max_length=20)


class RunEvalRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    doc_ids: List[str] = Field(min_length=1, max_length=50)


class UrlRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


# ---------------------------------------------------------------- helpers
def _token_response(user):
    return {
        "token": auth.make_token(user["id"]),
        "username": user["username"],
        "engine": ENGINE,
        "web_mode": WEB_MODE,
    }


def _owned(user, doc_ids):
    """Return the user's documents for these ids, or 404 if any id isn't theirs."""
    ids = list(dict.fromkeys(doc_ids))
    rows = db.get_documents(user["id"], ids)
    if len(rows) != len(ids):
        raise HTTPException(404, "Document not found")
    return rows


def _event(**kw):
    return json.dumps(kw, ensure_ascii=False) + "\n"


def _check_limit(user):
    if len(db.list_documents(user["id"])) >= MAX_DOCS_PER_USER:
        raise HTTPException(409, f"Document limit reached ({MAX_DOCS_PER_USER}). Delete one first.")


def _magic_ok(ext: str, head: bytes) -> bool:
    if ext == ".pdf":
        return head.startswith(b"%PDF-")
    if ext in (".docx", ".pptx"):
        return head.startswith(b"PK\x03\x04")
    return True  # txt / md


def _ingest_path(path: str, user, name: str):
    """Index a file on disk and register it. Maps loader problems to clean HTTP errors."""
    try:
        doc_id, n, kind = ingest_file(path, user["id"], display_name=name)
    except ValueError as e:  # LoadError is a ValueError; messages are safe to show
        raise HTTPException(422, str(e))
    except Exception:
        log.exception("ingest failed")
        raise HTTPException(422, "Could not read this file")
    db.add_document(user["id"], doc_id, name, n, kind)
    return {"doc_id": doc_id, "name": name, "chunks": n, "kind": kind}


# ---------------------------------------------------------------- auth
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/auth/register", status_code=201)
def register(c: Credentials):
    uid = db.create_user(c.username, auth.hash_password(c.password))
    if uid is None:
        raise HTTPException(409, "Username already taken")
    return _token_response(db.get_user(uid))


@app.post("/auth/login")
def login(c: Credentials):
    user = db.get_user_by_name(c.username)
    if not auth.verify_password(c.password, user["password_hash"] if user else None):
        raise HTTPException(401, "Invalid username or password")
    return _token_response(user)


# ---------------------------------------------------------------- documents
@app.get("/documents")
def list_documents(user=Depends(auth.current_user)):
    return db.list_documents(user["id"])


@app.post("/documents", status_code=201)
def upload_document(file: UploadFile = File(...), user=Depends(auth.current_user)):
    name = os.path.basename(file.filename or "")
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, "Supported files: PDF, DOCX, PPTX, TXT, MD")
    _check_limit(user)

    fd, tmp = tempfile.mkstemp(suffix=ext)
    try:
        size = 0
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_MB * 1024 * 1024:
                    raise HTTPException(413, f"File too large (max {MAX_UPLOAD_MB} MB)")
                out.write(chunk)
        with open(tmp, "rb") as f:
            if not _magic_ok(ext, f.read(5)):
                raise HTTPException(400, f"This file is not a valid {ext[1:].upper()}")
        return _ingest_path(tmp, user, name)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


@app.post("/documents/url", status_code=201)
def add_from_url(req: UrlRequest, user=Depends(auth.current_user)):
    """Web page, PDF link or YouTube video (via its transcript)."""
    _check_limit(user)
    try:
        item = loaders.load_url(req.url)
    except ValueError as e:  # LoadError
        raise HTTPException(422, str(e))
    except Exception:
        log.exception("URL load failed")
        raise HTTPException(422, "Could not load that link")

    if item["kind"] == "pdf":  # a link that points straight to a PDF
        fd, tmp = tempfile.mkstemp(suffix=".pdf")
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(item["data"])
            name = item["name"] if item["name"].lower().endswith(".pdf") else item["name"] + ".pdf"
            return _ingest_path(tmp, user, name)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    try:
        doc_id, n = ingest_pages(item["pages"], user["id"], item["name"], item["key"])
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception:
        log.exception("ingest failed")
        raise HTTPException(422, "Could not index that link")
    db.add_document(user["id"], doc_id, item["name"], n, item["kind"])
    return {"doc_id": doc_id, "name": item["name"], "chunks": n, "kind": item["kind"]}


@app.delete("/documents/{doc_id}", status_code=204)
def remove_document(doc_id: str, user=Depends(auth.current_user)):
    _owned(user, [doc_id])
    delete_doc(user["id"], doc_id)
    db.remove_document(user["id"], doc_id)
    return Response(status_code=204)


# ---------------------------------------------------------------- voice (Groq Whisper)
@app.post("/transcribe")
def transcribe_audio(
    file: UploadFile = File(...),
    language: str = Form(""),
    user=Depends(auth.current_user),
):
    lang = language.strip().lower()
    if lang and not re.fullmatch(r"[a-z]{2,3}", lang):
        raise HTTPException(400, "Invalid language code")
    data = file.file.read(MAX_AUDIO_MB * 1024 * 1024 + 1)
    if not data:
        raise HTTPException(400, "Empty audio")
    if len(data) > MAX_AUDIO_MB * 1024 * 1024:
        raise HTTPException(413, f"Audio too large (max {MAX_AUDIO_MB} MB)")
    try:
        text = transcribe(data, os.path.basename(file.filename or "audio.wav"), lang or None)
    except Exception:
        log.exception("transcription failed")
        raise HTTPException(502, "Speech-to-text failed. Please try again.")
    return {"text": text}


# ---------------------------------------------------------------- chat (NDJSON stream)
@app.post("/chat")
def chat(req: ChatRequest, user=Depends(auth.current_user)):
    docs = _owned(user, req.doc_ids)
    names = [d["name"] for d in docs]
    history = [{"role": m.role, "content": m.content[:2000]} for m in req.history[-6:]]
    doc_ids = list(dict.fromkeys(req.doc_ids))

    def gen():
        try:
            plan = run_agent(req.question, history, user["id"], doc_ids, names)
            route = plan["route"]
            yield _event(type="meta", route=route, trace=plan.get("trace", []))

            if plan.get("notice"):
                yield _event(type="done", content=plan["notice"])
                return

            if route in ("quiz", "flashcards"):
                items, _raw = generate_items(plan["prompt"], route)
                if items:
                    text = (
                        "📝 Quiz ready! Pick your answers and press **Check answers**."
                        if route == "quiz"
                        else "🃏 Flashcards ready! Click a card to flip it."
                    )
                    yield _event(type="done", content=text, data=items)
                else:
                    yield _event(
                        type="done",
                        content="⚠️ The model didn't return valid JSON. Please try again.",
                    )
                return

            raw = ""
            for token in stream_text(plan["prompt"]):  # qa / web / summary
                raw += token
                yield _event(type="token", text=token)

            final = {
                "type": "done",
                "content": raw or "⚠️ The model returned an empty response.",
            }
            if route == "qa":
                final["sources"] = [
                    {
                        "name": d.metadata.get("source_name", "unknown"),
                        "loc": _loc(d.metadata),
                        "text": d.page_content,
                        "highlight": d.metadata.get("highlight", []),
                    }
                    for d in plan.get("docs", [])
                ]
                final["question"] = plan.get("standalone", req.question)
                final["contexts"] = [d.page_content for d in plan.get("docs", [])]
            elif route == "web":
                final["web"] = plan.get("web", [])
            yield _event(**final)
        except Exception:
            log.exception("chat failed")
            yield _event(type="error", message="Something went wrong while answering. Please try again.")

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- evaluation
@app.post("/evaluate")
def evaluate_answer(req: EvaluateRequest, user=Depends(auth.current_user)):
    result = evaluate(req.question, req.answer, req.contexts)
    db.add_eval(user["id"], "chat", req.question, req.answer, result)
    return result


@app.post("/evaluations/run")
def run_evaluation(req: RunEvalRequest, user=Depends(auth.current_user)):
    """Run one test-set question through the full pipeline and evaluate it."""
    _owned(user, req.doc_ids)
    return eval_question(req.question, user["id"], list(dict.fromkeys(req.doc_ids)), "test set")


@app.get("/evaluations")
def list_evaluations(user=Depends(auth.current_user)):
    return db.list_evals(user["id"])


@app.delete("/evaluations", status_code=204)
def clear_evaluations(user=Depends(auth.current_user)):
    db.clear_evals(user["id"])
    return Response(status_code=204)