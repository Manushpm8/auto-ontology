"""Deterministic column mapping — exclude FK columns."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.semantic.constants import MAX_SAMPLE_VALUE_LEN
from gsf.semantic.models import ColumnAttributeSpec, ColumnDescriptionResult
from gsf.utils.llm_invoke import get_llm_client, invoke_with_structured_output

logger = logging.getLogger(__name__)

# Wide tables make the model emit one JSON object per column in a single
# structured-output response; large batches generate too many tokens and time
# out. Bucket columns into small batches and describe them concurrently so one
# slow/failed batch never wipes out the whole table's descriptions.
_DESCRIPTION_BATCH_SIZE = 15
_DESCRIPTION_MAX_WORKERS = 1

_DESCRIPTION_SYSTEM = """\
You are a data analyst documenting the columns of a relational table for a \
semantic layer. For every column you are given, write ONE concise sentence \
describing what the column represents in business terms.

Rules:
- Use the column name, data type, and sample values as evidence.
- Keep each description to a single, factual sentence — no speculation.
- Return exactly one entry per column provided, using the physical column name."""


def to_term_name(table_name: str) -> str:
    """CamelCase provisional Term from snake_case table name."""
    parts = re.split(r"[_\s]+", table_name.strip())
    return "".join(p.capitalize() for p in parts if p)


_DATE_TYPE_TOKENS = ("date", "time", "timestamp", "datetime")


def fk_source_columns(fks: list[dict[str, Any]]) -> set[str]:
    return {fk["source_column"] for fk in fks if fk.get("source_column")}


def _is_date_type(data_type: str | None) -> bool:
    """Whether a declared column type is a date/time type."""
    if not data_type:
        return False
    lowered = data_type.lower()
    return any(token in lowered for token in _DATE_TYPE_TOKENS)


def _get_column_samples(
    col: dict[str, Any],
    columns_profiling_samples: dict[str, dict[str, Any]],
) -> list[str]:
    """Profiled sample values for a column, short enough to belong in a prompt.

    Date/time columns are excluded — concrete dates add no business meaning
    (to an LLM description prompt, or as a stand-in when no description
    needed to be generated).

    Values over ``MAX_SAMPLE_VALUE_LEN`` are dropped, as the embedding and FK
    renderers already do. Skipping the cap here let a free-text column paste its
    whole first row into the description: one ``users.AboutMe`` value alone ran
    to 2.5k characters, crowding out the rest of the schema on the primary
    retrieval path, where the description is the only channel to the prompt.
    """
    if _is_date_type(col.get("data_type")):
        return []
    name = col.get("name", "")
    samples = (columns_profiling_samples.get(name) or {}).get("sample_values") or []
    return [str(s) for s in samples if len(str(s)) <= MAX_SAMPLE_VALUE_LEN]


def _is_exhaustive(
    col: dict[str, Any],
    columns_profiling_samples: dict[str, dict[str, Any]],
) -> bool:
    """Whether the values that survive filtering are the column's complete set.

    Dropping an over-length value leaves an incomplete list, which must stop
    being presented as the column's permitted values.
    """
    name = col.get("name", "")
    profile = columns_profiling_samples.get(name) or {}
    if not profile.get("exhaustive"):
        return False
    raw = profile.get("sample_values") or []
    return len(_get_column_samples(col, columns_profiling_samples)) == len(raw)


def _add_samples_suffix(text: str, samples: list[str], exhaustive: bool = False) -> str:
    """Append the profiled values to *text*, as a constraint when they are all of them.

    A closed enumeration is stated as "one of: ..." so the model treats it as the
    permitted set; an open sample stays "samples: ..." so it reads as examples and
    the model does not assume a value it needs is absent.
    """
    if not samples:
        return text
    label = "one of" if exhaustive else "samples"
    listed = f"{label}: {', '.join(samples)}"
    # A failed description batch leaves no text to append to; emit the values
    # alone rather than a dangling separator.
    return f"{text} — {listed}" if text else listed


def _describe_column_batch(
    columns: list[dict[str, Any]],
    columns_profiling_samples: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Ask the LLM for a business description of a single batch of columns.

    Returns a ``{column_name: description}`` map; empty when the call fails.
    """
    lines = []
    for col in columns:
        name = col.get("name", "")
        data_type = col.get("data_type") or "unknown"
        samples = _get_column_samples(col, columns_profiling_samples)
        line = _add_samples_suffix(f"  - {name} ({data_type})", samples)
        lines.append(line)

    prompt = "Columns:\n" + "\n".join(lines)

    try:
        result = invoke_with_structured_output(
            get_llm_client(temperature=0.0),
            [
                SystemMessage(content=_DESCRIPTION_SYSTEM),
                HumanMessage(content=prompt),
            ],
            ColumnDescriptionResult,
        )
    except Exception:
        logger.warning(
            "column description batch errored — proceeding without them",
            exc_info=True,
        )
        return {}
    if result is None:
        logger.warning("column description batch failed — proceeding without them")
        return {}

    # The prompt annotates each column as "<name> (<dtype>)". When a column name
    # contains spaces or parentheses (e.g. BIRD's "Academic Year",
    # "Charter School (Y/N)"), the model sometimes echoes the annotation back as
    # the column_name (e.g. "Academic Year (TEXT)"). Resolve each returned name
    # to the requested physical name: exact match first, then the longest
    # requested name the returned string starts with.
    requested_names = [c.get("name", "") for c in columns if c.get("name")]
    requested_set = set(requested_names)
    out: dict[str, str] = {}
    for d in result.descriptions:
        raw = d.column_name or ""
        desc = (d.description or "").strip()
        if not desc:
            continue
        if raw in requested_set:
            out.setdefault(raw, desc)
            continue
        prefixes = [n for n in requested_names if n and raw.startswith(n)]
        if prefixes:
            out.setdefault(max(prefixes, key=len), desc)
    return out


