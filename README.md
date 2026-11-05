# NextLeap — HDFC Mutual Fund FAQ Assistant

A small, facts-only RAG assistant for selected **HDFC Mutual Fund** schemes. It
answers questions about expense ratio, exit load, minimum SIP, ELSS lock-in,
riskometer, benchmark, statements, and related investor-education topics using
**public official pages and Groww’s public scheme/help pages**.

Every answered question gets exactly **one source link** and a
**“Last updated from sources”** date taken from the ingested chunks. Advice,
performance calculations, out-of-corpus questions, and personal data are refused.

> **Facts-only. No investment advice.**

---

## Scope

**AMC:** HDFC Mutual Fund

**Schemes (Direct Growth):**

| Scheme | Groww page |
| --- | --- |
| HDFC Large Cap Fund | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth |
| HDFC Flexi Cap Fund | https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth |
| HDFC ELSS Tax Saver Fund | https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth |
| HDFC Mid Cap Fund | https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth |

HDFC AMC’s own website was not reachable for automated ingestion (HTTP 403), so
the four scheme pages and the AMC listing above are **public Groww pages**. Groww
is a third-party investment platform, not the AMC or a regulator; these pages are
used as supplementary source material. The corpus also includes official SEBI
Investor, AMFI, and Mutual Funds Sahi Hai (AMFI’s investor-education initiative)
pages, plus Groww Help guides for statements and general concepts. See
`sources/urls.csv` for the full **24-URL** list.

---

## What it does

- **Safety gate first:** rule-based intent classification and PII detection run
  before retrieval. Refusals never call the LLM.
- **Grounded retrieval:** embeds the question with the same MiniLM model used at
  ingestion and queries the same Chroma collection.
- **Grounded generation:** the LLM sees only retrieved chunks. Python appends the
  single citation and the real `fetched_at` date after generation.
- **Honest not-found:** empty or low-confidence retrieval returns a deterministic
  “couldn’t find that in the provided sources” answer with one verified fallback
  link — never an invented fact.
- **Inspectable RAG:** every stage writes artifacts under `data/`; retrieval
  traces are stored in `data/retrieval/`.
- **Protected admin dashboard:** loaded docs, chunks, index status, and the latest
  retrieval trace are visible only after a valid `ADMIN_PASSCODE` is entered.

---

## Safety and privacy

| Rule | Behaviour |
| --- | --- |
| No investment advice | Questions such as “Should I buy…”, “Which is better?”, “Is this good for me?”, “Suggest a fund”, or allocation/timing questions are refused with an official educational link. |
| No performance claims | Returns, CAGR, XIRR, NAV history, and performance comparisons are refused; the linked scheme/factsheet page is provided instead. |
| No out-of-corpus answers | Other AMCs, bank-account actions, market predictions, and guarantee claims are refused with an HDFC/AMFI page. |
| No PII | PAN, Aadhaar, account numbers, OTPs, emails, and phone numbers are detected before retrieval. The raw question is discarded and **no retrieval trace is written**. |
| No PII in traces | Even a direct `search()` call stores a PII-redacted question. |
| No secrets in Git | `.env` is gitignored; only `.env.example` is committed. |

The admin dashboard cannot be unlocked with URL query parameters; it requires the
configured passcode and uses a constant-time comparison.

---

## Architecture

```
sources/urls.csv
      │
      ▼
scrape ──► data/raw/          HTML + fetch metadata
      │
      ▼
load ────► data/loaded/       cleaned document JSON
      │
      ▼
chunk ───► data/chunks/       structure-aware chunks + metadata
      │
      ▼
embed ───► data/embeddings/   MiniLM vectors (384-d, normalized)
      │
      ▼
index ───► data/chroma/       persistent Chroma collection
                              │
question ──► safety gate ─────┤
                              ▼
                         retrieve ──► data/retrieval/ trace
                              │
                              ▼
                         generate ──► answer + one citation + date
                              │
                              ▼
                         Streamlit UI
```

- Embedding model: `sentence-transformers/all-MiniLM-L6-v2` (config-driven).
- Vector store: ChromaDB collection `hdfc_mf_faq`.
- LLM provider: Groq; model from `LLM_MODEL`.
- The query path never scrapes, chunks, embeds, or indexes.

Detailed design: [`architecture.md`](architecture.md). Product requirements:
[`PRD.md`](PRD.md).

---

## Repository layout

