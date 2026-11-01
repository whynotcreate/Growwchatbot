# Architecture: NextLeap Mutual Fund FAQ Assistant

**Product:** Facts-only RAG FAQ assistant for listed HDFC Mutual Fund pages  
**Source of truth:** `PRD.md`  
**Version:** 0.1  
**Status:** Draft for implementation  
**Last updated:** 2026-11-01

---

## 1. Purpose

This document describes **how** the prototype is built: components, data flow, persisted RAG artifacts, safety gates, and configuration. Product rules (what to answer, what to refuse) live in `PRD.md`.

Design constraints from the PRD that shape the architecture:

- Small public corpus (Groww AMC + 4 schemes, expanded to 15–25 official URLs).
- **Inspectable RAG:** persist scrape → load → chunk → embed → index → retrieve → generate.
- Same embedding model and Chroma collection for ingest and query.
- LLM, embedder, and vector DB are **config / `.env`**, not hardcoded.
- Streamlit (or similar) so stage data is visible in a tiny UI.
- No PII storage, no private APIs, no performance math.

---

## 2. System context

```
┌─────────────┐     question      ┌──────────────────┐     public GET      ┌─────────────────┐
│ Retail user │ ───────────────► │ NextLeap app      │ ─────────────────► │ Groww / AMC /   │
│ Support     │ ◄─────────────── │ (Streamlit + RAG) │ ◄───────────────── │ SEBI / AMFI     │
└─────────────┘  answer+citation └────────┬─────────┘                     │ public pages    │
                                          │                                └─────────────────┘
                                          │ Groq (v1)  Gemini/OpenAI/Anthropic/Mistral (later)
                                          ▼
                                   ┌─────────────┐
                                   │ LLM API     │
                                   └─────────────┘
```

The app never logs into Groww, never calls account APIs, and never stores PAN / Aadhaar / account numbers / OTPs / emails / phones.

---

## 3. High-level architecture

Two pipelines share config, embedder, and Chroma:

| Pipeline | When | Output |
| --- | --- | --- |
| **Ingestion** | Offline / “Rebuild index” | scrape `data/raw` → load `data/loaded` → `data/chunks` → `data/embeddings` → Chroma |
| **Query** | Each user question | Guardrails → retrieve → generate → UI + `data/retrieval` trace |

```
                    config.yaml + .env
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│ Source list   │   │ Embedder      │   │ LLM adapter   │
│ (CSV/MD URLs) │   │ MiniLM (cfg)  │   │ Groq default  │
└───────┬───────┘   └───────▲───────┘   └───────▲───────┘
        │                   │                   │
        ▼                   │                   │
┌───────────────┐           │                   │
│ Scrape        │           │                   │
│ data/raw      │           │                   │
└───────┬───────┘           │                   │
        ▼                   │                   │
┌───────────────┐           │                   │
│ Load/clean    │           │                   │
│ data/loaded   │           │                   │
└───────┬───────┘           │                   │
        ▼                   │                   │
┌───────────────┐           │                   │
│ Chunker       │───────────┤                   │
│ data/chunks   │           │                   │
└───────┬───────┘           │                   │
        ▼                   │                   │
┌───────────────┐           │                   │
│ Embed         │───────────┘                   │
│ embeddings/   │                               │
└───────┬───────┘                               │
        ▼                   │                   │
┌───────────────┐           │                   │
│ Chroma index  │           │                   │
│ data/chroma   │           │                   │
└───────┬───────┘                               │
        │ query-time retrieve                   │
        ▼                                       │
┌───────────────┐     prompt + chunks           │
│ Retriever     │───────────────────────────────┤
└───────────────┘                               │
        ▲                                       │
        │                                       ▼
┌───────────────┐                       ┌───────────────┐
│ Safety gate   │ ──── allow / refuse ─►│ Generator     │
│ PII / advice  │                       │ + citation    │
└───────┬───────┘                       └───────┬───────┘
        │                                       │
        └──────────────► UI (Streamlit) ◄───────┘
```

---

## 4. Suggested repo layout

