"""NextLeap — Facts-Only Mutual Fund FAQ Assistant UI.

Streamlit application for Phase 10. Calls ``src.query.answer_question`` and
reads existing ``data/`` artifacts for demo inspection. Does not scrape, chunk,
embed, or index.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

# Ensure repository root is on sys.path so 'src' can be imported in any deployment environment
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

# Forward Streamlit secrets to os.environ if running on Streamlit Cloud
try:
    for _key, _val in st.secrets.items():
        if isinstance(_val, str) and _key not in os.environ:
            os.environ[_key] = _val
except Exception:
    pass

from src.config import Settings, load_settings
from src.generate.answer import GenerationError
from src.ingest import chroma_client
from src.query import answer_question

# Suggested example questions from PRD §10 / implementation.md
EXAMPLE_QUESTIONS = [
    "What is the expense ratio of HDFC Large Cap Fund Direct Growth?",
    "What is the lock-in for HDFC ELSS Tax Saver Fund?",
    "How do I download a capital-gains statement?",
]


@st.cache_data
def get_loaded_files(project_root: Path) -> list[Path]:
    loaded_dir = project_root / "data" / "loaded"
    if not loaded_dir.is_dir():
        return []
    return sorted(loaded_dir.glob("*.json"))


@st.cache_data
def get_chunk_files(project_root: Path) -> list[Path]:
    chunks_dir = project_root / "data" / "chunks"
    if not chunks_dir.is_dir():
        return []
    return sorted(chunks_dir.glob("*.jsonl"))


def get_latest_retrieval_trace(project_root: Path) -> tuple[Path | None, dict[str, Any] | None]:
    retrieval_dir = project_root / "data" / "retrieval"
    if not retrieval_dir.is_dir():
        return None, None
    files = list(retrieval_dir.glob("*.json"))
    if not files:
        return None, None
    # Sort by mtime descending
    latest_file = max(files, key=lambda p: p.stat().st_mtime)
    try:
        data = json.loads(latest_file.read_text(encoding="utf-8"))
        return latest_file, data
    except Exception:
        return latest_file, None


def check_chroma_status(settings: Settings) -> tuple[bool, int, str]:
    """Inspect ChromaDB collection count without running indexing."""
    try:
        client = chroma_client.get_client(settings.chroma_persist_dir)
        collection = chroma_client.open_collection(
            client, settings.chroma_collection, settings.embedding_model
        )
        count = int(collection.count())
        return (count > 0, count, f"Collection '{settings.chroma_collection}' ({count} chunks)")
    except Exception as exc:
        return (False, 0, f"Unavailable: {exc}")


def render_inspection_dashboard(settings: Settings, chroma_count: int) -> None:
    """Render developer/admin RAG pipeline inspection dashboard."""
    st.divider()
    st.subheader("🛠️ RAG Pipeline Inspection (Admin / Developer)")
    tab_docs, tab_chunks, tab_index, tab_trace = st.tabs([
        "📄 Loaded Docs",
        "🧩 Chunks",
        "📊 Index Status",
        "🔍 Last Retrieval Trace",
    ])

    # Tab 1: Loaded Docs
    with tab_docs:
        loaded_files = get_loaded_files(settings.project_root)
        if not loaded_files:
            st.info("No documents found in `data/loaded/`.")
        else:
            selected_doc_path = st.selectbox(
                "Select a loaded document:",
                loaded_files,
                format_func=lambda p: p.stem,
                key="select_loaded_doc",
            )
            if selected_doc_path:
                try:
                    doc_data = json.loads(selected_doc_path.read_text(encoding="utf-8"))
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Document ID", doc_data.get("doc_id", "N/A"))
                    c2.metric("HTTP Status", doc_data.get("http_status", "N/A"))
                    c3.metric("Fetched At", doc_data.get("fetched_at", "N/A"))

                    st.markdown(f"**Title:** {doc_data.get('title', 'N/A')}")
                    source_url = doc_data.get("source_url", "")
                    if source_url:
                        st.markdown(f"**Source URL:** [{source_url}]({source_url})")

                    with st.expander("View Cleaned Text Preview", expanded=True):
                        st.text_area(
                            "Cleaned visible text",
                            doc_data.get("text", ""),
                            height=300,
                            disabled=True,
                        )
                    with st.expander("View Raw JSON"):
                        st.json(doc_data)
                except Exception as exc:
                    st.error(f"Error loading document JSON: {exc}")

    # Tab 2: Chunks
    with tab_chunks:
        chunk_files = get_chunk_files(settings.project_root)
        if not chunk_files:
            st.info("No chunks found in `data/chunks/`.")
        else:
            selected_chunk_file = st.selectbox(
                "Select a chunk file:",
                chunk_files,
                format_func=lambda p: p.stem,
                key="select_chunk_file",
            )
            if selected_chunk_file:
                try:
                    lines = [
                        line.strip()
                        for line in selected_chunk_file.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ]
                    chunks = [json.loads(line) for line in lines]
                    st.metric("Total Chunks in File", len(chunks))

                    # Show preview of chunks
                    for i, chunk in enumerate(chunks):
                        with st.expander(
                            f"#{i+1}: [{chunk.get('section', 'General')}] {chunk.get('chunk_id')}"
                        ):
                            st.markdown(f"**Section:** {chunk.get('section', 'N/A')}")
                            st.markdown(f"**URL:** {chunk.get('source_url', 'N/A')}")
                            st.markdown(f"**Fetched At:** {chunk.get('fetched_at', 'N/A')}")
                            st.text_area(
                                "Chunk Text",
                                chunk.get("text", ""),
                                height=100,
                                key=f"chunk_txt_{i}_{selected_chunk_file.stem}",
                                disabled=True,
                            )
                except Exception as exc:
                    st.error(f"Error reading chunk file: {exc}")

    # Tab 3: Index Status
    with tab_index:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Indexed Chunks", chroma_count)
        c2.metric("Vector Metric", "Cosine Distance")
        c3.metric("Top-k Retrieval", settings.retrieval_top_k)
        c4.metric("Min Score Threshold", settings.retrieval_min_score)

        st.markdown(f"**Embedding Model:** `{settings.embedding_model}`")
        st.markdown(f"**Chroma Collection:** `{settings.chroma_collection}`")
        st.markdown(f"**Chroma Persist Directory:** `{settings.chroma_persist_dir}`")
        st.markdown(f"**Configured LLM Provider:** `{settings.llm_provider}`")

        # Display registered sources from sources/urls.csv
        st.write("##### Registered Sources (`sources/urls.csv`):")
        if settings.sources_path.is_file():
            try:
                with settings.sources_path.open("r", encoding="utf-8", newline="") as h:
                    reader = csv.DictReader(h)
                    rows = list(reader)
                st.dataframe(rows, use_container_width=True)
            except Exception as exc:
                st.error(f"Could not load sources CSV: {exc}")
        else:
            st.info("Sources CSV file not found.")

    # Tab 4: Last Retrieval Trace
    with tab_trace:
        trace_file, trace_data = get_latest_retrieval_trace(settings.project_root)
        if not trace_file or not trace_data:
            st.info("No retrieval traces found in `data/retrieval/`.")
        else:
            st.caption(f"Trace File: `{trace_file.name}`")
            t_col1, t_col2, t_col3 = st.columns(3)
            t_col1.metric("Status", trace_data.get("status", "N/A"))
            t_col2.metric("Intent", trace_data.get("intent", "N/A"))
            t_col3.metric("Asked At (UTC)", trace_data.get("asked_at", "N/A"))

            st.markdown(f"**Query:** {trace_data.get('question', 'N/A')}")
            if trace_data.get("question_redacted"):
                st.badge("PII Redacted", type="warning")

            citation = trace_data.get("citation_url")
            if citation:
                st.markdown(f"**Citation Candidate:** [{citation}]({citation})")
            st.markdown(f"**Last Updated from Sources:** {trace_data.get('last_updated_from_sources') or 'N/A'}")

            hits = trace_data.get("hits") or []
            st.write(f"##### Retrieved Hits ({len(hits)}):")
            for idx, hit in enumerate(hits, start=1):
                score = hit.get("score", 0.0)
                sec = hit.get("section", "Section")
                cid = hit.get("chunk_id", "N/A")
                with st.expander(f"Hit #{idx}: Score {score:.4f} | [{sec}] {cid}"):
                    st.markdown(f"**Source URL:** {hit.get('source_url')}")
                    st.text_area(
                        "Hit Text",
                        hit.get("text", ""),
                        height=100,
                        key=f"trace_hit_{idx}",
                        disabled=True,
                    )

            with st.expander("Raw Trace JSON"):
                st.json(trace_data)


def main() -> None:
    st.set_page_config(
        page_title="NextLeap — Mutual Fund FAQ Assistant",
        page_icon="📈",
        layout="wide",
    )

    settings = load_settings()

    # --- Admin / Developer Mode Detection ---
    # Can be toggled via ?admin=true query parameter or via the sidebar expander
    query_admin = st.query_params.get("admin", "").lower() in ("true", "1", "yes")
    is_admin = query_admin or st.session_state.get("admin_mode", False)

    # --- Sidebar: Clean brand info + Discreet Admin Access ---
    with st.sidebar:
        st.markdown("### NextLeap")
        st.caption("Facts-Only HDFC Mutual Fund Assistant")
        st.markdown("---")
        if is_admin:
            st.success("🛠️ Admin Mode Active")
            if st.button("Exit Admin Mode", use_container_width=True):
                if "admin" in st.query_params:
                    del st.query_params["admin"]
                st.session_state["admin_mode"] = False
                st.rerun()
        else:
            with st.expander("Admin Access"):
                admin_pass = st.text_input(
                    "Passcode:", type="password", key="admin_pwd_input"
                )
                if st.button("Unlock Dashboard", key="btn_unlock_admin", use_container_width=True):
                    expected_pass = os.getenv("ADMIN_PASSCODE", "admin")
                    if admin_pass == expected_pass:
                        st.session_state["admin_mode"] = True
                        st.rerun()
                    else:
                        st.error("Invalid passcode.")

    # --- Customer-Facing Interface Header (PRD §10, implementation.md) ---
    st.title("📈 NextLeap: Mutual Fund FAQ Assistant")
    st.info("⚠️ **Facts-only. No investment advice.**")
    st.markdown(
        "Ask factual questions about the listed HDFC Mutual Fund schemes. "
        "Answers come from official public pages and always include a source link."
    )

    # Check ChromaDB readiness
    chroma_ok, chroma_count, chroma_status_desc = check_chroma_status(settings)
    if not chroma_ok:
        st.warning(
            "⚠️ **ChromaDB index is empty or unavailable.**\n\n"
            f"Status: {chroma_status_desc}\n\n"
            "Please run the ingestion pipeline to build the index:\n"
            "```bash\npython -m src.ingest.run\n```"
        )

    # --- Example Questions (Click-to-fill) ---
    st.write("##### Example Questions (click to fill):")
    cols = st.columns(len(EXAMPLE_QUESTIONS))
    for idx, question_text in enumerate(EXAMPLE_QUESTIONS):
        with cols[idx]:
            if st.button(question_text, key=f"btn_ex_{idx}", use_container_width=True):
                st.session_state["question_input"] = question_text
                st.session_state["auto_submit"] = True

    # User input
    current_value = st.session_state.get("question_input", "")
    user_query = st.text_input(
        "Your Question:",
        value=current_value,
        key="query_text_box",
        placeholder="e.g. What is the exit load of HDFC Large Cap Fund Direct Growth?",
    )

    submit_clicked = st.button("Submit Question", type="primary")
    auto_submit = st.session_state.pop("auto_submit", False)

    if submit_clicked or auto_submit:
        active_query = user_query.strip()
        if not active_query:
            st.warning("Please enter a question to ask.")
        elif not chroma_ok and chroma_count == 0:
            st.error(
                "Cannot execute query: ChromaDB is empty. "
                "Please run ingestion first using `python -m src.ingest.run`."
            )
        else:
            with st.spinner("Processing question through safety, retrieval, and generation..."):
                try:
                    result = answer_question(active_query, settings=settings)
                    st.session_state["last_result"] = result
                    st.session_state["last_error"] = None
                except GenerationError as exc:
                    st.session_state["last_result"] = None
                    st.session_state["last_error"] = (
                        f"LLM Generation Error: {exc}\n\n"
                        "Note: Ensure GROQ_API_KEY and LLM_MODEL are set in your `.env` file or Streamlit Secrets."
                    )
                except Exception as exc:
                    st.session_state["last_result"] = None
                    st.session_state["last_error"] = f"Query Pipeline Error ({type(exc).__name__}): {exc}"

    # Display Answer / Result (Customer-facing)
    if st.session_state.get("last_error"):
        st.error(st.session_state["last_error"])

    last_res = st.session_state.get("last_result")
    if last_res:
        st.markdown("### Answer")
        refused = bool(last_res.get("refused"))
        reason = last_res.get("refusal_reason")

        if refused:
            st.warning(f"🛡️ **Refusal Policy Triggered ({reason or 'safety'})**")
            st.markdown(last_res.get("text", ""))
            citation = last_res.get("citation_url")
            if citation:
                st.markdown(f"**Official Educational Page:** [{citation}]({citation})")
        else:
            st.success(last_res.get("text", ""))
            citation = last_res.get("citation_url")
            if citation:
                st.markdown(f"🔗 **Citation:** [{citation}]({citation})")
            updated = last_res.get("last_updated_from_sources")
            if updated:
                st.caption(f"📅 Last updated from sources: {updated}")
            else:
                st.caption("📅 Last updated from sources: N/A")

    # --- Developer / Admin Inspection Dashboard ---
    # Only displayed when admin mode is activated (via ?admin=true or sidebar passcode)
    if is_admin:
        render_inspection_dashboard(settings, chroma_count)


if __name__ == "__main__":
    main()
