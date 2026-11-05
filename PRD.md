# Product Requirements Document: Groww Mutual Fund FAQ Assistant

**Product:** NextLeap — facts-only FAQ assistant for HDFC Mutual Fund schemes  
**Source of truth:** `Docs/Problemstatement.txt`  
**Version:** 0.2  
**Status:** Implemented prototype  
**Last updated:** 2026-11-05

---

## 1. Overview

Build a small FAQ assistant that answers **factual questions** about selected HDFC Mutual Fund schemes using **only official public pages** (Groww scheme/AMC pages and, where needed, AMC/SEBI/AMFI public pages).

Typical facts: expense ratio, exit load, minimum SIP, ELSS lock-in, riskometer, benchmark, and how to download statements.

Every answer must include **one source link**. The product gives **no investment advice**.

This is also a **RAG teaching demo**. Each pipeline stage must persist its output so the data can be inspected: load → chunk → embed → retrieve → generate.

---

## 2. Problem

Retail users and support/content teams repeatedly ask the same scheme facts. Answers must be:

- Grounded in public pages, not blogs or app backends
- Cited with a single official URL
- Short and transparent
- Safe: refuse advice, skip PII, and avoid performance claims

---

## 3. Goals and non-goals

### 3.1 Goals

- Answer factual queries about the in-scope AMC and four scheme pages.
- Show **one clear citation URL** on every answer.
- Refuse opinionated / portfolio questions politely, still pointing to a relevant educational public page.
- Persist **each RAG stage** so a reviewer can inspect intermediate data.
- Keep models, vector DB, and LLM provider **configurable** (config + `.env`).

### 3.2 Non-goals

- Investment advice, buy/sell/hold recommendations, or portfolio construction.
- Computing or comparing returns / performance rankings.
- Login, account lookup, or any Groww/AMC private APIs.
- Storing PAN, Aadhaar, account numbers, OTPs, emails, or phone numbers.
- Using third-party blogs as sources.
- Multi-AMC coverage beyond the listed HDFC pages in v1.

---

## 4. Users

| Persona | Need |
| --- | --- |
| Retail user comparing schemes | Fast facts (expense ratio, SIP min, exit load, lock-in, risk, benchmark, statement download) with a source link |
| Support / content team | Repeatable, cited answers instead of hunting scheme pages |

---

## 5. In-scope sources (v1 corpus)

Scrape and store the five core Groww pages plus official public pages needed for
statements, ELSS rules, riskometer education, and refusals. The implemented list
contains **24 URLs** (15–25 required), all HTTPS and all on Groww, SEBI Investor,
AMFI, or Mutual Funds Sahi Hai domains. The exact machine-readable list lives in
`sources/urls.csv` with the schema `doc_id,page_type,title,url`.

### 5.1 Core pages (required)

| # | Page | URL |
| --- | --- | --- |
| 1 | AMC overview — HDFC Mutual Fund | https://groww.in/mutual-funds/amc/hdfc-mutual-funds |
| 2 | Large Cap — HDFC Large Cap Fund Direct Growth | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth |
| 3 | Flexi Cap — HDFC Flexi Cap Direct Plan Growth | https://groww.in/mutual-funds/hdfc-equity-fund-direct-growth |
| 4 | ELSS (tax saver) — HDFC ELSS Tax Saver Fund Direct Plan Growth | https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth |
| 5 | Mid Cap — HDFC Mid Cap Fund Direct Growth | https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth |

### 5.2 Additional official pages (implemented)

| doc_id | Type | Page |
| --- | --- | --- |
| `groww-elss-tax-proof-80c` | howto | Groww Help: Tax proof for Section 80C |
| `groww-elss-statement-download` | howto | Groww Help: Download ELSS tax statement |
| `groww-cas-from-cams` | howto | Groww Help: Generate CAS from CAMS |
| `sebi-elss` | educational | SEBI Investor: Guide to ELSS |
| `sebi-riskometer` | educational | SEBI Investor: Understanding the Riskometer |
| `sebi-understanding-mf` | educational | SEBI Investor: Understanding Mutual Funds |
| `sebi-cas` | educational | SEBI Investor: Consolidated Account Statement |
| `amfi-expense-ratio` | educational | AMFI: Expense Ratio |
| `mfsh-expenses` | educational | MFSH: Expenses in a mutual fund scheme |
| `mfsh-exit-load` | educational | MFSH: Exit load |
| `mfsh-lock-in` | educational | MFSH: Lock-in period |
| `mfsh-riskometer-derived` | educational | MFSH: How the riskometer is derived |
| `mfsh-riskometer-levels` | educational | MFSH: Riskometer levels |
| `mfsh-capital-gains` | educational | MFSH: Capital gain statement |
| `mfsh-cas` | educational | MFSH: Consolidated Account Statement |
| `mfsh-factsheet` | factsheet | MFSH: What is a factsheet? |
| `mfsh-scheme-documents` | educational | MFSH: Scheme-related documents |
| `mfsh-elss` | educational | MFSH: ELSS tax-saving fund |
| `mfsh-sip` | educational | MFSH: What is a SIP? |

