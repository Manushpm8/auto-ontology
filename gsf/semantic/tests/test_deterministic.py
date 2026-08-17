"""Tests for deterministic column mapping."""

from __future__ import annotations

import os
from unittest.mock import patch

from gsf.semantic.constants import MAX_SAMPLE_VALUE_LEN
from gsf.semantic.deterministic import (
    _describe_column_batch,
    blank_column_descriptions,
    column_attribute_specs,
    fk_source_columns,
    fk_target_table_names,
    is_degenerate_description,
    to_term_name,
)


@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
def test_excludes_suggested_fk_columns(_mock_desc) -> None:
    columns = [
        {"name": "id", "data_type": "integer"},
        {"name": "vendor_id", "data_type": "integer"},
        {"name": "amount", "data_type": "numeric"},
    ]
    specs = column_attribute_specs(
        columns,
        [],
        suggested_fk_columns={"vendor_id"},
    )
    assert {s.source_column for s in specs} == {"id", "amount"}


@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
def test_excludes_fk_columns(_mock_desc) -> None:
    columns = [
        {"name": "id", "data_type": "integer"},
        {"name": "customer_id", "data_type": "integer"},
        {"name": "amount", "data_type": "numeric"},
    ]
    fks = [{"source_column": "customer_id", "target_table": "customers"}]
    specs = column_attribute_specs(columns, fks)
    assert {s.source_column for s in specs} == {"id", "amount"}
    assert fk_source_columns(fks) == {"customer_id"}


@patch(
    "gsf.semantic.deterministic._generate_column_descriptions",
    return_value={"amount": "The monetary amount of the order."},
)
def test_llm_description_used_as_fallback(_mock_desc) -> None:
    columns = [
        {"name": "id", "data_type": "integer", "description": "Primary key."},
        {"name": "amount", "data_type": "numeric"},
    ]
    specs = {s.source_column: s for s in column_attribute_specs(columns, [])}
    # Existing description wins over the LLM one.
    assert specs["id"].description == "Primary key."
    # LLM description fills in when the column has none.
    assert specs["amount"].description == "The monetary amount of the order."


@patch(
    "gsf.semantic.deterministic._generate_column_descriptions",
    return_value={
        "status": "The operational status of the school.",
        "email": "The contact email address.",
    },
)
def test_profiled_values_reach_llm_descriptions(_mock_desc) -> None:
    """An LLM-written description carries its values too, labelled by completeness.

    The primary retrieval path projects only the description, so an LLM-described
    column loses its values unless they are folded into the prose — and the
    generated prose rarely enumerates them on its own.
    """
    columns = [
        {"name": "status", "data_type": "text"},
        {"name": "email", "data_type": "text"},
    ]
    profiling = {
        "status": {"sample_values": ["Active", "Closed"], "exhaustive": True},
        "email": {"sample_values": ["a@x.com", "b@y.com"], "exhaustive": False},
    }
    specs = {
        s.source_column: s
        for s in column_attribute_specs(
            columns, [], columns_profiling_samples=profiling
        )
    }
    assert specs["status"].description == (
        "The operational status of the school. — one of: Active, Closed"
    )
    assert specs["email"].description == (
        "The contact email address. — samples: a@x.com, b@y.com"
    )


@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
def test_over_length_values_are_dropped_from_the_description(_mock_desc) -> None:
    """A free-text column must not paste a whole row into the schema block.

    The description is the only channel the primary retrieval path carries, so an
    uncapped value here reaches the prompt in full — a real ``users.AboutMe``
    sample ran to 2.5k characters.
    """
    long_value = "<p>" + "x" * MAX_SAMPLE_VALUE_LEN + "</p>"
    columns = [
        {"name": "about_me", "data_type": "text", "description": "User bio."},
        {"name": "status", "data_type": "text", "description": "Row status."},
    ]
    profiling = {
        "about_me": {"sample_values": [long_value, "ok"], "exhaustive": False},
        # Exhaustive, but one value is too long to carry: the surviving list is no
        # longer the complete set, so it must not be stated as a constraint.
        "status": {"sample_values": ["Active", long_value], "exhaustive": True},
    }
    specs = {
        s.source_column: s
        for s in column_attribute_specs(
            columns, [], columns_profiling_samples=profiling
        )
    }
    assert specs["about_me"].description == "User bio. — samples: ok"
    assert specs["status"].description == "Row status. — samples: Active"


