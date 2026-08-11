"""Tests for process_table taxonomy compilation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from gsf.semantic.constants import MAX_SAMPLE_VALUE_LEN
from gsf.semantic.visit_enter import calculate_columns_profiling, process_table


@patch("gsf.semantic.visit_enter.store_column_exhaustiveness")
@patch("gsf.semantic.visit_enter.store_column_uniqueness")
@patch("gsf.semantic.visit_enter.store_column_sample_values")
def test_calculate_columns_profiling_unhashable_values(
    mock_store_samples: MagicMock,
    mock_store_unique: MagicMock,
    mock_store_exhaustive: MagicMock,
) -> None:
    # Postgres array / JSON columns come back as Python lists/dicts, which are
    # unhashable — profiling must not crash on them.
    df = pd.DataFrame(
        {
            "id": [1, 2],
            "tags": [["a", "b"], ["a", "b"]],
            "meta": [{"k": 1}, {"k": 2}],
        }
    )
    connector = MagicMock()
    connector.execute.return_value = df

    table = {"id": "t1", "name": "orders", "schema_name": "public"}
    columns = [
        {"name": "id", "data_type": "integer"},
        {"name": "tags", "data_type": "ARRAY"},
        {"name": "meta", "data_type": "jsonb"},
    ]

    result = calculate_columns_profiling(table, columns, connector)

    assert set(result) == {"id", "tags", "meta"}
    assert result["tags"]["is_unique"] is False  # ["a","b"] repeated
    assert result["meta"]["is_unique"] is True
    assert result["id"]["is_unique"] is True


@patch("gsf.semantic.visit_enter.store_column_exhaustiveness")
@patch("gsf.semantic.visit_enter.store_column_uniqueness")
@patch("gsf.semantic.visit_enter.store_column_sample_values")
def test_calculate_columns_profiling(
    mock_store_samples: MagicMock,
    mock_store_unique: MagicMock,
    mock_store_exhaustive: MagicMock,
) -> None:
    df = pd.DataFrame(
        {
            "id": [1, 2, 3, 4],
            "status": ["open", "open", "closed", "open"],
            "created_at": ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"],
            # Sized off the cap rather than a literal, so raising the cap does not
            # quietly turn this into a "short value" and stop testing the filter.
            "token": [c * (MAX_SAMPLE_VALUE_LEN + 10) for c in "abac"],
            "code": [c * MAX_SAMPLE_VALUE_LEN for c in "wxyz"],
        }
    )

    # A single return_value would hand the row-sample frame back to the
    # per-column distinct probes too, so every column would be profiled with the
    # first column's values. Route each query to the data it really asks for.
    def _execute(sql: str) -> pd.DataFrame:
        if "DISTINCT" not in sql.upper():
            return df
        column = sql.split('"')[1]
        return pd.DataFrame({column: pd.Series(df[column].dropna().unique())})

    connector = MagicMock()
    connector.execute.side_effect = _execute

    table = {"id": "t1", "name": "orders", "schema_name": "public"}
    columns = [
        {"name": "id", "data_type": "integer"},
        {"name": "status", "data_type": "text"},
        {"name": "created_at", "data_type": "timestamp"},
        {"name": "token", "data_type": "text"},
        {"name": "code", "data_type": "text"},
    ]

    result = calculate_columns_profiling(table, columns, connector)

    # Several queries run per table — a row count, the row sample, then a
    # distinct-value probe per categorical column — so find the row sample by what
    # it says rather than by its position, which changes as probes are added.
    executed = [call[0][0] for call in connector.execute.call_args_list]
    sample_sql = [sql for sql in executed if "LIMIT 1000" in sql]
    assert len(sample_sql) == 1
    assert '"public"."orders"' in sample_sql[0]

    # Returned dict includes every column (dates, long strings included),
    # each with its top-5 sample values and is_unique flag.
    assert set(result) == {"id", "status", "created_at", "token", "code"}
    assert result["status"]["sample_values"][0] == "open"
    assert len(result["created_at"]["sample_values"]) == 4
    assert result["id"]["is_unique"] is True
    assert result["status"]["is_unique"] is False
    assert result["created_at"]["is_unique"] is True
    assert result["token"]["is_unique"] is False

    # A DISTINCT probe that returned every value marks the list as a closed set;
    # the numeric and date columns are never probed, so they stay open samples.
    assert result["status"]["exhaustive"] is True
    assert result["id"]["exhaustive"] is False
    assert result["created_at"]["exhaustive"] is False

    # Uniqueness persisted for every column.
    uniqueness = mock_store_unique.call_args[0][1]
    assert uniqueness == {
        "id": True,
        "status": False,
        "created_at": True,
        "token": False,
        "code": True,
    }

    # Stored sample values exclude the date column and the over-long token
    # values, while a value of exactly the cap length is kept.
    stored = mock_store_samples.call_args[0][1]
    assert "created_at" not in stored
    assert "token" not in stored
    assert len(stored["code"][0]) == MAX_SAMPLE_VALUE_LEN
    assert stored["status"][0] == "open"

    # Exhaustiveness is persisted so the semantic layer can read the profile back
    # instead of recomputing it. It describes the *stored* list, so a column whose
    # values were dropped is no longer advertised as a complete set.
    persisted = mock_store_exhaustive.call_args[0][1]
    assert persisted["status"] is True
    assert persisted["id"] is False
    assert persisted["created_at"] is False
    assert persisted["token"] is False


@patch("gsf.semantic.visit_enter.store_column_exhaustiveness")
@patch("gsf.semantic.visit_enter.store_column_uniqueness")
@patch("gsf.semantic.visit_enter.store_column_sample_values")
def test_calculate_columns_profiling_quotes_reserved_word_table(
    mock_store_samples: MagicMock,
    mock_store_unique: MagicMock,
    mock_store_exhaustive: MagicMock,
) -> None:
    """A table named after a SQL reserved word must still be profiled.

    ``SELECT * FROM order`` is a syntax error, so an unquoted table name cost
    tables like ``order`` every sample value and uniqueness flag they had.
    """
    df = pd.DataFrame({"k_symbol": ["SIPO", "UVER", "SIPO", "POJISTNE"]})
    connector = MagicMock()
    connector.execute.return_value = df

    table = {"id": "t1", "name": "order", "schema_name": None}
    columns = [{"name": "k_symbol", "data_type": "text"}]

    result = calculate_columns_profiling(table, columns, connector)

    for call in connector.execute.call_args_list:
        assert '"order"' in call[0][0]
        assert "FROM order" not in call[0][0]
    assert result["k_symbol"]["sample_values"][0] == "SIPO"
    assert mock_store_samples.call_args[0][1]["k_symbol"]


@patch("gsf.semantic.visit_enter.exact_cardinality_enabled", return_value=True)
@patch("gsf.semantic.visit_enter.store_column_exhaustiveness")
@patch("gsf.semantic.visit_enter.store_column_uniqueness")
@patch("gsf.semantic.visit_enter.store_column_sample_values")
def test_sparse_column_is_still_probed_for_its_full_domain(
    mock_store_samples: MagicMock,
    mock_store_unique: MagicMock,
    mock_store_exhaustive: MagicMock,
    _exact_enabled: MagicMock,
) -> None:
    """A column that is nearly all NULL in the row sample keeps its value list.

    thrombosis_prediction's antibody columns hold 99 values across 13,908 rows,
    so the sampled prefix carries one, which ``Series.is_unique`` calls unique.
    Gating the distinct probe on that flag dropped exactly the columns whose
    domain the sample fails to show: the prompt saw ``0`` alone, while the
    answer needed ``negative``.
    """
    df = pd.DataFrame({"ssa": [None, "0", None, None]})
    distinct = ["0", "1", "4", "16", "64", "256", "negative"]

    def _execute(sql: str) -> pd.DataFrame:
        upper = sql.upper()
        if "COUNT(DISTINCT" in upper:
            return pd.DataFrame({"n": [99], "nd": [len(distinct)]})
        if "COUNT(*)" in upper:
            return pd.DataFrame({"n": [13908]})
        if "DISTINCT" in upper:
            return pd.DataFrame({"ssa": distinct})
        return df

    connector = MagicMock()
    connector.execute.side_effect = _execute

    result = calculate_columns_profiling(
        {"id": "t1", "name": "Laboratory", "schema_name": None},
        [{"name": "ssa", "data_type": "text"}],
        connector,
    )

    assert "negative" in result["ssa"]["sample_values"]
    assert result["ssa"]["exhaustive"] is True
    # The exact count overrides the sampled flag, which is what makes the
    # column eligible for the probe in the first place.
    assert result["ssa"]["is_unique"] is False
    assert result["ssa"]["n_distinct"] == len(distinct)
    assert "negative" in mock_store_samples.call_args[0][1]["ssa"]


@patch("gsf.semantic.visit_enter.exact_cardinality_enabled", return_value=True)
@patch("gsf.semantic.visit_enter.store_column_exhaustiveness")
@patch("gsf.semantic.visit_enter.store_column_uniqueness")
@patch("gsf.semantic.visit_enter.store_column_sample_values")
def test_small_dimension_key_is_enumerated_despite_being_unique(
    mock_store_samples: MagicMock,
    mock_store_unique: MagicMock,
    mock_store_exhaustive: MagicMock,
    _exact_enabled: MagicMock,
) -> None:
    """A tiny lookup table's unique values are the literals a filter must match.

    ``League.name`` is unique across its eleven rows, and under the old gate
    that uniqueness alone withheld the list, leaving the model to guess at names
    like 'Spain LIGA BBVA'.
    """
    names = [f"league {i}" for i in range(11)]
    df = pd.DataFrame({"name": names})

    def _execute(sql: str) -> pd.DataFrame:
        upper = sql.upper()
        if "COUNT(DISTINCT" in upper:
            return pd.DataFrame({"n": [len(names)], "nd": [len(names)]})
        if "COUNT(*)" in upper:
            return pd.DataFrame({"n": [len(names)]})
        if "DISTINCT" in upper:
            return pd.DataFrame({"name": names})
        return df

    connector = MagicMock()
    connector.execute.side_effect = _execute

    result = calculate_columns_profiling(
        {"id": "t1", "name": "League", "schema_name": None},
        [{"name": "name", "data_type": "text"}],
        connector,
    )

    assert result["name"]["sample_values"] == names
    assert result["name"]["exhaustive"] is True
    # Enumerating the values does not stop it being reported as a key.
    assert result["name"]["is_unique"] is True


@patch("gsf.semantic.visit_enter.exact_cardinality_enabled", return_value=True)
@patch("gsf.semantic.visit_enter.store_column_exhaustiveness")
@patch("gsf.semantic.visit_enter.store_column_uniqueness")
@patch("gsf.semantic.visit_enter.store_column_sample_values")
def test_high_cardinality_column_is_not_probed(
    mock_store_samples: MagicMock,
    mock_store_unique: MagicMock,
    mock_store_exhaustive: MagicMock,
    _exact_enabled: MagicMock,
) -> None:
    """Keying the probe off the true count must not start probing wide columns."""
    df = pd.DataFrame({"uuid": [f"id-{i}" for i in range(4)]})

    def _execute(sql: str) -> pd.DataFrame:
        upper = sql.upper()
        if "COUNT(DISTINCT" in upper:
            return pd.DataFrame({"n": [56822], "nd": [56822]})
        if "COUNT(*)" in upper:
            return pd.DataFrame({"n": [56822]})
        if "DISTINCT" in upper:
            raise AssertionError("high-cardinality column must not be probed")
        return df

    connector = MagicMock()
    connector.execute.side_effect = _execute

    result = calculate_columns_profiling(
        {"id": "t1", "name": "cards", "schema_name": None},
        [{"name": "uuid", "data_type": "text"}],
        connector,
    )

    assert result["uuid"]["exhaustive"] is False
    assert len(result["uuid"]["sample_values"]) == 4


@patch("gsf.semantic.visit_enter.exact_cardinality_enabled", return_value=False)
@patch("gsf.semantic.visit_enter.store_column_date_formats")
@patch("gsf.semantic.visit_enter.store_column_exhaustiveness")
@patch("gsf.semantic.visit_enter.store_column_uniqueness")
@patch("gsf.semantic.visit_enter.store_column_sample_values")
def test_a_date_column_records_the_notation_it_stores(
    mock_store_samples: MagicMock,
    _mock_store_unique: MagicMock,
    _mock_store_exhaustive: MagicMock,
    mock_store_formats: MagicMock,
    _exact_enabled: MagicMock,
) -> None:
    """Dates keep their exclusion from sample values and gain a notation.

    The two travel together: withholding the values is what leaves the model
    with no way to see that ``financial.loan.date`` is written ``1995-03-24``
    rather than ``950324``, and the notation is how it finds out without
    putting concrete dates back into the description.
    """
    df = pd.DataFrame(
        {
            "date": ["1995-03-24", "1996-01-02", "1997-12-13"],
            "amount": [100, 200, 300],
        }
    )
    connector = MagicMock()
    connector.execute.return_value = df

    result = calculate_columns_profiling(
        {"id": "t1", "name": "loan", "schema_name": None},
        [
            {"name": "date", "data_type": "DATE"},
            {"name": "amount", "data_type": "INTEGER"},
        ],
        connector,
    )

    assert result["date"]["date_format"] == "YYYY-MM-DD"
    mock_store_formats.assert_called_once_with("t1", {"date": "YYYY-MM-DD"})
    # The dates themselves are still withheld, and a non-date column is not
    # given a notation it has no use for.
    assert "date" not in mock_store_samples.call_args[0][1]
    assert result["amount"]["date_format"] is None


@patch("gsf.semantic.visit_enter.exact_cardinality_enabled", return_value=False)
@patch("gsf.semantic.visit_enter.store_column_date_formats")
@patch("gsf.semantic.visit_enter.store_column_exhaustiveness")
@patch("gsf.semantic.visit_enter.store_column_uniqueness")
@patch("gsf.semantic.visit_enter.store_column_sample_values")
def test_a_date_column_of_mixed_notations_records_none(
    _mock_store_samples: MagicMock,
    _mock_store_unique: MagicMock,
    _mock_store_exhaustive: MagicMock,
    mock_store_formats: MagicMock,
    _exact_enabled: MagicMock,
) -> None:
    """Nothing is written when the sample does not agree with itself."""
    connector = MagicMock()
    connector.execute.return_value = pd.DataFrame(
        {"date": ["1995-03-24", "24/03/1995"]}
    )

    result = calculate_columns_profiling(
        {"id": "t1", "name": "loan", "schema_name": None},
        [{"name": "date", "data_type": "DATE"}],
        connector,
    )

    assert result["date"]["date_format"] is None
    mock_store_formats.assert_called_once_with("t1", {})


def test_profiling_from_columns_reads_stored_profile() -> None:
    """The stored profile is reused verbatim, so no re-profiling query is needed."""
    from gsf.semantic.visit_enter import _profiling_from_columns

    result = _profiling_from_columns(
        [
            {
                "name": "status",
                "sample_values": '["Active", "Closed"]',
                "is_unique": False,
                "exhaustive": True,
                "n_distinct": 2,
            },
            {
                "name": "created_at",
                "sample_values": None,
                "is_unique": True,
                "exhaustive": False,
                "date_format": "YYYY-MM-DD HH:MM:SS",
            },
        ]
    )

    assert result["status"] == {
        "sample_values": ["Active", "Closed"],
        "is_unique": False,
        "exhaustive": True,
        "n_distinct": 2,
        "date_format": None,
    }
    # A column with nothing stored still appears, with no values.
    assert result["created_at"]["sample_values"] == []
    assert result["created_at"]["is_unique"] is True
    # A graph ingested without the exact probe carries no count, which is what
    # stops a description from claiming a cardinality nobody measured.
    assert result["created_at"]["n_distinct"] is None
    # A date column stores no values, so the notation read back here is the only
    # thing the recompiled description can say about how to compare against it.
    assert result["created_at"]["date_format"] == "YYYY-MM-DD HH:MM:SS"


def test_profiling_from_columns_signals_a_graph_without_the_property() -> None:
    """An empty result tells the caller to fall back to profiling live."""
    from gsf.semantic.visit_enter import _profiling_from_columns

    assert _profiling_from_columns([{"name": "status", "sample_values": '["a"]'}]) == {}


@patch("gsf.semantic.visit_enter.calculate_columns_profiling")
@patch("gsf.semantic.visit_enter.merge_column_attribute")
@patch("gsf.semantic.visit_enter.merge_term")
@patch("gsf.semantic.visit_enter.suggest_potential_foreign_keys")
@patch("gsf.semantic.visit_enter.extract_term")
def test_process_table_prefers_the_stored_profile(
    mock_term: MagicMock,
    mock_fk_suggest: MagicMock,
    _mock_merge_term: MagicMock,
    _mock_merge_col_attr: MagicMock,
    mock_profile: MagicMock,
) -> None:
    """Ingest already profiled these columns, so no second table scan is issued."""
    from gsf.semantic.models import PotentialFkResult, TableTermsResult

    mock_fk_suggest.return_value = PotentialFkResult()
    mock_term.return_value = TableTermsResult(terms=[])

    table = {"id": "t1", "name": "orders", "description": "", "schema_name": "public"}
    ctx = {
        "columns": [
            {
                "name": "status",
                "data_type": "text",
                "sample_values": '["Active", "Closed"]',
                "is_unique": False,
                "exhaustive": True,
            }
        ],
        "fks": [],
    }

    process_table(table, ctx, domain_summary=None)

    mock_profile.assert_not_called()
    assert mock_fk_suggest.call_args[0][2]["status"]["exhaustive"] is True


@patch("gsf.semantic.visit_enter.merge_column_attribute")
@patch("gsf.semantic.visit_enter.merge_term")
@patch("gsf.semantic.visit_enter.suggest_potential_foreign_keys")
@patch("gsf.semantic.visit_enter.extract_term")
def test_process_table_writes_term_and_attributes(
    mock_term: MagicMock,
    mock_fk_suggest: MagicMock,
    mock_merge_term: MagicMock,
    mock_merge_col_attr: MagicMock,
) -> None:
    from gsf.semantic.models import (
        PotentialFkResult,
        TableTermsResult,
        TermAttributeAssignment,
        TermProposal,
    )

    mock_fk_suggest.return_value = PotentialFkResult()
    mock_term.return_value = TableTermsResult(
        terms=[
            TermProposal(
                name="Order",
                description="Order entity",
                attributes=[
                    TermAttributeAssignment(
                        source_column="amount",
                        display_name="Total Amount",
                    )
                ],
            )
        ]
    )

    table = {"id": "t1", "name": "orders", "description": "", "schema_name": "public"}
    ctx = {
        "columns": [{"name": "amount", "data_type": "numeric"}],
        "fks": [],
    }

    process_table(table, ctx, domain_summary=None)

    mock_merge_term.assert_called_once_with("Order", "Order entity", "t1", synonyms=[])
    mock_merge_col_attr.assert_called_once()


@patch("gsf.semantic.visit_enter.merge_column_attribute")
@patch("gsf.semantic.visit_enter.merge_term")
@patch("gsf.semantic.visit_enter.suggest_potential_foreign_keys")
@patch("gsf.semantic.visit_enter.extract_term")
def test_process_table_skips_fk_columns(
    mock_term: MagicMock,
    mock_fk_suggest: MagicMock,
    mock_merge_term: MagicMock,
    mock_merge_col_attr: MagicMock,
) -> None:
    from gsf.semantic.models import (
        PotentialFkResult,
        TableTermsResult,
        TermProposal,
    )

    mock_fk_suggest.return_value = PotentialFkResult()
    mock_term.return_value = TableTermsResult(
        terms=[TermProposal(name="Order", description="", attributes=[])]
    )

    table = {"id": "t1", "name": "orders", "description": "", "schema_name": ""}
    ctx = {
        "columns": [{"name": "customer_id", "data_type": "integer"}],
        "fks": [
            {
                "source_column": "customer_id",
                "target_table": "customers",
                "target_table_id": "t2",
            }
        ],
    }

    process_table(table, ctx, domain_summary=None)

    mock_merge_term.assert_not_called()


@patch("gsf.semantic.visit_enter.merge_column_attribute")
@patch("gsf.semantic.visit_enter.merge_term")
@patch("gsf.semantic.visit_enter.suggest_potential_foreign_keys")
@patch("gsf.semantic.visit_enter.extract_term")
def test_process_table_multiple_terms(
    mock_term: MagicMock,
    mock_fk_suggest: MagicMock,
    mock_merge_term: MagicMock,
    mock_merge_col_attr: MagicMock,
) -> None:
    from gsf.semantic.models import (
        PotentialFkResult,
        TableTermsResult,
        TermAttributeAssignment,
        TermProposal,
    )

    mock_fk_suggest.return_value = PotentialFkResult()
    mock_term.return_value = TableTermsResult(
        terms=[
            TermProposal(
                name="Order",
                description="Core order",
                attributes=[
                    TermAttributeAssignment(
                        source_column="amount", display_name="Total Amount"
                    )
                ],
            ),
            TermProposal(
                name="Audit Metadata",
                description="Audit fields",
                attributes=[
                    TermAttributeAssignment(
                        source_column="created_at", display_name="Created At"
                    )
                ],
            ),
        ]
    )

    table = {"id": "t1", "name": "orders", "description": "", "schema_name": "public"}
    ctx = {
        "columns": [
            {"name": "amount", "data_type": "numeric"},
            {"name": "created_at", "data_type": "timestamp"},
        ],
        "fks": [],
    }

    process_table(table, ctx, domain_summary=None)

    assert mock_merge_term.call_count == 2
    assert mock_merge_col_attr.call_count == 2