Mutual Funds Sahi Hai (MFSH) is AMFI’s investor-education initiative and is
treated as an official AMFI-family source. Exact URLs are in `sources/urls.csv`.

**HDFC AMC’s own website is excluded:** tested `hdfcfund.com` pages return HTTP
403 (“Access Denied” via Akamai) and `hdfcamc.com` is a parked domain, so neither
can be verified as a working automated source. Groww’s HDFC AMC/scheme pages and
the SEBI/AMFI/MFSH pages replace them.

**Public sources only.** No screenshots of app backends. No third-party blogs as citations.

---

## 6. Product requirements

### 6.1 FAQ assistant (working prototype)

The assistant must:

1. Answer **factual** queries only, for example:
   - Expense ratio of [scheme]?
   - ELSS lock-in?
   - Minimum SIP?
   - Exit load?
   - Riskometer / benchmark?
   - How to download a capital-gains statement?
2. Include **one citation link** in every answer.
3. Refuse opinionated / portfolio questions (e.g. “Should I buy/sell?”) with a polite, facts-only message and a relevant **educational** official link.
4. Keep answers **≤ 3 sentences**.
5. Include **“Last updated from sources: &lt;date&gt;”** on answers.
6. Never compute or compare returns; if asked about performance, **link to the official factsheet** (public URL) instead of calculating.

### 6.2 UI (tiny)

- Welcome line
- **3 example questions**
- Persistent note: **“Facts-only. No investment advice.”**
- Chat / Q&A input
- Visible citation + last-updated line on each answer
- Optional (demo): a simple view of pipeline stages (loaded docs, chunks, retrieved chunks) because this is a RAG showcase

### 6.3 Safety and privacy

- **No PII:** do not accept or store PAN, Aadhaar, account numbers, OTPs, emails, or phone numbers. If the user pastes them, refuse, do not persist, and ask them to rephrase without personal data.
- **No advice:** no “should I…”, suitability, timing, or allocation answers.
- **No performance claims:** no computed returns, CAGR, or “this fund beat X”.

### 6.4 Answer quality bar (skills being tested)

| Skill | Requirement |
| --- | --- |
| W1 — Thinking like a model | Identify the exact fact asked; decide **answer vs refuse** |
| W2 — LLMs & prompting | Instruction style, concise phrasing, polite safe-refusals, citation wording |
| W3 — RAG only | Small-corpus retrieval with accurate citations from AMC/SEBI/AMFI/Groww public pages |

---

## 7. RAG architecture (phased, inspectable)

Implement **phase-wise**. Persist artifacts after each stage (files and/or a demo UI), not only the final answer.

### Phase 1 — Data loading

- Fetch the in-scope public pages (and additional official URLs in the source list).
- Store raw HTML and a cleaned text/markdown extract per URL.
- Record fetch timestamp, URL, title, and HTTP status.
- Output example: `data/raw/`, `data/loaded/` (JSON or MD per document).

### Phase 2 — Data chunking

- Choose a **chunking strategy based on the actual page structure** (scheme fact tables vs long AMC copy vs FAQ/how-to pages).
- Prefer structure-aware chunks (e.g. heading + field blocks such as “Exit Load”, “Expense Ratio”) over naive fixed windows when the page is tabular/keyed.
- Each chunk must keep metadata: `source_url`, `title`, `scheme_or_page_id`, `section`, `fetched_at`.
- Output example: `data/chunks/*.jsonl`.

### Phase 3 — Data embedding

- Embedding model (default): `sentence-transformers/all-MiniLM-L6-v2`
- Model id must live in **config**, not hardcoded across the app.
- Persist embeddings + chunk ids for inspection (e.g. `data/embeddings/` and/or Chroma persist dir).

### Phase 4 — Vector store

- Vector DB: **ChromaDB**
- Persist directory and collection name in **config**.
- Same collection used at query time as at ingestion.

### Phase 5 — Retrieval

- Embed the query with the **same** embedding model as ingestion.
- Retrieve from the **same** Chroma collection.
- Return top-k chunks with scores, `source_url`, and section.
- Persist per-query retrieval traces for the demo (e.g. `data/retrieval/` or an in-app “retrieved chunks” panel).

### Phase 6 — Generation

- LLM answers **only** from retrieved chunks.
- System prompt enforces: facts-only, ≤3 sentences, one citation URL, last-updated line, refuse advice/PII/performance math.
- If retrieval is empty or low-confidence, say so and link the closest official page rather than guessing.

---

## 8. Configuration

Make swap-outs **config-driven**. Secrets stay in `.env` (never commit keys).

### 8.1 Config file (e.g. `config.yaml` or `config.py`)

| Setting | Default / intent |
| --- | --- |
| Embedding model | `sentence-transformers/all-MiniLM-L6-v2` |
| Vector DB | ChromaDB |
| Chroma persist path | configurable |
| Collection name | configurable |
| Chunking params | strategy + size/overlap (tuned to data) |
| Top-k | configurable |
| LLM provider | `groq` (switchable) |
| Source URL list | path to CSV/MD of 15–25 URLs |