```
NextLeap/
├── config.yaml                 # models, Chroma, chunking, retrieval, prompt
├── .env.example                # documented environment variables (no secrets)
├── requirements.txt
├── sources/urls.csv            # 24 verified URLs, exact 4-column schema
├── data/
│   ├── raw/                    # scraped HTML + meta
│   ├── loaded/                 # cleaned documents
│   ├── chunks/                 # chunk JSONL
│   ├── embeddings/             # embedding JSONL
│   ├── chroma/                 # committed Chroma index
│   └── retrieval/              # per-query retrieval traces
├── src/
│   ├── config.py
│   ├── query.py                # safety → retrieval → generation entry point
│   ├── ingest/                 # scrape, load, chunk, embed, index, run
│   ├── retrieve/search.py
│   ├── generate/               # prompt, answer, providers/groq.py
│   ├── safety/                 # pii.py, intent.py
│   └── ui/app.py               # Streamlit app + protected admin dashboard
├── samples/qa.md               # 10 tested Q&A examples
└── tests/
```

---

## Quick start

Requirements: Python **3.9+** (the scheduled workflow uses 3.10), a Groq API key,
and ~2 GB free disk for the embedding model and vector store.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env:
#   GROQ_API_KEY=<your key>
#   LLM_MODEL=qwen/qwen3.8-27b
#   ADMIN_PASSCODE=<optional admin passcode>
```

The committed `data/` artifacts already include the index, so ingestion is **not
required** just to run the app. To rebuild the corpus:

```bash
python -m src.ingest.run --refresh --rebuild
```

Run the UI:

```bash
streamlit run src/ui/app.py
```

Open the URL Streamlit prints (normally `http://localhost:8501`).

---

## Configuration

Committed defaults live in `config.yaml`:

| Key | Default |
| --- | --- |
| `sources.path` | `sources/urls.csv` |
| `embedding.model` | `sentence-transformers/all-MiniLM-L6-v2` |
| `chunking.strategy` | `structure_aware` |
| `chunking.max_chars` / `overlap_chars` | `1200` / `200` |
| `chroma.persist_dir` / `collection` | `data/chroma` / `hdfc_mf_faq` |
| `retrieval.top_k` / `min_score` | `5` / `0.35` |
| `llm.provider` | `groq` |
| `prompts.system_id` | `v1` |

Secrets and the active model live in `.env` (never committed):

| Variable | Purpose |
| --- | --- |
| `GROQ_API_KEY` | Groq API key |
| `LLM_MODEL` | Groq model id; `qwen/qwen3.8-27b` is the verified project model |
| `ADMIN_PASSCODE` | Optional; enables the admin inspection dashboard |

Changing `LLM_MODEL` does not require a code change. Groq model availability can
change over time; verify the id against your account’s model list if a request
fails.

### Changing the embedding model or collection

If you change `embedding.model` or `chroma.collection`, re-run ingestion with
`--rebuild`. Ingestion and retrieval share one `Settings` object and one
`get_embedder()` loader, so a mismatch is visible immediately rather than silently
returning bad neighbours.

---

## Tests

```bash
pytest tests/ -q
```

The suite covers:

- source-list size, uniqueness, HTTPS, allowed domains, and four-scheme coverage;
- loader boilerplate pruning without losing short FAQ prose;
- generic-chunk filtering of JS/navigation noise;
- ingestion invariants (loaded = chunked docs, embeddings = chunks, Chroma =
  chunks, failed scrape aborts before indexing);
- safety false positives/negatives (expense-ratio comparison is factual; advice,
  performance, out-of-corpus, and PII are refused);
- PII never written to retrieval traces;
- one citation, real date, ≤3 sentences, and honest not-found behaviour;
- UI helper and admin-authentication behaviour.

Tests do **not** call Groq. Live generation is sampled manually in
`samples/qa.md`.

---

## Deployment (Streamlit Community Cloud or similar)

1. Push the repository with the committed `data/` artifacts. The app reads
   `data/loaded`, `data/chunks`, and `data/chroma`; it does not need to scrape or
   ingest at startup.
2. Set **Streamlit Secrets** (or environment variables):
   ```toml
   GROQ_API_KEY = "..."
   LLM_MODEL = "qwen/qwen3.8-27b"
   ADMIN_PASSCODE = "..."
   ```
3. Use Python 3.10 in the deployment settings and install `requirements.txt`.
4. Confirm the Groq model id is available to the deployment account.
5. Verify the admin dashboard stays locked until the passcode is entered and that
   `?admin=true` does not grant access.

The daily GitHub Actions workflow (`.github/workflows/data_refresh.yml`)
re-scrapes, re-indexes, runs the tests, and commits only
`data/raw`, `data/loaded`, `data/chunks`, `data/embeddings`, and `data/chroma`.
It never commits `.env` or retrieval traces.

