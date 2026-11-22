# DocMind 3D

A RAG-based document assistant that goes beyond "chat with your PDF" — it ingests multiple
formats, answers in Hindi or English, and measures its own answer quality.

## Features

- **Multi-format ingestion** — PDF, DOCX, PPTX, TXT/MD, web pages, and YouTube transcripts
- **Hindi + multilingual support** — multilingual embeddings and reranker, plus voice questions
  transcribed with Groq Whisper
- **Hybrid retrieval** — vector search + BM25 keyword search, fused with Reciprocal Rank Fusion
  and re-ranked with a cross-encoder
- **Agentic routing (LangGraph)** — routes each question to Q&A, web search, summary, quiz, or
  flashcard generation
- **Per-user accounts** — FastAPI + JWT auth, with each user's documents in an isolated vector
  store (no data crosses between users)
- **Built-in evaluation dashboard** — RAGAS-style metrics (faithfulness, answer relevancy,
  context precision) scored by an LLM judge, with a trend chart and a test-set runner

## Architecture

```
Streamlit (app.py)  →  FastAPI (api.py)  →  LangGraph agent (agent.py)
                                           →  Retrieval + ingestion (rag.py, loaders.py)
                                           →  SQLite (db.py) · ChromaDB (vector store)
                                           →  Groq (gpt-oss-20b for chat, Whisper for voice)
```

- `app.py` — Streamlit frontend. Handles auth, uploads, chat UI, and the evaluation dashboard.
  Talks to the API over HTTP; holds no business logic itself.
- `api.py` — FastAPI backend. Auth, document upload/URL ingestion, chat streaming (NDJSON),
  evaluation endpoints.
- `agent.py` — LangGraph state machine that routes a question and builds the final prompt.
- `rag.py` — embeddings, hybrid retrieval (vector + BM25 + rerank), and Whisper transcription.
- `loaders.py` — pure-Python text extraction for DOCX, PPTX, TXT, web pages, and YouTube
  (no compiled dependencies, so it works even under restrictive environments).
- `db.py` — SQLite: users, per-user document registry, evaluation log.
- `auth.py` — password hashing (scrypt) and JWT issuing/verification.
- `evaluator.py` — the three RAGAS-style metrics, each computed by prompting the LLM.

## Setup

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
GROQ_API_KEY=your_groq_api_key
JWT_SECRET=run: python -c "import secrets; print(secrets.token_hex(32))"
# optional:
# TAVILY_API_KEY=your_tavily_key        (for web search; falls back to keyless mode without it)
# WHISPER_MODEL=whisper-large-v3-turbo  (faster, default is whisper-large-v3)
```

Run the backend and frontend in two terminals:

```bash
python -m uvicorn api:app --port 8000
python -m streamlit run app.py
```

Open `http://localhost:8501`, create an account, and upload a document.

## Notes

- The evaluation judge is the same model that generates answers (`gpt-oss-20b`), so scores are
  best used to compare changes over time, not as an absolute measure of correctness.
- `loaders.py` deliberately avoids `python-docx`/`python-pptx` (which pull in compiled `lxml`)
  and instead parses the underlying Office XML directly with the standard library.
