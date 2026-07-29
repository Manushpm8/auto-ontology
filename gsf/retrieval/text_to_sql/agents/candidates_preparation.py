# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Candidate Preparation Agent

This agent prepares and fetches all candidates needed for SQL construction.
It runs before SQL generation agents to gather all necessary context.

Responsibilities:
- Fetch relevant tables from candidates
- Filter tables by LLM-based relevance check
- Retrieve relevant queries for context
- Filter and process complex candidates (custom analyses)
- Store all prepared data in path_state for downstream agents

Design Decisions:
- Runs before SQL generation to separate data fetching from SQL construction logic
- Stores fetched data in path_state for reusability across multiple SQL agents
- Handles embeddings and conversation history lookup
- LLM relevance filter removes noise tables before SQL construction
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.dal.attributes import fetch_attr_column_contexts, find_join_path
from gsf.dal.custom_analyses import (
    fetch_custom_analyses_with_sql,
    fetch_tables_from_custom_analyses,
)
from gsf.dal.datasources import (
    fetch_fk_neighbour_tables,
    fetch_table_by_name,
    fetch_tables_by_ids,
)
from gsf.dal.foreign_keys import get_relevant_fks
from gsf.dal.sql_attributes import (
    fetch_sql_attributes_with_sql,
    fetch_tables_from_sql_attributes,
)
from gsf.dal.terms import fetch_term_synonyms
from gsf.retrieval.data_access.relevant_tables import (
    dedupe_merge_relevant_tables,
    get_relevant_tables,
    get_relevant_tables_from_candidates,
)
from gsf.retrieval.text_to_sql.base import BaseAgent
from gsf.retrieval.text_to_sql.evidence_hints import (
    evidence_retrieval_phrases,
    evidence_table_name_hints,
    extract_evidence,
)
from gsf.retrieval.text_to_sql.few_shot import fetch_similar_questions
from gsf.retrieval.text_to_sql.models import (
    AnchorColumnModel,
    CustomAnalysisRelevanceModel,
    TableRelevanceModel,
)
from gsf.retrieval.text_to_sql.prompts import (
    CUSTOM_ANALYSIS_RELEVANCE_FILTER_PROMPT,
    TABLE_RELEVANCE_FILTER_PROMPT,
)
from gsf.retrieval.text_to_sql.state import (
    AgentState,
    get_original_question,
    get_question_for_processing,
    rules_to_text,
)
from gsf.utils.llm_invoke import invoke_with_structured_output


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


# Extra table vector-search budget (was hard-coded ~5).
_TABLE_SEARCH_K = _env_int("BIRD_TABLE_SEARCH_K", 12)
# Floor on hits fetched per search query, so adding entity/evidence queries
# cannot make each individual search shallower than this.
_TABLE_SEARCH_MIN_K = _env_int("BIRD_TABLE_SEARCH_MIN_K", 5)
_TABLE_SEARCH_CAP = _env_int("BIRD_TABLE_SEARCH_CAP", 20)
_TABLE_FILTER_ENABLED = _env_flag("BIRD_TABLE_FILTER", "1")
_EVIDENCE_FORCE_KEEP = _env_flag("BIRD_EVIDENCE_FORCE_KEEP_TABLES", "1")
# Add tables that sit one foreign key away from a table we already kept.
_FK_CLOSURE = _env_flag("BIRD_FK_CLOSURE", "1")
# Cap on tables added by FK closure, so a hub table cannot pull in a whole DB.
_FK_CLOSURE_MAX = _env_int("BIRD_FK_CLOSURE_MAX", 4)
# Ship the physical FK edges among the final tables to the generator, so join
# conditions come from the schema rather than from semantic path inference.
_PHYSICAL_FK_JOINS = _env_flag("BIRD_PHYSICAL_FK_JOINS", "0")
# Never describe an anchor attribute whose table is absent from the schema block.
_FORCE_ANCHOR_TABLE = _env_flag("BIRD_FORCE_ANCHOR_TABLE", "0")


def _qualified_name(t: dict) -> str:
    """Build schema-qualified table name (e.g. 'public.users') for dedup/filtering."""
    schema = t.get("schema_name", "")
    name = t.get("name", "")
    return f"{schema}.{name}" if schema else name