```
NextLeap/
  PRD.md
  architecture.md
  README.md
  config.yaml                 # models, chroma, chunking, top-k, source list path
  .env                        # keys + LLM_MODEL (not committed)
  sources/urls.csv            # 15–25 official URLs
  data/
    raw/                      # scrape: HTML + fetch meta
    loaded/                   # load: cleaned document JSON
    chunks/                   # chunk jsonl
    embeddings/               # embed dump (not Chroma)
    chroma/                   # vector DB persist
    retrieval/                # retrieve traces (not generation)
  src/
    config.py                 # load yaml + env
    ingest/
      scrape.py            # HTTP → data/raw only
      load.py              # data/raw → data/loaded only
      chunk.py
      embed.py             # data/chunks → data/embeddings only
      index.py             # embeddings + chunks → Chroma only
    retrieve/
      search.py
    generate/
      providers/              # groq.py + stubs for gemini, openai, anthropic, mistral
      prompt.py
      answer.py
    safety/
      pii.py
      intent.py               # facts vs advice vs performance vs PII
    ui/
      app.py                  # Streamlit
  samples/
    qa.md                     # 5–10 Q&A deliverable
```

Stage folders under `data/` are the **RAG demo artifacts**. A reviewer should open them without rerunning the pipeline.

---

## 5. Ingestion pipeline

Scrape and load are separate. Scrape does not write cleaned documents. Load does not hit the network.

### 5.1 Scrape → `data/raw/`

**Input:** `sources/urls.csv`.  
**Process:** HTTP GET public pages only. Cache HTML; do not scrape behind login.  
**Output:** `data/raw/{id}.html` and `data/raw/{id}.meta.json` (`url`, `fetched_at`, `http_status`, `title`, `content_type`, `error`).

**Failure:** non-200 is recorded in meta. Do not write `data/loaded/` here.

### 5.2 Load → `data/loaded/`

**Input:** `data/raw/` only.  
**Process:** Extract visible text (strip nav, scripts).  
**Output:** `data/loaded/{id}.json` only when status is 200 and text is non-empty:

```json
{
  "doc_id": "hdfc-large-cap-direct-growth",
  "source_url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
  "title": "HDFC Large Cap Fund Direct Growth",
  "fetched_at": "2026-11-01T00:00:00Z",
  "http_status": 200,
  "text": "..."
}
```

Empty extract: skip; do not invent text. Chunking reads this folder only.

### 5.3 Chunking

**Strategy (data-driven, as PRD requires):**

| Page type | Strategy |
| --- | --- |
| Scheme pages (keyed facts: expense ratio, exit load, min SIP, riskometer, benchmark) | **Structure-aware:** one chunk per heading/field block (e.g. “Exit Load”) so retrieval hits the exact fact |
| AMC overview | Heading-bounded sections; split long sections with overlap |
| How-to / educational (statements, ELSS lock-in, factsheet links) | Section chunks; keep procedure steps together |

Do **not** default to a single naive character window for scheme tables.

**Every chunk** must include:

| Field | Role |
| --- | --- |
| `chunk_id` | Stable id |
| `text` | Retrieval + generation context |
| `source_url` | Sole citation candidate |
| `title` | Display |
| `scheme_or_page_id` | Filter / grouping |
| `section` | e.g. Exit Load |
| `fetched_at` | “Last updated from sources” |

Write `data/chunks/{doc_id}.jsonl`. **No chunk without `source_url`.**

### 5.4 Embedding → `data/embeddings/`

- Model from config: default `sentence-transformers/all-MiniLM-L6-v2`.
- Embed chunk `text` only; metadata stays sidecar.
- Write inspect dump under `data/embeddings/`. Do not open Chroma here.

### 5.5 Vector store → `data/chroma/`

- **ChromaDB**, persist path and collection name from config.
- Upsert **existing** embeddings from 5.4 plus chunk text/metadata. Do not re-embed.
- Rebuild is idempotent (delete collection or upsert by id).
- Query path **must** use this same persist dir and collection.

---

## 6. Query pipeline (guardrails → retrieve → generate)