@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
def test_values_are_labelled_by_completeness(_mock_desc) -> None:
    """A complete value set is a constraint; an incomplete one is examples.

    Both must be appended: the description is the only field the primary retrieval
    path carries to the prompt, so a value left out here is one the model cannot see.
    """
    columns = [
        {"name": "status", "data_type": "text", "description": "School status."},
        {"name": "city", "data_type": "text", "description": "City name."},
        {"name": "notes", "data_type": "text", "description": "Free text."},
    ]
    profiling = {
        "status": {"sample_values": ["Active", "Closed"], "exhaustive": True},
        "city": {"sample_values": ["Prague", "Brno"], "exhaustive": False},
        "notes": {"sample_values": [], "exhaustive": False},
    }
    specs = {
        s.source_column: s
        for s in column_attribute_specs(
            columns, [], columns_profiling_samples=profiling
        )
    }
    assert specs["status"].description == "School status. — one of: Active, Closed"
    assert specs["city"].description == "City name. — samples: Prague, Brno"
    # No profiled values: the description is passed through untouched rather than
    # gaining a dangling separator.
    assert specs["notes"].description == "Free text."


@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
def test_exhaustive_values_survive_a_failed_description_batch(_mock_desc) -> None:
    """No description to append to must not yield a dangling separator."""
    columns = [{"name": "status", "data_type": "text"}]
    profiling = {"status": {"sample_values": ["Active"], "exhaustive": True}}
    specs = column_attribute_specs(columns, [], columns_profiling_samples=profiling)
    assert specs[0].description == "one of: Active"


@patch(
    "gsf.semantic.deterministic._generate_column_descriptions",
    return_value={"uuid": "The unique identifier of a card."},
)
def test_only_blank_columns_are_described(mock_desc) -> None:
    """Ingest describes the blank columns, including foreign keys.

    Foreign keys matter here because the semantic layer excludes them from
    ColumnAttributes, so this is the only stage that can describe them.
    """
    columns = [
        {"name": "uuid", "data_type": "text"},
        {"name": "set_code", "data_type": "text", "description": "The set code."},
    ]
    out = blank_column_descriptions(columns)
    assert out == {"uuid": "The unique identifier of a card."}
    # The documented column was not sent to the LLM.
    assert [c["name"] for c in mock_desc.call_args[0][0]] == ["uuid"]


@patch("gsf.semantic.deterministic._generate_column_descriptions")
def test_blank_descriptions_skip_the_llm_when_nothing_is_blank(mock_desc) -> None:
    columns = [{"name": "set_code", "data_type": "text", "description": "The set."}]
    assert blank_column_descriptions(columns) == {}
    mock_desc.assert_not_called()


_TAUTOLOGY = {
    "name": "Charter Funding Type",
    "data_type": "text",
    "description": "Charter Funding Type",
}
_SUBSTANTIVE = {
    "name": "pct_free",
    "data_type": "real",
    "description": "The share of pupils eligible for a free meal, as a percentage.",
}


def test_degenerate_annotations_are_recognised() -> None:
    # A tautology, judged on alphanumerics so punctuation and case do not hide it.
    assert is_degenerate_description("ProductID", "Product ID")
    # A raw fragment from the source CSVs.
    assert is_degenerate_description(
        "ClosedDate", "closed date, commonsense evidence:x"
    )
    # Too short to hold more than the name.
    assert is_degenerate_description("a12", "unemployment rate")
    # Blank, which makes degenerate mode a superset of blank mode.
    assert is_degenerate_description("uuid", None)
    # Substantive text is left alone.
    assert not is_degenerate_description(
        _SUBSTANTIVE["name"], _SUBSTANTIVE["description"]
    )


@patch.dict(os.environ, {"SEMANTIC_DESCRIBE_MODE": "degenerate"})
@patch(
    "gsf.semantic.deterministic._generate_column_descriptions",
    return_value={"Charter Funding Type": "How the charter school is funded."},
)
def test_degenerate_mode_replaces_a_tautology_but_spares_a_real_annotation(
    mock_desc,
) -> None:
    out = blank_column_descriptions([_TAUTOLOGY, _SUBSTANTIVE])
    # The tautology is replaced outright: it held nothing to preserve.
    assert out == {"Charter Funding Type": "How the charter school is funded."}
    assert [c["name"] for c in mock_desc.call_args[0][0]] == ["Charter Funding Type"]