logger = logging.getLogger(__name__)


class CandidatePreparationAgent(BaseAgent):
    """
    Agent that prepares and fetches all candidates for SQL construction.

    This agent gathers all necessary context before SQL generation:
    - Relevant tables
    - Relevant queries for context
    - Similar questions from conversation history


    Output:
    - path_state["candidates"]: Flat list of candidate dicts (same as retrieved, enriched)
    - path_state["relevant_tables"]: Deduplicated list of relevant table dicts
        (same per-table dict shape as ``get_relevant_tables``)
    - path_state["relevant_queries"]: Relevant queries for context
    - path_state["similar_questions"]: Similar questions from history
    - path_state["custom_analyses"]: Filtered complex candidates
    - path_state["custom_analyses_str"]: String representation for prompts
    - path_state["sql_attributes"]: Retrieved SqlAttribute details
    - path_state["sql_attributes_str"]: String representation of SqlAttributes for prompts
    """

    def __init__(self):
        super().__init__("candidate_preparation")

    def validate_input(self, state: AgentState) -> bool:
        """Validate that retrieval produced at least one hit."""
        path_state = state.get("path_state", {})
        has_col_attrs = bool(path_state.get("retrieved_column_attributes"))
        has_custom = bool(path_state.get("retrieved_custom_analyses"))
        has_sql_attrs = bool(path_state.get("retrieved_sql_attributes"))
        if not has_col_attrs and not has_custom and not has_sql_attrs:
            connectors = state.get("connectors") or []
            if path_state.get("target_db") or (
                len(connectors) == 1 and getattr(connectors[0], "database_name", None)
            ):
                return True
            self.logger.warning(
                "No candidates for preparation: expected retrieved_column_attributes, "
                "retrieved_custom_analyses, or retrieved_sql_attributes in path_state"
            )
            return False
        return True

    def execute(self, state: AgentState) -> Dict[str, Any]:
        """
        Prepare and fetch all candidates for SQL construction.

        Gathers tables, queries, similar questions, and processes complex candidates.

        Args:
            state: Current agent state

        Returns:
            Dictionary with:
            - path_state: Contains all prepared candidate data
        """
        path_state = state.get("path_state", {})
        question = get_question_for_processing(state)
        original_question = get_original_question(state) or question
        evidence_phrases = evidence_retrieval_phrases(original_question)
        evidence_table_hints = evidence_table_name_hints(original_question)
        target_db = path_state.get("target_db")
        if not target_db:
            connectors = state.get("connectors") or []
            if len(connectors) == 1:
                target_db = getattr(connectors[0], "database_name", None)
        custom_analyses = list(path_state.get("retrieved_custom_analyses") or [])
        column_attributes = list(path_state.get("retrieved_column_attributes") or [])
        sql_attributes_raw = list(path_state.get("retrieved_sql_attributes") or [])
        candidates = custom_analyses + column_attributes + sql_attributes_raw

        # --- 1. Custom analyses ---
        self.logger.info("Retrieved %d custom analyses", len(custom_analyses))

        analysis_ids = [str(ca["id"]) for ca in custom_analyses if ca.get("id")]
        relevant_queries = fetch_custom_analyses_with_sql(analysis_ids)
        self.logger.info(
            "Found %d relevant queries from custom analyses", len(relevant_queries)
        )

        custom_analyses_str = self._build_custom_analyses_str(relevant_queries)

        # --- 2. Enrich ColumnAttributes with Neo4j context and build join paths ---
        primary_attribute: dict | None = None
        attribute_join_paths: list[dict] = []
        attr_contexts: dict[str, dict] = {}
        term_synonyms: dict[str, list[str]] = {}

        if column_attributes:
            attr_ids = [
                str(hit.get("id") or "") for hit in column_attributes if hit.get("id")
            ]
            attr_ids = list(dict.fromkeys(attr_ids))

            attr_contexts = fetch_attr_column_contexts(attr_ids)
            self.logger.info(
                "Fetched Neo4j context for %d/%d column attributes",
                len(attr_contexts),
                len(attr_ids),
            )
            term_synonyms = fetch_term_synonyms(attr_ids)
            self.logger.info("Fetched synonyms for %d term(s)", len(term_synonyms))

            anchor_id = self._identify_anchor(
                state,
                question,
                attr_contexts,
                preferred_order=[
                    str(hit.get("id") or "")
                    for hit in column_attributes
                    if hit.get("id")
                ],
                evidence_phrases=evidence_phrases,
            )
            self.logger.info("Anchor attribute id: %s", anchor_id)

            if anchor_id and anchor_id in attr_contexts:
                anchor_ctx = attr_contexts[anchor_id]
                primary_attribute = {
                    "id": anchor_id,
                    "attr_name": anchor_ctx["attr_name"],
                    "col_name": anchor_ctx["col_name"],
                    "table_name": anchor_ctx["table_name"],
                    "schema_name": anchor_ctx["schema_name"],
                }

                dest_items = [
                    (did, dctx)
                    for did, dctx in attr_contexts.items()
                    if did != anchor_id
                ]
                with ThreadPoolExecutor(max_workers=len(dest_items) or 1) as pool:
                    futures = {
                        pool.submit(
                            find_join_path, anchor_ctx["col_id"], dctx["col_id"]
                        ): (did, dctx)
                        for did, dctx in dest_items
                    }
                    for future in as_completed(futures):
                        dest_id, dest_ctx = futures[future]
                        join_path = future.result()
                        attribute_join_paths.append(
                            {
                                "id": dest_id,
                                "attr_name": dest_ctx["attr_name"],
                                "col_name": dest_ctx["col_name"],
                                "table_name": dest_ctx["table_name"],
                                "schema_name": dest_ctx["schema_name"],
                                "path": join_path,
                            }
                        )
                        self.logger.info(
                            "Join path to %s (%s): %d hop(s)",
                            dest_ctx["attr_name"],
                            dest_id,
                            len(join_path),
                        )
            else:
                self.logger.warning(
                    "No valid anchor attribute found — skipping join path computation"
                )

        # --- 4. Retrieve relevant tables ---
        relevant_tables = get_relevant_tables_from_candidates(candidates)

        if attr_contexts:
            ca_table_ids = list(
                dict.fromkeys(
                    ctx["table_id"]
                    for ctx in attr_contexts.values()
                    if ctx.get("table_id")
                )
            )
            ca_tables = fetch_tables_by_ids(ca_table_ids)
            existing_ids = {t.get("id") for t in relevant_tables}
            for tbl in ca_tables:
                if tbl.get("id") not in existing_ids:
                    relevant_tables.append(tbl)
                    existing_ids.add(tbl.get("id"))

        self.logger.info(
            "Tables from candidates: %s", [t["name"] for t in relevant_tables]
        )

        additional_tables = []
        # Prefer original (with Evidence) + evidence phrases so table search
        # is not blind to BIRD evidence table/column routing.
        search_queries: list[str] = []
        for q in [
            original_question,
            question,
            *path_state.get("entities", []),
            *evidence_phrases,
            *evidence_table_hints,
        ]:
            q = (q or "").strip()
            if q and q not in search_queries:
                search_queries.append(q)
        # Dividing the budget across search queries meant every extra entity or
        # evidence phrase made each individual search shallower: with the usual
        # 5-7 queries this floored at k=2 for 87% of questions, so a gold table
        # ranked 3rd for every query was never retrieved. Search each query to a
        # fixed depth instead and let the dedupe + relevance filter downstream
        # do the narrowing.
        k_per_query = max(
            _TABLE_SEARCH_MIN_K, _TABLE_SEARCH_K // max(1, len(search_queries))
        )

        def _fetch_tables_for_query(query: str) -> list[dict]:
            return get_relevant_tables(
                state["data_retriever"],
                query,
                k=k_per_query,
                database_name=target_db,
            )

        with ThreadPoolExecutor(max_workers=len(search_queries)) as pool:
            futures = {
                pool.submit(_fetch_tables_for_query, q): q for q in search_queries
            }
            for future in as_completed(futures):
                query = futures[future]
                try:
                    additional_tables.extend(future.result())
                except Exception:
                    self.logger.warning(
                        "Table retrieval failed for query: %s", query, exc_info=True
                    )
        additional_tables = dedupe_merge_relevant_tables(additional_tables)[
            :_TABLE_SEARCH_CAP
        ]
        seen_qnames: set[str] = set()
        deduped_tables: list[dict] = []
        for t in relevant_tables + additional_tables:
            qn = _qualified_name(t).lower()
            if qn in seen_qnames:
                continue
            seen_qnames.add(qn)
            deduped_tables.append(t)
        relevant_tables = deduped_tables

        # Force-inject evidence-named tables (exact Neo4j name lookup + vector).
        force_kept: list[str] = []
        if _EVIDENCE_FORCE_KEEP and evidence_table_hints:
            existing_ids = {t.get("id") for t in relevant_tables}
            existing_names = {(t.get("name") or "").lower() for t in relevant_tables}
            for hint in evidence_table_hints:
                if hint.lower() in existing_names:
                    continue
                row = fetch_table_by_name(hint, database_name=target_db)
                added = False
                if row and row.get("id") and row.get("id") not in existing_ids:
                    relevant_tables.append(
                        {
                            "id": row.get("id"),
                            "name": row.get("name") or hint,
                            "description": row.get("description") or "",
                            "schema_name": row.get("schema_name") or "",
                            "label": "Table",
                            "columns": row.get("columns") or [],
                        }
                    )
                    existing_ids.add(row.get("id"))
                    existing_names.add((row.get("name") or hint).lower())
                    force_kept.append(_qualified_name(relevant_tables[-1]))
                    added = True
                if not added:
                    try:
                        hits = get_relevant_tables(
                            state["data_retriever"],
                            hint,
                            k=3,
                            database_name=target_db,
                        )
                    except Exception:
                        hits = []
                    for hit in hits:
                        hname = (hit.get("name") or "").lower()
                        if hname != hint.lower():
                            continue
                        if hit.get("id") in existing_ids:
                            continue
                        relevant_tables.append(hit)
                        existing_ids.add(hit.get("id"))
                        existing_names.add(hname)
                        force_kept.append(_qualified_name(hit))
                        break
            if force_kept:
                self.logger.info(
                    "Evidence force-kept table(s): %s (hints=%s)",
                    force_kept,
                    evidence_table_hints,
                )

        self.logger.info(
            "Found %d relevant tables (after dedupe, capped at %d): %s",
            len(relevant_tables),
            _TABLE_SEARCH_CAP,
            [_qualified_name(t) for t in relevant_tables],
        )

        # --- 4b. Add tables referenced by custom analyses via Neo4j ---
        if custom_analyses:
            ca_ids = [str(ca["id"]) for ca in custom_analyses if ca.get("id")]
            ca_linked_tables = fetch_tables_from_custom_analyses(ca_ids)
            existing_ids = {t.get("id") for t in relevant_tables}
            added = 0
            for tbl in ca_linked_tables:
                if tbl.get("id") not in existing_ids:
                    relevant_tables.append(tbl)
                    existing_ids.add(tbl.get("id"))
                    added += 1
            self.logger.info(
                "Added %d table(s) from custom analyses SQL references: %s",
                added,
                [t["name"] for t in ca_linked_tables],
            )

        # --- 4c. Enrich SqlAttributes with SQL + term from Neo4j ---
        sql_attributes: list[dict] = []
        if sql_attributes_raw:
            sa_ids = [
                str(hit.get("id") or "") for hit in sql_attributes_raw if hit.get("id")
            ]
            sa_ids = list(dict.fromkeys(sa_ids))
            sql_attributes = fetch_sql_attributes_with_sql(sa_ids)
            self.logger.info(
                "Fetched %d/%d SqlAttribute details from Neo4j",
                len(sql_attributes),
                len(sa_ids),
            )

            sa_linked_tables = fetch_tables_from_sql_attributes(sa_ids)
            existing_ids = {t.get("id") for t in relevant_tables}
            added = 0
            added_names: list[str] = []
            for tbl in sa_linked_tables:
                if tbl.get("id") not in existing_ids:
                    relevant_tables.append(tbl)
                    existing_ids.add(tbl.get("id"))
                    added += 1
                    added_names.append(_qualified_name(tbl))
            self.logger.info(
                "Added %d table(s) from SqlAttribute SQL references: %s",
                added,
                [t["name"] for t in sa_linked_tables],
            )

        sql_attributes_str = self._build_sql_attributes_str(sql_attributes)

        # --- 4d. Guarantee the anchor's own table is available ---
        # The prompt names an anchor column; omitting its table leaves the
        # generator with a hint it cannot act on.
        anchor_table = str((primary_attribute or {}).get("table_name") or "")
        if _FORCE_ANCHOR_TABLE and anchor_table:
            have = {
                (t.get("name") or "").split(".")[-1].lower() for t in relevant_tables
            }
            if anchor_table.split(".")[-1].lower() not in have:
                found = fetch_table_by_name(anchor_table, target_db)
                if found:
                    relevant_tables.append(found)
                    self.logger.info(
                        "Added anchor table missing from retrieval: %s", anchor_table
                    )

        # --- 5. Filter tables by relevance ---
        force_keep = {
            (t.get("name") or "").lower()
            for t in relevant_tables
            if (t.get("name") or "").lower()
            in {h.lower() for h in evidence_table_hints}
        } | {
            h.lower()
            for h in evidence_table_hints
            # only protect hints that resolved to a real in-DB table above
            if any((t.get("name") or "").lower() == h.lower() for t in relevant_tables)
        }
        if _FORCE_ANCHOR_TABLE and anchor_table:
            force_keep.add(anchor_table.split(".")[-1].lower())

        relevant_tables, table_relevance_reasoning = self._filter_tables_by_relevance(
            state,
            question,
            relevant_tables,
            custom_analyses,
            original_question=original_question,
            force_keep_names=force_keep,
        )
        self.logger.info(
            "Kept %d relevant tables (after relevance filter): %s",
            len(relevant_tables),
            [_qualified_name(t) for t in relevant_tables],
        )

        # --- 5b. Pull in FK neighbours of the tables we kept ---
        # 77.8% of gold tables missing from the prompt sat one foreign key away
        # from a table that *was* retrieved, so closing over FK edges recovers
        # most of them while adding far fewer tables than the whole schema.
        if _FK_CLOSURE and target_db:
            before_names = [_qualified_name(t) for t in relevant_tables]
            relevant_tables = self._expand_by_fk(relevant_tables, target_db)
            added = [
                _qualified_name(t)
                for t in relevant_tables
                if _qualified_name(t) not in before_names
            ]
            if added:
                self.logger.info("FK closure added table(s): %s", added)

        # --- 5c. Physical FK edges among the final tables ---
        # Join conditions the model can trust: these come from the schema, not
        # from walking the semantic graph.
        verified_fks: list[dict] = []
        if _PHYSICAL_FK_JOINS:
            table_ids = [t.get("id") for t in relevant_tables if t.get("id")]
            if table_ids:
                try:
                    verified_fks = get_relevant_fks(table_ids)
                except Exception:
                    self.logger.warning("get_relevant_fks failed", exc_info=True)
                    verified_fks = []
            self.logger.info(
                "Verified FK edges among prompt tables: %d", len(verified_fks)
            )

        # --- 6. Cross-database Train few-shot Q→SQL demos ---
        retrieved_questions = fetch_similar_questions(question)
        # Preserve examples supplied by callers / conversation retrieval, then
        # append Train demos without introducing duplicate Q→SQL pairs.
        similar_questions = []
        seen_examples: set[tuple[str, str]] = set()
        for example in [
            *(path_state.get("similar_questions") or []),
            *retrieved_questions,
        ]:
            if not isinstance(example, (list, tuple)) or len(example) < 2:
                continue
            pair = (str(example[0]).strip(), str(example[1]).strip())
            if not pair[0] or not pair[1] or pair in seen_examples:
                continue
            seen_examples.add(pair)
            similar_questions.append(pair)

        return {
            "path_state": {
                **path_state,
                "relevant_tables": relevant_tables,
                "relevant_queries": [
                    r["sql"] for r in relevant_queries if r.get("sql")
                ],
                "similar_questions": similar_questions,
                "custom_analyses": custom_analyses,
                "custom_analyses_str": custom_analyses_str,
                "verified_fks": verified_fks,
                "sql_attributes": sql_attributes,
                "sql_attributes_str": sql_attributes_str,
                "table_relevance_reasoning": table_relevance_reasoning,
                "primary_attribute": primary_attribute,
                "attribute_join_paths": attribute_join_paths,
                "term_synonyms": term_synonyms,
            }
        }

    def _filter_custom_analyses_by_relevance(
        self,
        state: AgentState,
        question: str,
        analyses: list[dict],
    ) -> list[dict]:
        """Use the LLM to decide which retrieved custom analyses are relevant."""
        if len(analyses) <= 1:
            return analyses

        try:
            llm = state["llm"]
        except KeyError:
            self.logger.warning(
                "No LLM in state — skipping custom analysis relevance filter"
            )
            return analyses

        analyses_summary = "\n".join(
            f"- {a.get('name', '(unnamed)')}: "
            f"{(a.get('description') or '(no description)').strip()}"
            f"{('  SQL: ' + a['sql'].strip()) if a.get('sql') else ''}"
            for a in analyses
        )

        prompt_text = CUSTOM_ANALYSIS_RELEVANCE_FILTER_PROMPT.format(
            question=question,
            analyses_summary=analyses_summary,
        )

        messages = [
            SystemMessage(
                content="You are a database domain expert that filters custom analyses."
            ),
            HumanMessage(content=prompt_text),
        ]

        try:
            result = invoke_with_structured_output(
                llm, messages, CustomAnalysisRelevanceModel
            )
        except Exception as e:
            self.logger.warning(
                "Custom analysis relevance LLM call failed: %s — keeping all",
                e,
            )
            return analyses

        if result is None:
            self.logger.warning(
                "Custom analysis relevance filter returned None — keeping all"
            )
            return analyses

        names_to_remove = {name.lower() for name in result.analyses_to_remove}

        filtered = [
            a for a in analyses if (a.get("name") or "").lower() not in names_to_remove
        ]
        removed = [
            a.get("name")
            for a in analyses
            if (a.get("name") or "").lower() in names_to_remove
        ]

        reasoning = (result.reasoning or "").strip()
        self.logger.info(
            "Custom analysis filter reasoning: %s",
            reasoning if reasoning else "(empty)",
        )
        if removed:
            self.logger.info("Custom analysis filter removed: %s", removed)
        self.logger.info(
            "Custom analysis filter kept: %s", [a.get("name") for a in filtered]
        )

        if not filtered:
            self.logger.warning(
                "Custom analysis filter removed ALL analyses — keeping all"
            )
            return analyses

        return filtered

    def _expand_by_fk(self, tables: list[dict], target_db: str) -> list[dict]:
        """Append tables one foreign key away from *tables*.

        Runs after the relevance filter so the LLM's choices seed the expansion
        rather than compete with it. Capped at ``_FK_CLOSURE_MAX`` additions to
        keep a hub table from dragging in the whole database.
        """
        names = [(t.get("name") or "") for t in tables if t.get("name")]
        if not names:
            return tables
        neighbours = fetch_fk_neighbour_tables(target_db, names)
        if not neighbours:
            return tables
        existing = {t.get("id") for t in tables}
        added = 0
        for tbl in neighbours:
            if added >= _FK_CLOSURE_MAX:
                break
            if tbl.get("id") in existing:
                continue
            tables.append(tbl)
            existing.add(tbl.get("id"))
            added += 1
        return tables

    def _filter_tables_by_relevance(
        self,
        state: AgentState,
        question: str,
        tables: list[dict],
        custom_analyses: list[dict] | None = None,
        *,
        original_question: str | None = None,
        force_keep_names: set[str] | None = None,
    ) -> tuple[list[dict], str]:
        """Use the LLM to decide which candidate tables are actually needed."""
        if not _TABLE_FILTER_ENABLED:
            return tables, "BIRD_TABLE_FILTER=0"
        if len(tables) <= 2:
            return tables, ""

        force_keep_names = {n.lower() for n in (force_keep_names or set()) if n}
        try:
            llm = state["llm"]
        except KeyError:
            self.logger.warning("No LLM in state — skipping relevance filter")
            return tables, ""

        tables_summary = "\n".join(
            f"- {_qualified_name(t)}: {t.get('description', '(no description)')}"
            for t in tables
        )

        domain_rules_text = rules_to_text(state.get("domain_rules", []))
        domain_rules_section = ""
        if domain_rules_text:
            domain_rules_section = (
                "Domain-specific rules (use these to decide relevance):\n"
                f"{domain_rules_text}\n"
            )

        evidence = extract_evidence(original_question or question)
        if evidence:
            domain_rules_section += (
                "Evidence from the user question (MUST keep any table named here):\n"
                f"{evidence[:800]}\n"
            )
        if force_keep_names:
            domain_rules_section += (
                "Force-keep table names (never remove these): "
                + ", ".join(sorted(force_keep_names))
                + "\n"
            )

        ca_section = ""
        if custom_analyses:
            ca_lines = []
            for a in custom_analyses:
                line = f"- {a.get('name', '(unnamed)')}"
                desc = (a.get("description") or "").strip()
                if desc:
                    line += f": {desc}"
                sql = (a.get("sql") or "").strip()
                if sql:
                    line += f"  SQL: {sql}"
                ca_lines.append(line)
            ca_section = (
                "Selected custom analyses (their SQL references tables that MUST be kept):\n"
                + "\n".join(ca_lines)
                + "\n\n"
            )

        prompt_text = TABLE_RELEVANCE_FILTER_PROMPT.format(
            question=original_question or question,
            tables_summary=tables_summary,
            domain_rules=domain_rules_section,
            custom_analyses=ca_section,
        )

        messages = [
            SystemMessage(
                content="You are a database schema expert that filters candidate tables."
            ),
            HumanMessage(content=prompt_text),
        ]

        try:
            result = invoke_with_structured_output(llm, messages, TableRelevanceModel)
        except Exception as e:
            top_n = tables[:10]
            self.logger.warning(
                "Table relevance LLM call failed: %s — falling back to top %d/%d tables",
                e,
                len(top_n),
                len(tables),
            )
            return top_n, ""

        if result is None:
            top_n = tables[:10]
            self.logger.warning(
                "Table relevance filter returned None (LLM parsing failed). "
                "Falling back to top %d/%d tables by retrieval order. "
                "Check ERROR logs above for parsing/validation details.",
                len(top_n),
                len(tables),
            )
            return top_n, ""

        reasoning = (result.reasoning or "").strip()
        names_to_remove = {name.lower() for name in result.tables_to_remove}
        # Never drop evidence-force-kept tables (match bare or qualified).
        protected = set()
        for t in tables:
            bare = (t.get("name") or "").lower()
            qn = _qualified_name(t).lower()
            if bare in force_keep_names or qn in force_keep_names:
                protected.add(qn)
                names_to_remove.discard(qn)
                names_to_remove.discard(bare)

        filtered = [
            t for t in tables if _qualified_name(t).lower() not in names_to_remove
        ]
        removed = [
            _qualified_name(t)
            for t in tables
            if _qualified_name(t).lower() in names_to_remove
        ]

        self.logger.info(
            "Relevance filter reasoning: %s", reasoning if reasoning else "(empty)"
        )
        if removed:
            self.logger.info("Relevance filter removed tables: %s", removed)

        if not filtered:
            self.logger.warning("Relevance filter removed ALL tables — keeping all")
            return tables, reasoning

        return filtered, reasoning

    def _identify_anchor(
        self,
        state: "AgentState",
        question: str,
        contexts: dict[str, dict],
        preferred_order: list[str] | None = None,
        evidence_phrases: list[str] | None = None,
    ) -> str | None:
        """Use the LLM to pick the primary (anchor) ColumnAttribute for the question."""
        ids = list(contexts.keys())
        if not ids:
            return None
        if len(ids) == 1:
            return ids[0]

        # Prefer retrieval/rerank order over arbitrary dict insertion order.
        ranked: list[str] = []
        for aid in preferred_order or []:
            if aid in contexts and aid not in ranked:
                ranked.append(aid)
        for aid in ids:
            if aid not in ranked:
                ranked.append(aid)

        # Soft bias: if evidence names a column/table matching an attribute, prefer it.
        evidence_boost: list[str] = []
        phrases = [p.lower() for p in (evidence_phrases or []) if p]
        if phrases:
            for aid in ranked:
                ctx = contexts[aid]
                hay = " ".join(
                    [
                        str(ctx.get("attr_name") or ""),
                        str(ctx.get("col_name") or ""),
                        str(ctx.get("table_name") or ""),
                    ]
                ).lower()
                if any(p in hay or hay.find(p) >= 0 for p in phrases):
                    evidence_boost.append(aid)
            if evidence_boost:
                ranked = evidence_boost + [a for a in ranked if a not in evidence_boost]

        fallback_id = ranked[0]

        try:
            llm = state["llm"]
        except KeyError:
            self.logger.warning(
                "_identify_anchor: no LLM in state — using retrieval-ranked attribute"
            )
            return fallback_id

        attrs_block = "\n".join(
            f"- id: {aid} | {contexts[aid]['attr_name']} "
            f"(table: {contexts[aid].get('table_name', '?')}, "
            f"column: {contexts[aid].get('col_name', '?')})"
            + (
                f" — {contexts[aid]['attr_description']}"
                if contexts[aid].get("attr_description")
                else ""
            )
            for aid in ranked
        )
        evidence_note = ""
        if phrases:
            evidence_note = (
                "Evidence phrases (prefer attributes matching these): "
                + ", ".join(phrases[:8])
                + "\n\n"
            )
        messages = [
            SystemMessage(
                content=(
                    "You are identifying the primary column attribute that is the main focus "
                    "of a user's analytical question."
                )
            ),
            HumanMessage(
                content=(
                    f"Question: {question}\n\n"
                    f"{evidence_note}"
                    f"Available column attributes (best retrieval matches first):\n"
                    f"{attrs_block}\n\n"
                    "Return the id of the single column attribute that best represents "
                    "the primary subject of the question."
                )
            ),
        ]

        try:
            result = invoke_with_structured_output(
                llm.bind(max_tokens=1024), messages, AnchorColumnModel
            )
        except Exception:
            self.logger.warning(
                "_identify_anchor: LLM call failed — using retrieval-ranked attribute",
                exc_info=True,
            )
            return fallback_id

        if result and result.anchor_id and result.anchor_id in contexts:
            self.logger.info(
                "Anchor column identified: %s (%s)", result.anchor_id, result.reasoning
            )
            return result.anchor_id

        self.logger.warning(
            "_identify_anchor: LLM returned invalid id — using retrieval-ranked attribute"
        )
        return fallback_id

    def _build_custom_analyses_str(self, relevant_queries: list[dict]) -> list[str]:
        """Build string representation of custom analyses for prompts."""
        parts_list: list[str] = []
        for x in relevant_queries:
            name = (x.get("name") or "").strip()
            if not name:
                continue
            entry = f"name: {name}"
            desc = (x.get("description") or "").strip()
            if desc:
                entry += f", description: {desc}"
            sql = (x.get("sql") or "").strip()
            if sql:
                entry += f", sql: {sql}"
            parts_list.append(entry)
        return parts_list

    def _build_sql_attributes_str(self, sql_attributes: list[dict]) -> list[str]:
        """Build string representation of sql attributes for prompts."""
        parts_list: list[str] = []
        for x in sql_attributes:
            name = (x.get("name") or "").strip()
            if not name:
                continue
            entry = f"name: {name}"
            desc = (x.get("description") or "").strip()
            if desc:
                entry += f", description: {desc}"
            expr = (x.get("expression") or "").strip()
            sql = (x.get("sql") or "").strip()
            if expr:
                entry += f", expression: {expr}"
            if sql and sql != expr:
                entry += f", full_query: {sql}"
            term = (x.get("term_name") or "").strip()
            if term:
                entry += f", term: {term}"
            parts_list.append(entry)
        return parts_list