def _generate_column_descriptions(
    columns: list[dict[str, Any]],
    columns_profiling_samples: dict[str, dict[str, Any]] | None,
) -> dict[str, str]:
    """Ask the LLM for a business description of each column.

    Columns are bucketed into small batches described concurrently, so wide
    tables don't overflow a single structured-output response (which times out).

    Returns a ``{column_name: description}`` map; empty when no columns are
    provided. Batches that fail are simply skipped.
    """
    if not columns:
        return {}

    profiling = columns_profiling_samples or {}
    batches = [
        columns[i : i + _DESCRIPTION_BATCH_SIZE]
        for i in range(0, len(columns), _DESCRIPTION_BATCH_SIZE)
    ]

    descriptions: dict[str, str] = {}
    if len(batches) == 1:
        return _describe_column_batch(batches[0], profiling)

    max_workers = min(len(batches), _DESCRIPTION_MAX_WORKERS)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(_describe_column_batch, batch, profiling) for batch in batches
        ]
        for future in as_completed(futures):
            descriptions.update(future.result())

    return descriptions


def blank_column_descriptions(
    columns: list[dict[str, Any]],
    columns_profiling_samples: dict[str, dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Describe every column the source metadata left blank.

    Called from the ingest stage, before the Column nodes are embedded, so that a
    generated description reaches both the embedding index and — via the semantic
    layer, which then only has to copy it onto the ColumnAttribute — the prompt.

    Describing at ingest closes a gap the semantic layer could not: it excludes
    foreign keys from ``column_attribute_specs`` (they are linked by SEMANTIC_FK
    to the attribute they reference, which is what makes two FK columns pointing
    at one primary key recognisably joinable), so an FK column has nothing of its
    own to carry a description. BIRD's ``cards.uuid`` is the case in point — its
    annotation is the string "not useful", which the loader drops, and it is a
    join key many gold queries need.

    Returns ``{column_name: description}`` for the blank columns only, so a fully
    documented table costs no LLM call.
    """
    profiling = columns_profiling_samples or {}
    targets = [
        col
        for col in columns
        if col.get("name") and not str(col.get("description") or "").strip()
    ]
    if not targets:
        return {}
    described = _generate_column_descriptions(targets, profiling)
    return {name: desc for name, desc in described.items() if desc and desc.strip()}


def column_attribute_specs(
    columns: list[dict[str, Any]],
    fks: list[dict[str, Any]],
    suggested_fk_columns: set[str] | None = None,
    columns_profiling_samples: dict[str, dict[str, Any]] | None = None,
) -> list[ColumnAttributeSpec]:
    """Non-FK columns mapped 1:1 to ColumnAttribute candidates."""
    fk_cols = fk_source_columns(fks)
    if suggested_fk_columns:
        fk_cols |= suggested_fk_columns

    candidates = [
        col for col in columns if (name := col.get("name", "")) and name not in fk_cols
    ]

    # Only ask the LLM for columns that don't already have a description
    cols_with_description = [col for col in candidates if col.get("description")]
    cols_without_description = [col for col in candidates if not col.get("description")]
    llm_descriptions = _generate_column_descriptions(
        cols_without_description, columns_profiling_samples
    )

    profiling = columns_profiling_samples or {}

    def _build_column_attribute_spec(
        col: dict[str, Any], description: str | None
    ) -> ColumnAttributeSpec:
        name = col["name"]
        return ColumnAttributeSpec(
            source_column=name,
            name=_column_to_attr_name(name),
            datatype=str(col.get("data_type") or ""),
            description=description,
        )

    # The description is the *only* channel to the generation prompt on the primary
    # retrieval path — ``_FETCH_TABLES_BY_IDS`` projects name, data_type and
    # description, and no sample values — so profiled values have to be folded in
    # here or the model never sees them. A closed enumeration says "one of: ..."
    # and an open sample says "samples: ...", which is the only difference between
    # them: a constraint versus a set of examples.
    #
    # Applied to every column that has values, including those whose description
    # was generated at ingest. Those saw the samples as evidence, but the prose
    # they produced rarely enumerates them, so excluding them would drop values
    # the prompt has no other way to learn.
    def _description_for(col: dict[str, Any]) -> str | None:
        text = col.get("description") or llm_descriptions.get(col["name"])
        samples = _get_column_samples(col, profiling)
        if not samples:
            description = text
        else:
            description = _add_samples_suffix(
                text or "", samples, _is_exhaustive(col, profiling)
            )
        return description

    specs = [
        _build_column_attribute_spec(col, _description_for(col))
        for col in cols_with_description + cols_without_description
    ]
    return specs


def fk_target_table_names(fks: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for fk in fks:
        tgt = fk.get("target_table")
        if tgt and tgt not in seen:
            seen.add(tgt)
            out.append(tgt)
    return out


def _column_to_attr_name(column_name: str) -> str:
    parts = re.split(r"[_\s]+", column_name.strip())
    if not parts:
        return column_name
    return parts[0].lower() + "".join(p.capitalize() for p in parts[1:] if p)