@patch.dict(os.environ, {"SEMANTIC_DESCRIBE_MODE": "fused"})
@patch(
    "gsf.semantic.deterministic._generate_column_descriptions",
    return_value={"pct_free": "Values run from 0 to 100."},
)
def test_fused_mode_keeps_the_annotation_and_appends_what_is_new(_mock_desc) -> None:
    out = blank_column_descriptions([_SUBSTANTIVE])
    assert out["pct_free"] == (
        "The share of pupils eligible for a free meal, as a percentage. "
        "Values run from 0 to 100."
    )


@patch.dict(os.environ, {"SEMANTIC_DESCRIBE_MODE": "fused"})
@patch(
    "gsf.semantic.deterministic._generate_column_descriptions",
    return_value={"pct_free": "share of pupils eligible for a free meal"},
)
def test_fused_mode_does_not_repeat_a_restated_annotation(_mock_desc) -> None:
    """Fusing a well-documented column must not double its text."""
    assert blank_column_descriptions([_SUBSTANTIVE]) == {}


@patch.dict(os.environ, {"SEMANTIC_DESCRIBE_MODE": "not-a-mode"})
@patch("gsf.semantic.deterministic._generate_column_descriptions")
def test_an_unknown_mode_falls_back_to_blank(mock_desc) -> None:
    assert blank_column_descriptions([_TAUTOLOGY]) == {}
    mock_desc.assert_not_called()


@patch.dict(os.environ, {"SEMANTIC_EXACT_CARDINALITY": "1"})
@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
def test_exact_cardinality_reaches_the_description(_mock_desc) -> None:
    """Cardinality was profiled and stored but never reached the prompt.

    Uniqueness tells the model a join on this column cannot fan out, and that no
    GROUP BY or DISTINCT is needed for one row per value. A plain distinct count
    tells it how large a domain is that it never sees enumerated.
    """
    columns = [
        {"name": "card_id", "data_type": "text", "description": "The card identifier."},
        {"name": "artist", "data_type": "text", "description": "The illustrator."},
        {"name": "colour", "data_type": "text", "description": "The card colour."},
    ]
    profiling = {
        "card_id": {
            "sample_values": ["a1", "b2"],
            "is_unique": True,
            "exhaustive": False,
            "n_distinct": 12345,
        },
        "artist": {
            "sample_values": ["Rex", "Dan"],
            "is_unique": False,
            "exhaustive": False,
            "n_distinct": 4021,
        },
        "colour": {
            "sample_values": ["Red", "Blue"],
            "is_unique": False,
            "exhaustive": True,
            "n_distinct": 2,
        },
    }
    specs = {
        s.source_column: s
        for s in column_attribute_specs(
            columns, [], columns_profiling_samples=profiling
        )
    }
    assert specs["card_id"].description == (
        "The card identifier. — unique per row — samples: a1, b2"
    )
    assert specs["artist"].description == (
        "The illustrator. — 4,021 distinct values — samples: Rex, Dan"
    )
    # A closed enumeration says nothing: the count is visible in the list.
    assert specs["colour"].description == "The card colour. — one of: Red, Blue"


@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
def test_no_cardinality_is_claimed_without_an_exact_count(_mock_desc) -> None:
    """The sampled is_unique flag is wrong on 17% of the columns it flags.

    Without ``n_distinct`` from a full COUNT(DISTINCT) the description stays
    silent rather than repeat a claim derived from 1000 rows.
    """
    columns = [
        {"name": "loyalty", "data_type": "text", "description": "The loyalty value."}
    ]
    profiling = {
        "loyalty": {"sample_values": ["3"], "is_unique": True, "exhaustive": False}
    }
    specs = column_attribute_specs(columns, [], columns_profiling_samples=profiling)
    assert specs[0].description == "The loyalty value. — samples: 3"