---

## Sources and verification

- `sources/urls.csv` contains **24** verified URLs, all HTTPS. The title column
  names the publisher and the `page_type` column names the document type.
- The table schema is exactly `doc_id,page_type,title,url`.

| Publisher | Role | Pages in corpus |
| --- | --- | --- |
| Groww | Third-party investment platform (not the AMC or a regulator) | 1 AMC listing, 4 scheme pages, 3 Help guides |
| SEBI Investor | Market regulator’s investor-education site | 4 educational pages |
| AMFI | Mutual fund industry association | 1 educational page |
| Mutual Funds Sahi Hai | AMFI’s investor-education initiative | 10 educational pages, 1 factsheet explainer |

- All 24 URLs returned HTTP 200 during the 2026-11-05 refresh and produced usable
  extracted text.
- **HDFC AMC’s own site is not in the corpus:** tested `hdfcfund.com` pages
  returned HTTP 403 (“Access Denied” via Akamai), and `hdfcamc.com` now resolves
  to a parked domain. No official HDFC AMC factsheet, SID, or KIM document was
  available in the downloaded corpus or the existing source records, so the app
  does not cite one. Groww’s HDFC AMC/scheme pages are used as supplementary
  sources; SEBI, AMFI, and MFSH provide general investor education.
- The `factsheet` page type here labels MFSH’s explainer about factsheets, not an
  HDFC scheme factsheet.

To re-verify URLs without rebuilding the index:

```bash
python -m src.ingest.scrape --refresh
python -m src.ingest.load
```

`src.ingest.run` aborts before touching Chroma if any scrape fails, so a partial
refresh cannot leave the index inconsistent.

---

## Data artifacts

| Stage | Location | Contents |
| --- | --- | --- |
| Scrape | `data/raw/` | `{doc_id}.html`, `{doc_id}.meta.json` |
| Load | `data/loaded/` | `{doc_id}.json` with cleaned text and fetch date |
| Chunk | `data/chunks/` | `{doc_id}.jsonl` with one chunk per line |
| Embed | `data/embeddings/` | `chunk_id`, `model`, `dim`, normalized `vector` |
| Index | `data/chroma/` | persistent Chroma collection |
| Retrieve | `data/retrieval/` | `{query_id}.json` trace with hits, scores, citation, date |

Current index: **24 documents → 116 chunks → 116 embeddings → 116 vectors**.

---

## Known limitations

- The corpus is a small, HDFC-only demo, not all mutual funds on Groww.
- Facts can go stale between refreshes. “Last updated from sources” reflects the
  last successful scrape, not live NAV.
- Groww/SEBI page HTML can change and break text extraction; re-run ingestion and
  inspect `data/loaded/` if counts drop unexpectedly.
- The LLM can still misread ambiguous chunks. Empty/low-score retrieval is
  refused deterministically, but retrieval quality is not a guarantee of truth.
- The rule-based safety gate is intentionally conservative; novel phrasings may
  need new fixtures in `tests/test_safety_gate.py`.
- Performance questions are refused rather than answered; the app provides the
  linked scheme/factsheet page instead.
- Not a SEBI-registered advisor and not a substitute for the SID, KIM, or
  factsheet.
- HDFC AMC’s own site blocks automated requests. Groww’s public scheme pages are
  used as supplementary sources; they are not official AMC documents.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `GROQ_API_KEY is not set` | Add the key to `.env` or Streamlit Secrets. |
| Groq model/request error | Verify `LLM_MODEL` against the current Groq catalog; `qwen/qwen3.8-27b` worked on 2026-11-05. |
| Chroma index empty/unavailable | Run `python -m src.ingest.run --refresh --rebuild`, or confirm `data/chroma/` is present. |
| Admin expander says disabled | Set `ADMIN_PASSCODE` in environment or Streamlit Secrets. |
| Scrape failure aborts ingestion | Fix or replace the failing URL in `sources/urls.csv`; the existing index is preserved. |
| Facts look stale | Re-run ingestion and check the new `Last updated from sources` date. |

---

## Deliverables

- Working Streamlit prototype: `src/ui/app.py`
- Source list: `sources/urls.csv` (24 URLs)
- README: this file
- Sample Q&A: `samples/qa.md` (10 tested examples)
- Disclaimer: “Facts-only. No investment advice.”
- Stage artifacts: `data/raw`, `data/loaded`, `data/chunks`, `data/embeddings`,
  `data/chroma`, `data/retrieval`