### 8.2 Environment (`.env`)

| Variable | Purpose |
| --- | --- |
| `GROQ_API_KEY` | Groq API |
| `LLM_MODEL` | Groq model id; verified working model for this project is `qwen/qwen3.8-27b` (confirm against your own Groq catalog if it is retired) |
| Optional later | `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `MISTRAL_API_KEY` |

### 8.3 LLM providers (v1 vs later)

- **v1:** Groq, model from `.env`.
- **Designed for swap:** Gemini, OpenAI, Anthropic, Mistral — same retrieval path, different generator adapter.

---

## 9. Functional requirements (acceptance)

| ID | Requirement | Acceptance |
| --- | --- | --- |
| FR-1 | Load listed AMC + 4 scheme pages | Raw + cleaned artifacts exist per URL |
| FR-2 | Chunk with metadata including source URL | Chunks inspectable; no chunk without URL |
| FR-3 | Embed with MiniLM; store in Chroma | Config-driven model + persist dir |
| FR-4 | Retrieve with same embedder + same collection | Retrieval trace shows chunks + URLs |
| FR-5 | Factual answer ≤3 sentences + one citation + last-updated | Sample Q&A file matches this format |
| FR-6 | Refuse advice questions | Polite refusal + educational official link; no recommendation |
| FR-7 | Refuse / strip PII | No PAN/Aadhaar/accounts/OTP/email/phone stored |
| FR-8 | Performance questions | No computed returns; factsheet/official link only |
| FR-9 | Tiny UI | Welcome, 3 examples, facts-only disclaimer |
| FR-10 | Source list deliverable | 24 official URLs in `sources/urls.csv` (15–25 required), exact 4-column schema |
| FR-11 | Provider/model in config/.env | Changing `.env` model does not require code rewrite |

---

## 10. UX copy (required)

**Disclaimer (UI snippet):**  
Facts-only. No investment advice.

**Welcome (suggested):**  
Ask factual questions about the listed HDFC Mutual Fund schemes. Answers come from public pages and always include a source link.

**Example questions (suggested):**

1. What is the expense ratio of HDFC Large Cap Fund Direct Growth?
2. What is the lock-in for HDFC ELSS Tax Saver Fund?
3. How do I download a capital-gains statement?

**Refusal (suggested pattern):**  
I only share scheme facts from public pages, not buy/sell or portfolio advice. You can read more here: [educational official URL].

---

## 11. Deliverables

| Deliverable | Detail |
| --- | --- |
| Working prototype | App or notebook link; if hosting is not possible, a ≤3-minute demo video |
| Source list | CSV or MD of the 15–25 URLs used |
| README | Setup steps, scope (AMC + schemes), known limits |
| Sample Q&A | 5–10 queries with assistant answers + links |
| Disclaimer snippet | Facts-only, no advice (same as UI) |
| Stage artifacts | Explicit stored outputs for load, chunk, embed, retrieve (RAG demo) |

---

## 12. Success metrics (demo)

- ≥ 8/10 sample factual questions answered correctly vs the source page, with the **correct URL**.
- 100% of answers include exactly one citation (or refusal + educational official link).
- 100% of advice/PII/performance-compute prompts are refused per policy.
- A reviewer can open **on-disk stage outputs** without rerunning the full pipeline.

---

## 13. Known limits (to document in README)

- Corpus is a handful of HDFC pages, not all mutual funds on Groww.
- HDFC AMC’s own website cannot be scraped automatically: tested `hdfcfund.com`
  pages returned HTTP 403, and `hdfcamc.com` is a parked domain. The corpus uses
  Groww, SEBI, AMFI, and MFSH public pages instead.
- Facts can go stale between scrapes; “Last updated from sources” reflects fetch time, not live NAV.
- Groww page HTML may change and break extraction.
- LLM may still hallucinate if retrieval is weak; empty-retrieval must not invent numbers.
- Not a SEBI-registered advisor; not a substitute for scheme documents / SID / KIM / factsheet.

---

## 14. Implementation notes (for the coding agent)

- Prefer a small Python app (Streamlit or similar) so stage data is easy to show.
- Respect `robots.txt` and public-page scraping norms; cache downloads; do not hammer Groww.
- Do not scrape behind login.
- Keep prompts and retrieval thresholds in config so W2/W3 can be tuned without rewriting the UI.
- Schema for a chunk (minimum):

```json
{
  "chunk_id": "hdfc-large-cap-exit-load-01",
  "text": "...",
  "source_url": "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth",
  "title": "HDFC Large Cap Fund Direct Growth",
  "section": "Exit Load",
  "fetched_at": "2026-11-01T00:00:00Z"
}
```

---

## 15. Out of scope for later phases

- Other AMCs and categories
- Live NAV streaming
- User accounts and chat history with PII
- Multi-turn portfolio tools
- Production-grade anti-bot scraping infrastructure