@patch.dict(os.environ, {"SEMANTIC_EXACT_CARDINALITY": "1"})
@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
def test_a_date_column_states_the_notation_it_is_stored_in(_mock_desc) -> None:
    """Dates carry no sample values, so the notation is their only mechanics.

    ``950324`` and ``1995-03-24`` are the same day under different storage and
    admit no common predicate, and nothing else in the prompt distinguishes
    them: a model comparing against the wrong one gets SQL that runs, returns
    nothing, and looks right.
    """
    columns = [
        {
            "name": "First Date",
            "data_type": "DATE",
            "description": "When the patient first attended.",
        }
    ]
    profiling = {
        "First Date": {
            "sample_values": ["1991-06-13"],
            "is_unique": False,
            "exhaustive": False,
            "n_distinct": 797,
            "date_format": "YYYY-MM-DD",
        }
    }
    specs = column_attribute_specs(columns, [], columns_profiling_samples=profiling)
    # The values themselves stay out — a concrete date carries no business
    # meaning — while the shape they are written in comes through.
    assert specs[0].description == (
        "When the patient first attended. — 797 distinct values "
        "— date format: YYYY-MM-DD"
    )


@patch.dict(os.environ, {"SEMANTIC_EXACT_CARDINALITY": "1"})
@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
def test_no_notation_is_stated_when_the_sample_could_not_settle_one(
    _mock_desc,
) -> None:
    """A column whose values read two ways gets no claim at all."""
    columns = [
        {"name": "opened", "data_type": "DATE", "description": "Account opening."}
    ]
    profiling = {
        "opened": {
            "sample_values": [],
            "is_unique": False,
            "exhaustive": False,
            "n_distinct": 40,
            "date_format": None,
        }
    }
    specs = column_attribute_specs(columns, [], columns_profiling_samples=profiling)
    assert specs[0].description == "Account opening. — 40 distinct values"


@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
def test_the_notation_clause_is_not_applied_twice(_mock_desc) -> None:
    """A recompile reads back text this module already enriched.

    Separate from the cardinality clause's guard: that one bails on seeing its
    own markers, so a column carrying both would have had the notation appended
    afresh on every pass.
    """
    columns = [
        {
            "name": "First Date",
            "data_type": "DATE",
            "description": (
                "When the patient first attended. — 797 distinct values "
                "— date format: YYYY-MM-DD"
            ),
        }
    ]
    profiling = {
        "First Date": {
            "sample_values": [],
            "is_unique": False,
            "exhaustive": False,
            "n_distinct": 797,
            "date_format": "YYYY-MM-DD",
        }
    }
    specs = column_attribute_specs(columns, [], columns_profiling_samples=profiling)
    assert specs[0].description == (
        "When the patient first attended. — 797 distinct values "
        "— date format: YYYY-MM-DD"
    )


@patch("gsf.semantic.deterministic._generate_column_descriptions", return_value={})
def test_the_cardinality_clause_is_not_applied_twice(_mock_desc) -> None:
    """A recompile sees the attribute's own text back through the DAL."""
    columns = [
        {
            "name": "card_id",
            "data_type": "text",
            "description": "The card identifier. — unique per row",
        }
    ]
    profiling = {"card_id": {"sample_values": [], "is_unique": True, "n_distinct": 99}}
    specs = column_attribute_specs(columns, [], columns_profiling_samples=profiling)
    assert specs[0].description == "The card identifier. — unique per row"


@patch("gsf.semantic.deterministic.get_llm_client", return_value=object())
@patch("gsf.semantic.deterministic.invoke_with_structured_output", return_value=None)
def test_the_table_name_and_annotation_reach_the_prompt(mock_invoke, _client) -> None:
    """Both are evidence the model had no way to see before.

    An abbreviated name like "a12" is unreadable until the annotation says
    "unemployment rate 1995", and a column was previously described with no idea
    which table it belonged to.
    """
    _describe_column_batch(
        [{"name": "a12", "data_type": "real", "description": "unemployment rate 1995"}],
        {"a12": {"sample_values": ["3.3", "1.6"], "exhaustive": False}},
        table_name="district",
    )
    prompt = mock_invoke.call_args[0][1][1].content
    assert "Table: district" in prompt
    assert "existing annotation: unemployment rate 1995" in prompt
    assert "samples: 3.3, 1.6" in prompt


def test_to_term_name() -> None:
    assert to_term_name("purchase_orders") == "PurchaseOrders"


def test_fk_targets_deduped() -> None:
    fks = [
        {"target_table": "customers"},
        {"target_table": "customers"},
        {"target_table": "products"},
    ]
    assert fk_target_table_names(fks) == ["customers", "products"]