```
User question
    │
    ▼
PII detector ── hit ──► refuse, do not persist question, ask to rephrase
    │ clean
    ▼
Intent classifier (rules + optional LLM)
    ├─ advice / portfolio     → refuse + educational official URL
    ├─ performance compute    → no math; link official factsheet URL from corpus
    ├─ out of corpus          → say so; closest official page if any
    └─ factual scheme / how-to
            │
            ▼
    Embed query (same MiniLM)
            │
            ▼
    Chroma query top-k (config)
            │
            ▼
    Persist retrieval trace (data/retrieval/{query_id}.json)
            │
            ▼
    Empty / below score threshold?
            ├─ yes → “not found in sources” + best URL, no invented numbers
            └─ no  → LLM generate from chunks only
                        │
                        ▼
                   Format: ≤3 sentences + one citation URL
                           + “Last updated from sources: {max fetched_at of used chunks}”
```

### 6.1 Phase 5 — Retrieval

- Same embedder instance/config as ingestion.
- Return `text`, score, `source_url`, `section`, `fetched_at`.
- Citation rule: **one URL** — the URL of the chunk that actually supports the stated fact (typically the highest-scoring chunk used in the answer). Do not merge multiple URLs in v1.

### 6.2 Phase 6 — Generation

- System prompt (in `src/generate/prompt.py`, tunables in config):
  - Facts only from provided chunks.
  - ≤ 3 sentences.
  - Exactly one source link (the provided citation URL).
  - Append last-updated line.
  - No buy/sell, no PII echo, no return calculations.
- User/context payload: question + retrieved chunks (id, section, url, text).
- Provider adapter interface:

```python
class LLMClient:
    def generate(self, system: str, user: str) -> str: ...
```

v1: Groq, `LLM_MODEL` from `.env`. Later: Gemini, OpenAI, Anthropic, Mistral behind the same interface.

---

## 7. Safety architecture

Safety runs **before** retrieval persistence of the raw question when PII is detected.

| Gate | Implementation | On hit |
| --- | --- | --- |
| PII | Regex / validators for PAN, Aadhaar, phone, email, OTP-like codes, account numbers | Refuse; **do not write** the raw string to `data/retrieval` or logs |
| Advice | Intent: should I buy/sell, suitable for me, allocate, timing | Refusal copy + educational official URL from source list |
| Performance | Intent: returns, CAGR, beat benchmark, compare funds numerically | Do not compute; return factsheet / official public URL from corpus |
| Grounding | Empty retrieval or low similarity | Explicit “not in sources”; still one official link if possible |

UI always shows: **Facts-only. No investment advice.**

---

## 8. Application / UI layer

**Choice:** Streamlit (PRD: small Python app so stages are easy to show).

**Always visible**

- Welcome line
- Three example questions (click-to-fill)
- Disclaimer: “Facts-only. No investment advice.”
- Question input
- Answer, citation URL, last-updated line

**Demo (RAG showcase) — recommended tabs or expander**

| View | Reads |
| --- | --- |
| Loaded docs | `data/loaded/` |
| Chunks | `data/chunks/` |
| Index status | Chroma count, embedder name, collection |
| Retrieved chunks | last `data/retrieval/` trace |

No user accounts. No chat history that stores PII. In-session messages only.

---

## 9. Configuration and secrets

### 9.1 `config.yaml` (committed defaults)

| Key | Default |
| --- | --- |
| `sources.path` | `sources/urls.csv` |
| `embedding.model` | `sentence-transformers/all-MiniLM-L6-v2` |
| `chunking.strategy` | `structure_aware` (with size/overlap fallback) |
| `chroma.persist_dir` | `data/chroma` |
| `chroma.collection` | `hdfc_mf_faq` |
| `retrieval.top_k` | e.g. `5` |
| `retrieval.min_score` | threshold to treat as empty |
| `llm.provider` | `groq` |
| `prompts` | paths or inline ids for system prompt versions |

Changing embedder, collection, or top-k must not require UI changes.

### 9.2 `.env` (not committed)

