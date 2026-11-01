# Implementation Plan

**Sources:** `architecture.md`, `PRD.md`  
**Rule:** each phase reads only the previous phase’s folder and writes only its own folder.

| Phase | Reads | Does | Writes | Must not |
| --- | --- | --- | --- | --- |
| 1 Directory | — | Scaffold + config + URL list | repo layout | Fetch or parse pages |
| 2 Scrape | `sources/urls.csv` | HTTP GET public pages | `data/raw/` | Clean text or chunk |
| 3 Load | `data/raw/` | HTML → one cleaned document per URL | `data/loaded/` | Network or chunk |
| 4 Chunk | `data/loaded/` | Split documents into fact chunks | `data/chunks/` | Embed or index |
| 5 Embed | `data/chunks/` | Vectorize chunk text | `data/embeddings/` | Open Chroma |
| 6 Vector DB | chunks + embeddings | Upsert into Chroma | `data/chroma/` | Re-embed or scrape |
| 7 Guardrails | user question | PII / advice / performance / empty-input | allow or refuse | Retrieve or call LLM for facts |
| 8 Retrieval | allowed question + Chroma | Embed query, top-k, trace | `data/retrieval/` | Call Groq |
| 9 Generate | retrieval hits | Groq answer + citation | answer object | Search Chroma again |
| 10 UI | answer + `data/` | Streamlit Q&A and stage viewers | — | Own scrape/index logic |

Query order at runtime: **7 → 8 → 9**, shown in **10**.

---

## 1. Directory structure

Create folders and config only. No pages are fetched.

```
NextLeap/
  config.yaml
  .env.example
  .gitignore
  requirements.txt
  sources/urls.csv
  data/raw/
  data/loaded/
  data/chunks/
  data/embeddings/
  data/chroma/
  data/retrieval/
  src/config.py
  src/ingest/          # scrape, load, chunk, embed, index — one module per phase
  src/safety/
  src/retrieve/
  src/generate/
  src/ui/app.py
  samples/qa.md
```

**`config.yaml`:** embedding model `sentence-transformers/all-MiniLM-L6-v2`, Chroma path + collection, chunk sizes, `top_k`, `min_score`, `llm.provider: groq`.

**`.env`:** `GROQ_API_KEY`, `LLM_MODEL` (not committed).

**`sources/urls.csv`:** `doc_id,page_type,title,url`  
`page_type`: `scheme | amc | howto | educational | factsheet`

Seed URLs (grow to 15–25 official pages later: Groww, HDFC AMC, SEBI, AMFI only):

| doc_id | page_type | url |
| --- | --- | --- |
| hdfc-amc | amc | https://groww.in/mutual-funds/amc/hdfc-mutual-funds |
| hdfc-large-cap-direct-growth | scheme | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth |
| hdfc-flexi-cap-direct-growth | scheme | https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth |
| hdfc-elss-tax-saver-direct-growth | scheme | https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth |
| hdfc-mid-cap-direct-growth | scheme | https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth |

**Done when:** `load_settings()` works; csv exists; `data/*` folders exist and are empty.

---

## 2. Scrape

**File:** `src/ingest/scrape.py`  
**In:** `sources/urls.csv`  
**Out:** `data/raw/{doc_id}.html` and `data/raw/{doc_id}.meta.json`

Public GET only. No login. Sleep 1–2s between requests. If HTML already exists, skip the network unless `--refresh`.

`meta.json`: `doc_id`, `url`, `fetched_at`, `http_status`, `content_type`, `error`.

On failure, still write meta with `error`. Do **not** write cleaned JSON here.

**Done when:** five seed pages have raw HTML + meta; rerun without `--refresh` does not refetch.

---

## 3. Load documents

**File:** `src/ingest/load.py`  
**In:** `data/raw/` only (no HTTP)  
**Out:** `data/loaded/{doc_id}.json`

This phase was previously mixed into scrape. It is the step **before chunking**.

- Parse HTML with BeautifulSoup.
- Drop `script`, `style`, `nav`, `footer`.
- Keep visible text and headings.
- Write loaded JSON **only** if `http_status == 200` and text is non-empty.

