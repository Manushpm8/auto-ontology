"""Deterministic pattern detection (no LLM needed).

Scans a table's columns and foreign keys to detect:
1. Declared FK edges
2. Self-referential edges (FK pointing to own table)
3. Implicit FK patterns (*_id columns without declared FK)
4. Denormalized entity candidates (*_name, *_type, *_category, *_status)
5. PK column detection (explicit and implicit)
6. Auto-create Attribute nodes for all non-PK, non-FK columns
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from gsf.ontology.rigor.models import (
    DenormalizedCandidate,
    ObjectProperty,
    ProposedAttribute,
    Provenance,
)

logger = logging.getLogger(__name__)

_ID_SUFFIX_PATTERN = re.compile(r"^(.+?)_?id$", re.IGNORECASE)

_DENORM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(.+?)_name$", re.IGNORECASE), "*_name"),
    (re.compile(r"^(.+?)_type$", re.IGNORECASE), "*_type"),
    (re.compile(r"^(.+?)_category$", re.IGNORECASE), "*_category"),
    (re.compile(r"^(.+?)_status$", re.IGNORECASE), "*_status"),
]

_TEXT_TYPES = {"text", "varchar", "character varying", "char", "string"}


class DeterministicResult(BaseModel):
    """Output of deterministic analysis for one table."""

    edges: list[ObjectProperty] = Field(default_factory=list)
    denormalized_candidates: list[DenormalizedCandidate] = Field(default_factory=list)
    fk_column_names: set[str] = Field(
        default_factory=set,
        description="Columns consumed by FK/implicit-FK detection.",
    )
    pk_column_names: set[str] = Field(
        default_factory=set,
        description="Columns identified as primary keys.",
    )
    attributes: list[ProposedAttribute] = Field(
        default_factory=list,
        description="Auto-created attributes for non-FK columns (PKs included).",
    )


def to_term_name(table_name: str) -> str:
    """Convert a table name to CamelCase business term name.

    Examples: 'order_items' -> 'OrderItems', 'customers' -> 'Customers'
    """
    parts = re.split(r"[_\s]+", table_name)
    return "".join(p.capitalize() for p in parts if p)


def run_deterministic(
    table: dict[str, Any],
    ctx: dict[str, Any],
    all_table_names: list[str] | None = None,
) -> DeterministicResult:
    """Run all deterministic detections for a single table.

    Args:
        table: Table metadata dict (name, id, schema_name, etc.)
        ctx: Table context from fetch_table_context() with columns, fks, sqls
        all_table_names: All table names in the database for implicit FK matching
    """
    table_name = table["name"]
    term_name = to_term_name(table_name)
    fks = ctx.get("fks", [])
    columns = ctx.get("columns", [])

    result = DeterministicResult()

    fk_source_cols: set[str] = set()

    # 1. Declared FK edges
    for fk in fks:
        src_col = fk["source_column"]
        tgt_table = fk["target_table"]
        fk_source_cols.add(src_col)

        target_term = to_term_name(tgt_table)

        tgt_col = fk["target_column"]

        # 2. Self-referential detection
        if tgt_table == table_name:
            edge_name = _infer_self_ref_name(src_col)
            prov = Provenance(
                source_table=table_name,
                source_column=src_col,
                target_table=tgt_table,
                target_column=tgt_col,
                derivation="self_referential",
            )
            result.edges.append(
                ObjectProperty(
                    name=edge_name,
                    source_term=term_name,
                    target_term=term_name,
                    provenance=prov,
                )
            )
            logger.info(
                "  [det] Self-ref: %s.%s -> %s (%s)",
                table_name,
                src_col,
                table_name,
                edge_name,
            )
        else:
            prov = Provenance(
                source_table=table_name,
                source_column=src_col,
                target_table=tgt_table,
                target_column=tgt_col,
                derivation="declared_fk",
            )
            edge_name = _infer_fk_edge_name(src_col, tgt_table)
            result.edges.append(
                ObjectProperty(
                    name=edge_name,
                    source_term=term_name,
                    target_term=target_term,
                    provenance=prov,
                )
            )
            logger.info(
                "  [det] Declared FK: %s.%s -> %s (%s)",
                table_name,
                src_col,
                tgt_table,
                edge_name,
            )

    result.fk_column_names = fk_source_cols

    # 3. Implicit FK detection (*_id pattern)
    known_tables = set(all_table_names or [])
    for col in columns:
        col_name = col["name"]
        if col_name in fk_source_cols:
            continue

        match = _ID_SUFFIX_PATTERN.match(col_name)
        if not match:
            continue

        prefix = match.group(1).lower()
        matched_table = _find_table_match(prefix, known_tables, table_name)
        if matched_table:
            target_term = to_term_name(matched_table)
            prov = Provenance(
                source_table=table_name,
                source_column=col_name,
                target_table=matched_table,
                target_column=col_name,
                derivation="implicit_id_pattern",
            )
            edge_name = _infer_fk_edge_name(col_name, matched_table)
            result.edges.append(
                ObjectProperty(
                    name=edge_name,
                    source_term=term_name,
                    target_term=target_term,
                    provenance=prov,
                )
            )
            result.fk_column_names.add(col_name)
            logger.info(
                "  [det] Implicit FK: %s.%s -> %s (%s)",
                table_name,
                col_name,
                matched_table,
                edge_name,
            )

    # 4. PK detection
    pk_cols: set[str] = set()
    explicit_pk = table.get("pk")
    if explicit_pk:
        if isinstance(explicit_pk, list):
            pk_cols.update(explicit_pk)
        else:
            pk_cols.add(str(explicit_pk))

    if not pk_cols:
        col_names_lower = {c["name"].lower(): c["name"] for c in columns}
        if "id" in col_names_lower:
            pk_cols.add(col_names_lower["id"])
        else:
            implicit_pk = f"{table_name}_id"
            if implicit_pk.lower() in col_names_lower:
                pk_cols.add(col_names_lower[implicit_pk.lower()])

    result.pk_column_names = pk_cols
    if pk_cols:
        logger.info("  [det] PK columns: %s", pk_cols)

    # 5. Denormalized entity candidates
    for col in columns:
        col_name = col["name"]
        if col_name in result.fk_column_names:
            continue

        col_type = (col.get("data_type") or "").lower()
        if col_type not in _TEXT_TYPES:
            continue

        for pattern, label in _DENORM_PATTERNS:
            m = pattern.match(col_name)
            if m:
                entity = to_term_name(m.group(1))
                result.denormalized_candidates.append(
                    DenormalizedCandidate(
                        column_name=col_name,
                        inferred_entity_name=entity,
                        pattern=label,
                        column_type=col_type,
                    )
                )
                logger.info(
                    "  [det] Denormalized candidate: %s.%s -> %s (%s)",
                    table_name,
                    col_name,
                    entity,
                    label,
                )
                break

    # 6. Auto-create attributes for all non-FK columns (PKs included)
    skip_cols = result.fk_column_names
    for col in columns:
        col_name = col["name"]
        if col_name in skip_cols:
            continue
        result.attributes.append(
            ProposedAttribute(
                name=col_name,
                datatype=col.get("data_type") or "unknown",
                term_name=term_name,
                source_column=col_name,
                is_primary_key=col_name in result.pk_column_names,
            )
        )

    all_col_names = [c["name"] for c in columns]
    attr_names = [a.source_column for a in result.attributes]
    pk_attr_names = [a.source_column for a in result.attributes if a.is_primary_key]
    logger.info(
        "  [det] Columns: %d total -> %d attrs (%d PK), %d skipped (FK)",
        len(all_col_names),
        len(attr_names),
        len(pk_attr_names),
        len(result.fk_column_names),
    )
    if pk_attr_names:
        logger.info(
            "  [det]   PK attrs: %s",
            ", ".join(sorted(pk_attr_names)),
        )
    if result.fk_column_names:
        logger.info(
            "  [det]   Skipped (FK): %s",
            ", ".join(sorted(result.fk_column_names)),
        )
    if attr_names:
        logger.info("  [det]   Kept as attrs: %s", ", ".join(attr_names))

    return result


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------


def _infer_fk_edge_name(column_name: str, target_table: str) -> str:
    """Infer a relationship name from an FK column.

    E.g. 'customer_id' + 'customers' -> 'hasCustomer'
         'manager_id' + 'employees' -> 'hasManager'
    """
    m = _ID_SUFFIX_PATTERN.match(column_name)
    if m:
        prefix = m.group(1)
        parts = re.split(r"[_\s]+", prefix)
        camel = parts[0].lower() + "".join(p.capitalize() for p in parts[1:])
        return f"has{camel[0].upper()}{camel[1:]}"

    return f"relatesTo{to_term_name(target_table)}"


def _infer_self_ref_name(column_name: str) -> str:
    """Infer a name for a self-referential relationship.

    E.g. 'manager_id' -> 'reportsTo', 'parent_id' -> 'childOf'
    """
    col_lower = column_name.lower()
    if "manager" in col_lower:
        return "reportsTo"
    if "parent" in col_lower:
        return "childOf"
    if "supervisor" in col_lower:
        return "supervisedBy"

    m = _ID_SUFFIX_PATTERN.match(column_name)
    if m:
        prefix = m.group(1)
        parts = re.split(r"[_\s]+", prefix)
        camel = parts[0].lower() + "".join(p.capitalize() for p in parts[1:])
        return f"has{camel[0].upper()}{camel[1:]}"

    return "relatedToSelf"


def _find_table_match(
    prefix: str, known_tables: set[str], current_table: str
) -> str | None:
    """Try to match an *_id prefix to a known table name.

    Handles plural forms: 'customer' matches 'customers'.
    """
    if not known_tables:
        return None

    candidates = {t.lower(): t for t in known_tables if t != current_table}

    if prefix in candidates:
        return candidates[prefix]

    plural = prefix + "s"
    if plural in candidates:
        return candidates[plural]

    if prefix.endswith("s"):
        singular = prefix[:-1]
        if singular in candidates:
            return candidates[singular]

    if prefix.endswith("ies"):
        base = prefix[:-3] + "y"
        if base in candidates:
            return candidates[base]

    return None
