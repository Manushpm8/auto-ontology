"""Deterministic column mapping — exclude FK columns."""

from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from gsf.semantic.constants import MAX_SAMPLE_VALUE_LEN
from gsf.semantic.date_format import is_date_type
from gsf.semantic.models import ColumnAttributeSpec, ColumnDescriptionResult
from gsf.utils.llm_invoke import (
    get_non_reasoning_llm_client,
    invoke_with_structured_output,
)

logger = logging.getLogger(__name__)

# Wide tables make the model emit one JSON object per column in a single
# structured-output response; large batches generate too many tokens and time
# out. Bucket columns into small batches and describe them concurrently so one
# slow/failed batch never wipes out the whole table's descriptions.
_DESCRIPTION_BATCH_SIZE = 15
_DESCRIPTION_MAX_WORKERS = 1

# Which columns are worth an LLM description. "blank" is the original behaviour:
# a supplied annotation, however poor, suppresses generation. The other two exist
# because a supplied annotation is often worth less than the column name it
# restates -- across BIRD's 665 annotated columns, 11% merely repeat the name
# ("ProductID" -> "Product ID"), 20% more run under 25 characters, and 14% carry
# the raw "commonsense evidence:" fragment from the source CSVs.
DESCRIBE_BLANK = "blank"
DESCRIBE_DEGENERATE = "degenerate"
DESCRIBE_FUSED = "fused"
_DESCRIBE_MODES = (DESCRIBE_BLANK, DESCRIBE_DEGENERATE, DESCRIBE_FUSED)

# Fragments the source CSVs glue onto an annotation; their presence means the
# text is a raw export rather than prose written for a reader.
_ANNOTATION_ARTIFACTS = (
    "commonsense evidence",
    "value description",
    "value_description",
    "not useful",
)
# Below this an annotation cannot carry more than a restated name.
_THIN_DESCRIPTION_LEN = 25

_DESCRIPTION_SYSTEM = """\
You are a data analyst documenting the columns of a relational table for a \
semantic layer. For every column you are given, write ONE concise sentence \
describing what the column represents in business terms.

Rules:
- Use the table name, column name, data type, and sample values as evidence.
- Some columns come with an existing annotation. Treat it as authoritative and \
carry over every factual detail it states -- units, encodings such as "0: N;1: Y", \
null semantics -- while rewriting it into a clear sentence and resolving what an \
abbreviated column name refers to. Never contradict it.
- Keep each description to a single, factual sentence — no speculation.
- Return exactly one entry per column provided, using the physical column name."""


def describe_mode() -> str:
    """Which columns to describe, from ``SEMANTIC_DESCRIBE_MODE``.

    Read per call rather than captured at import so a run can set it without
    caring whether ``.env`` was loaded before this module was imported.
    """
    mode = (os.environ.get("SEMANTIC_DESCRIBE_MODE") or DESCRIBE_BLANK).strip().lower()
    if mode not in _DESCRIBE_MODES:
        logger.warning(
            "unknown SEMANTIC_DESCRIBE_MODE %r — falling back to %r",
            mode,
            DESCRIBE_BLANK,
        )
        return DESCRIBE_BLANK
    return mode


def _is_blank(description: Any) -> bool:
    return not str(description or "").strip()