```json
{
  "doc_id": "...",
  "source_url": "...",
  "title": "...",
  "page_type": "scheme",
  "fetched_at": "...",
  "http_status": 200,
  "text": "..."
}
```

Failed or empty raw files: skip; do not invent text.

After this phase, **read** the loaded files and note how facts appear (labels vs tables). That observation is used in phase 4 only.

**Done when:** five `data/loaded/*.json` files exist with `source_url`, `fetched_at`, and non-empty `text`.

---

## 4. Chunking

**File:** `src/ingest/chunk.py`  
**In:** `data/loaded/` only  
**Out:** `data/chunks/{doc_id}.jsonl`

| `page_type` | How to chunk |
| --- | --- |
| `scheme` | One chunk per fact (expense ratio, exit load, min SIP, lock-in, riskometer, benchmark). Text = scheme name + field + value |
| `amc` | Split on headings; window + overlap if a section is long |
| `howto` / `educational` / `factsheet` | Keep a section or procedure together |

Fallback (flat blob only): `max_chars` / `overlap_chars` from config.

Every chunk must include `chunk_id`, `text`, `source_url`, `title`, `scheme_or_page_id`, `section`, `fetched_at`. Drop chunks with no `source_url`. Do not embed here.

**Done when:** scheme facts are separate jsonl lines and can be opened without rerunning scrape/load.

---

## 5. Embedding

**Files:** `src/ingest/embedder.py`, `src/ingest/embed.py`  
**In:** `data/chunks/`  
**Out:** `data/embeddings/` (one record per chunk: `chunk_id`, model name, dim, vector)

- Model from config: `sentence-transformers/all-MiniLM-L6-v2` (dim 384)
- Embed `text` only
- `get_embedder()` is the only model loader; retrieval will import it later
- Do **not** write to Chroma in this phase

**Done when:** embedding count equals chunk count; `model` in the dump matches config.

---

## 6. Store in vector DB (Chroma)

**Files:** `src/ingest/chroma_client.py`, `src/ingest/index.py`  
**In:** `data/chunks/` + `data/embeddings/`  
**Out:** `data/chroma/`

- Persistent client, collection name from config
- Upsert existing vectors (do **not** call the embedder again)
- Metadata: `source_url`, `title`, `section`, `fetched_at`, `scheme_or_page_id`
- Collection metadata stores the embedding model name
- `--rebuild` deletes and recreates the collection

Ingest CLI runs **2 → 3 → 4 → 5 → 6** in order, each skippable if its output already exists:

```bash
python -m src.ingest.run
```

**Done when:** `collection.count()` equals chunk count; a new process opens the same persist dir.

---

## 7. Guardrails

**Files:** `src/safety/pii.py`, `src/safety/intent.py`  
**In:** raw user question  
**Out:** `allow` (pass the question through) or `refuse` (answer object, no retrieve)

Runs **before** retrieval. Does not search Chroma and does not ask Groq for scheme facts.

| Case | Detect | Response |
| --- | --- | --- |
| Empty | whitespace only | Ask for a question |
| PII | PAN, Aadhaar, phone, email, OTP, long account numbers | Refuse; do not persist the question |
| Advice | buy/sell/hold, “should I”, suitable for me, allocation, timing | Refuse + educational official URL |
| Performance | returns, CAGR, beat the index, which fund is better | No math; link factsheet or scheme URL |
| Out of corpus (optional rules) | other AMC, login, predict NAV | Out of scope + AMC or educational URL |

Empty / low-score retrieval is **not** this phase; that is handled in retrieval (phase 8).

**Done when:** PII/advice/performance fixtures refuse without writing PII to `data/retrieval/`.

---

## 8. Retrieval

**File:** `src/retrieve/search.py`  
**In:** allowed question + Chroma + same `get_embedder()` as phase 5  
**Out:** hit list + `data/retrieval/{query_id}.json`

- Embed the question
- Query `top_k`
- Drop hits below `min_score`
- `citation_url` = `source_url` of the best remaining hit (one URL)
- `last_updated_from_sources` = max `fetched_at` among kept hits
- If no hits: status `not_found`, still one official fallback URL (AMC), **no LLM**
- Do **not** call Groq here