| Variable | Use |
| --- | --- |
| `GROQ_API_KEY` | v1 generation |
| `LLM_MODEL` | Groq model id (confirm against Groq catalog; PRD example `qwen/qwen3.8-27b`) |
| Later | `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `MISTRAL_API_KEY` |

`config.llm.provider` selects the adapter; model name always from env for the active provider.

---

## 10. Runtime views

### Ingest (CLI)

```
python -m src.ingest.run          # load → chunk → embed → chroma
```

Idempotent rebuild for demos.

### Serve

```
streamlit run src/ui/app.py
```

Query path does not re-scrape unless the operator runs ingest again. “Last updated from sources” is `fetched_at` from retrieved chunks, not request time.

---

## 11. Data contracts

**Chunk (minimum, from PRD):**

```json
{
  "chunk_id": "hdfc-large-cap-exit-load-01",
  "text": "...",
  "source_url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
  "title": "HDFC Large Cap Fund Direct Growth",
  "scheme_or_page_id": "hdfc-large-cap-direct-growth",
  "section": "Exit Load",
  "fetched_at": "2026-11-01T00:00:00Z"
}
```

**Retrieval trace (demo):**

```json
{
  "query_id": "...",
  "asked_at": "...",
  "question_redacted": true,
  "intent": "factual",
  "hits": [
    {
      "chunk_id": "...",
      "score": 0.82,
      "source_url": "...",
      "section": "Expense Ratio"
    }
  ],
  "citation_url": "...",
  "last_updated_from_sources": "2026-11-01"
}
```

If `intent` is PII, **do not** store the original question.

**Answer object to UI:**

```json
{
  "text": "≤3 sentences",
  "citation_url": "https://...",
  "last_updated_from_sources": "2026-11-01",
  "refused": false,
  "refusal_reason": null
}
```

---

## 12. Technology choices

| Concern | Choice | Why |
| --- | --- | --- |
| Language | Python | Embeddings, Chroma, Streamlit |
| UI | Streamlit | Tiny FAQ UI + stage inspection |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` | PRD; local, small corpus |
| Vector DB | ChromaDB persistent | PRD; file-based demo |
| LLM v1 | Groq | PRD; model via `.env` |
| LLM later | Provider adapters | Same retrieve → generate path |
| HTTP fetch | `httpx` / `requests` + cache | Public pages only |
| HTML → text | BeautifulSoup (or equivalent) | Loaded-stage inspectability |

---

## 13. Trust, freshness, and failure modes

| Risk | Architectural response |
| --- | --- |
| Hallucinated numbers | Generate only from chunks; refuse on empty/low retrieval |
| Stale facts | Persist `fetched_at`; surface as last-updated; re-run ingest to refresh |
| HTML layout change | Loader records status; chunker is structure-aware but may need selector updates |
| Citation mismatch | Citation = URL of the supporting chunk, not a generic homepage |
| PII leak | Gate before disk; retrieval traces store redacted/intent-only on PII |
| Provider outage | UI error; retrieval still inspectable; no fake answer |
| Wrong collection / embedder drift | Single config object shared by ingest and query |

---

## 14. Mapping to PRD acceptance

| PRD | Architecture |
| --- | --- |
| FR-1 Load pages | `ingest/scrape.py` → `data/raw`; `ingest/load.py` → `data/loaded` |
| FR-2 Chunks + URL metadata | `ingest/chunk.py` → `data/chunks` |
| FR-3 MiniLM + Chroma | `ingest/embed.py`, `ingest/index.py`, config |
| FR-4 Same embedder/collection | Shared `config` + `retrieve/search.py` |
| FR-5 Short cited answers | `generate/answer.py` + prompt contract |
| FR-6 Advice refusal | `safety/intent.py` before generate |
| FR-7 PII | `safety/pii.py`; no persist |
| FR-8 No performance math | Intent branch + factsheet URL in corpus |
| FR-9 Tiny UI | `ui/app.py` |
| FR-10 Source list | `sources/urls.csv` |
| FR-11 Config/.env models | `config.yaml` + `LLM_MODEL` |

---

## 15. Implementation sequence

1. Config + source CSV + empty `data/` tree  
2. Scrape → `data/raw`  
3. Load/clean → `data/loaded`  
4. Chunk → `data/chunks`  
5. Embed → `data/embeddings`  
6. Chroma index (no re-embed)  
7. Guardrails  
8. Retriever + traces (no LLM)  
9. Groq generator  
10. Streamlit UI (calls query; does not ingest)  

Each step should leave artifacts on disk before the next step starts—this is the RAG teaching surface, not an implementation detail.