def _normalised(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def is_degenerate_description(name: str, description: Any) -> bool:
    """Whether an annotation says no more than the column name already does.

    Blank counts as degenerate, so ``degenerate`` mode is a superset of
    ``blank``. A tautology is judged on alphanumerics only, which is what makes
    "ProductID" and "Product ID" the same string.
    """
    if _is_blank(description):
        return True
    text = str(description).strip()
    lowered = text.lower()
    if any(artifact in lowered for artifact in _ANNOTATION_ARTIFACTS):
        return True
    if len(text) <= _THIN_DESCRIPTION_LEN:
        return True
    normalised_name = _normalised(name)
    normalised_text = _normalised(text)
    return normalised_text == normalised_name or (
        normalised_text.startswith(normalised_name)
        and len(normalised_text) <= len(normalised_name) + 4
    )


def to_term_name(table_name: str) -> str:
    """CamelCase provisional Term from snake_case table name."""
    parts = re.split(r"[_\s]+", table_name.strip())
    return "".join(p.capitalize() for p in parts if p)


def fk_source_columns(fks: list[dict[str, Any]]) -> set[str]:
    return {fk["source_column"] for fk in fks if fk.get("source_column")}


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
    if is_date_type(col.get("data_type")):
        return []
    name = col.get("name", "")
    samples = (columns_profiling_samples.get(name) or {}).get("sample_values") or []
    return [str(s) for s in samples if len(str(s)) <= MAX_SAMPLE_VALUE_LEN]


_UNIQUENESS_MARKER = "unique per row"
_DISTINCT_MARKER = "distinct values"
_DATE_FORMAT_MARKER = "format:"

# How an enriched description ends: "— samples: a, b", or "— one of: a, b" when
# the values are the column's complete set. Public because the generation prompt
# renderer recognises the same suffix on text this module wrote.
VALUE_SUFFIX_MARKERS = ("samples:", "one of:")


def exact_cardinality_enabled() -> bool:
    """Whether to probe every column's true distinct count.

    Off by default: it costs one ``COUNT(DISTINCT)`` per column over the whole
    column, which is cheap on the BIRD databases (~25s for 798 columns) but not
    on a large warehouse table. Read per call so a run can set it without regard
    to import order.
    """
    return (os.environ.get("SEMANTIC_EXACT_CARDINALITY") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _cardinality_clause(
    col: dict[str, Any],
    columns_profiling_samples: dict[str, dict[str, Any]],
) -> str | None:
    """How many different values the column holds, when that is known exactly.

    Returns ``None`` unless ``n_distinct`` was measured. The sampled
    ``is_unique`` flag is deliberately not
    trusted here: of 148 columns a 1000-row sample called unique, 25 were not
    unique over the whole table, so a description built on it would tell the
    model an 11-value enum was a key.

    Nothing is said for a closed enumeration — the values are about to be listed
    in full, so their count is already on the page.

    The probe flag controls whether fresh counts are measured. Once a count is
    persisted, retrieval keeps using that known catalog fact.
    """
    profile = columns_profiling_samples.get(col.get("name", "")) or {}
    n_distinct = profile.get("n_distinct")
    if n_distinct is None:
        return None
    if profile.get("is_unique"):
        return _UNIQUENESS_MARKER
    if profile.get("exhaustive"):
        return None
    return f"{n_distinct:,} {_DISTINCT_MARKER}"


def _date_format_clause(
    col: dict[str, Any],
    columns_profiling_samples: dict[str, dict[str, Any]],
) -> str | None:
    """How a date column stores its values, when a single reading fits them all.

    The counterpart to the value list every other column gets: dates are held
    back from sampling because a concrete date carries no business meaning, but
    that leaves nothing in the prompt saying whether a predicate should compare
    against ``'1995-03-24'`` or ``'950324'``. The notation says it in one clause
    without putting dates back in the description.
    """
    profile = columns_profiling_samples.get(col.get("name", "")) or {}
    date_format = profile.get("format") or profile.get("date_format")
    if not date_format:
        return None
    return f"{_DATE_FORMAT_MARKER} {date_format}"


def _add_date_format_clause(text: str, clause: str | None) -> str:
    """Append the notation, idempotently on its own marker.

    Keyed on its own marker rather than sharing :func:`_add_clause`'s, whose
    check would suppress the notation on any column already carrying a
    cardinality clause.
    """
    if not clause or _DATE_FORMAT_MARKER in text:
        return text
    return f"{text} — {clause}" if text else clause


def _add_clause(text: str, clause: str | None) -> str:
    """Append a clause in the same style as the value list.

    Idempotent on the marker words, because the DAL resolves a column's
    description through its ColumnAttribute: a second semantic compile over the
    same graph sees text this function already appended to.
    """
    if not clause:
        return text
    if _UNIQUENESS_MARKER in text or _DISTINCT_MARKER in text:
        return text
    return f"{text} — {clause}" if text else clause


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

    Idempotent on the suffix, for the same reason :func:`_add_clause` is: the text
    handed in may be a description this module already enriched, whether by an
    earlier compile over the same graph or by a caller that cannot tell the two
    apart. Without this a column's values were liable to be listed twice.
    """
    if not samples or any(marker in text for marker in VALUE_SUFFIX_MARKERS):
        return text
    label = "one of" if exhaustive else "samples"
    listed = f"{label}: {', '.join(samples)}"
    # A failed description batch leaves no text to append to; emit the values
    # alone rather than a dangling separator.
    return f"{text} — {listed}" if text else listed


def enrich_column_description(
    col: dict[str, Any],
    columns_profiling_samples: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    """A column's description with its measured shape and values folded in.

    The single implementation behind both channels that carry a column to the
    generation prompt: the ColumnAttribute description built here at compile
    time, and the Column node's own description, which is all an FK column has
    because the semantic layer deliberately gives it no attribute to hold one.
    Shared so the two cannot drift into describing the same profile differently.
    """
    profiling = columns_profiling_samples or {}
    text = _add_clause(
        str(col.get("description") or ""), _cardinality_clause(col, profiling)
    )
    text = _add_date_format_clause(text, _date_format_clause(col, profiling))
    samples = _get_column_samples(col, profiling)
    if samples:
        text = _add_samples_suffix(text, samples, _is_exhaustive(col, profiling))
    return text or None


def profile_from_column(col: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """A one-column profiling map built from properties stored on a Column node.

    Lets a caller holding a raw column row reuse :func:`enrich_column_description`,
    which is keyed by column name because the compile path profiles a whole table
    at once.
    """
    raw = col.get("sample_values")
    try:
        values = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except ValueError:
        values = []
    n_distinct = col.get("n_distinct")
    return {
        str(col.get("name") or ""): {
            "sample_values": [str(v) for v in values],
            "is_unique": bool(col.get("is_unique")),
            "exhaustive": bool(col.get("exhaustive")),
            "n_distinct": int(n_distinct) if n_distinct is not None else None,
            "date_format": col.get("format") or col.get("date_format"),
        }
    }


def _describe_column_batch(
    columns: list[dict[str, Any]],
    columns_profiling_samples: dict[str, dict[str, Any]],
    table_name: str | None = None,
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
        # An existing annotation is evidence like any other, and the strongest
        # available for an abbreviated name: "a12" means nothing until the
        # annotation says "unemployment rate 1995".
        existing = str(col.get("description") or "").strip()
        if existing:
            line = f"{line}\n      existing annotation: {existing}"
        lines.append(line)

    header = f"Table: {table_name}\n" if table_name else ""
    prompt = f"{header}Columns:\n" + "\n".join(lines)

    try:
        result = invoke_with_structured_output(
            get_non_reasoning_llm_client(temperature=0.0),
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
    table_name: str | None = None,
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
        return _describe_column_batch(batches[0], profiling, table_name)

    max_workers = min(len(batches), _DESCRIPTION_MAX_WORKERS)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(_describe_column_batch, batch, profiling, table_name)
            for batch in batches
        ]
        for future in as_completed(futures):
            descriptions.update(future.result())

    return descriptions


def columns_to_describe(
    columns: list[dict[str, Any]], mode: str | None = None
) -> list[dict[str, Any]]:
    """The columns the current mode wants an LLM description for.

    Exposed so a caller can skip profiling a table it has no work for without
    reimplementing the rule — getting that check out of step with the rule is
    what let a zero-blank database bypass description generation entirely.
    """
    mode = mode or describe_mode()
    if mode == DESCRIBE_FUSED:
        return [col for col in columns if col.get("name")]
    if mode == DESCRIBE_DEGENERATE:
        return [
            col
            for col in columns
            if col.get("name")
            and is_degenerate_description(col["name"], col.get("description"))
        ]
    return [
        col for col in columns if col.get("name") and _is_blank(col.get("description"))
    ]


def _fuse(annotation: str, generated: str) -> str:
    """Combine a supplied annotation with a generated one without losing either.

    A degenerate annotation is replaced outright — it holds nothing to preserve.
    A substantive one is kept and the generated sentence appended, so fusing can
    only ever add. Appending is skipped when the generated text restates the
    annotation, which is the common case for an already well-documented column.
    """
    if not generated:
        return annotation
    if not annotation:
        return generated
    if _normalised(generated) in _normalised(annotation):
        return annotation
    lead = (
        annotation
        if annotation.rstrip().endswith((".", "!", "?"))
        else f"{annotation}."
    )
    return f"{lead} {generated}"


def blank_column_descriptions(
    columns: list[dict[str, Any]],
    columns_profiling_samples: dict[str, dict[str, Any]] | None = None,
    table_name: str | None = None,
) -> dict[str, str]:
    """Describe the columns whose supplied annotation does not carry its weight.

    Which those are is set by ``SEMANTIC_DESCRIBE_MODE``:

    ``blank``
        Only columns with no annotation at all. The original behaviour, and the
        default, so an unset environment changes nothing.
    ``degenerate``
        Also columns whose annotation says no more than the column name — a
        tautology, under 25 characters, or a raw source-CSV fragment. Their text
        is replaced, since there is nothing in it to preserve.
    ``fused``
        Every column. A substantive annotation is kept and the generated
        sentence appended, so the result is never worse-informed than the input.

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

    Returns ``{column_name: description}`` for the columns it described, so a
    table this mode finds nothing to improve on costs no LLM call. Because
    ``apply_metadata`` re-stamps the supplied annotations at the start of every
    ingest, a replaced description is always recoverable by re-running with the
    mode unset.
    """
    profiling = columns_profiling_samples or {}
    targets = columns_to_describe(columns)
    if not targets:
        return {}
    described = _generate_column_descriptions(targets, profiling, table_name)

    out: dict[str, str] = {}
    for col in targets:
        name = col["name"]
        generated = (described.get(name) or "").strip()
        if not generated:
            continue
        annotation = str(col.get("description") or "").strip()
        # Only a substantive annotation is worth keeping alongside the new text.
        if annotation and not is_degenerate_description(name, annotation):
            fused = _fuse(annotation, generated)
        else:
            fused = generated
        if fused and fused != annotation:
            out[name] = fused
    return out


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
        # Cardinality was profiled and stored on the Column node, but its only
        # consumer was the FK suggester, so the generation prompt never learned
        # which columns identify a row — the thing that decides whether a join
        # can fan out and whether a GROUP BY or DISTINCT is needed at all — nor
        # how many values a column it cannot enumerate actually holds.
        return enrich_column_description({**col, "description": text}, profiling)

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