```bash
python -m src.retrieve.search --q "What is the exit load of HDFC Large Cap Fund Direct Growth?"
```

**Done when:** that question returns an Exit Load chunk and the Large Cap scheme URL.

---

## 9. Generate

**Files:** `src/generate/prompt.py`, `src/generate/answer.py`, `src/generate/providers/groq.py`  
**In:** question + retrieval hits (phase 8)  
**Out:** answer object

Skip this phase when guardrails refused or retrieval is `not_found` (those answers are already complete).

- Groq, `LLM_MODEL` from `.env`
- Use chunks only; ≤3 sentences; Python appends the one `citation_url` and last-updated line
- `LLMClient` interface so other providers can be added later; do not retrieve again

```json
{
  "text": "...",
  "citation_url": "https://...",
  "last_updated_from_sources": "YYYY-MM-DD",
  "refused": false,
  "refusal_reason": null
}
```

Wire `src/query.py` as **7 → 8 → 9** only.

**Done when:** expense-ratio question is grounded, cited, dated; forced-empty retrieval does not invent a number.

---

## 10. UI

**File:** `src/ui/app.py`  
Calls `src/query.py`. Does not scrape, chunk, embed, or index.

Always show: welcome, three example questions, **Facts-only. No investment advice.**, answer + citation + last-updated.

Demo tabs **read** existing folders: loaded docs, chunks, index count, last retrieval trace.

If Chroma is empty: tell the user to run ingest. If Groq fails: show the error; do not fake an answer.

```bash
streamlit run src/ui/app.py
```

---

## Guardrail test cases

Unit tests on phase 7 (no Groq). Integration tests on 8–9 after ingest.

**PII — refuse; raw question never on disk**

| # | Input |
| --- | --- |
| P1 | `My PAN is ABCDE1234F, what is exit load?` |
| P2 | `Aadhaar 1234 5678 9012, min SIP?` |
| P3 | `Email me at user@gmail.com` |
| P4 | `OTP 482193` |
| P5 | Phone `9876543210` in the question |

**Advice — refuse; no recommendation wording**

| # | Input |
| --- | --- |
| A1 | `Should I buy HDFC Mid Cap?` |
| A2 | `Is HDFC ELSS good for me?` |
| A3 | `Sell HDFC Large Cap now?` |
| A4 | `How should I allocate between large cap and flexi cap?` |

**Performance — no computed returns**

| # | Input |
| --- | --- |
| R1 | `What is the 3 year CAGR of HDFC Flexi Cap?` |
| R2 | `Did HDFC Large Cap beat Nifty 100?` |
| R3 | `Which of these four funds performed best?` |

**Factual (after ingest)**

| # | Input | Expect |
| --- | --- | --- |
| F1 | Expense ratio of HDFC Large Cap Direct Growth | Value from chunk + scheme URL |
| F2 | ELSS lock-in | Value from page + ELSS URL |
| F3 | Minimum SIP of HDFC Mid Cap | Value + mid-cap URL |
| F4 | Exit load of HDFC Flexi Cap | Value + flexi-cap URL |
| F5 | Riskometer or benchmark of a listed scheme | Value + scheme URL |
| F6 | How to download capital-gains statement | Steps + howto URL |

**Other**

| # | Input | Expect |
| --- | --- | --- |
| E1 | Empty / whitespace | Ask for a question; no retrieve, no LLM |
| E2 | `What is the NAV of SBI Small Cap?` | Out of corpus; no fake NAV |
| E3 | `Compare returns of all four funds` | Performance refuse |
| E4 | `min_score=1.0` (forced empty retrieval) | `not_found`; no invented expense ratio |

Every answer: ≤3 sentences plus last-updated line; exactly one citation URL.

---

## Run order

```bash
python -m src.ingest.run              # 2 scrape → 3 load → 4 chunk → 5 embed → 6 chroma
python -m src.retrieve.search --q "..."
pytest tests/                         # guardrails (7); factual after ingest
streamlit run src/ui/app.py           # 10
```
