"""Schema-block rendering: what the generation prompt actually shows the model."""

from gsf.retrieval.text_to_sql.agents.sql_from_semantic import format_tables_for_prompt


def _render(**col) -> str:
    return format_tables_for_prompt(
        [{"name": "orders", "columns": [{"name": "c", "data_type": "TEXT", **col}]}],
        target_db="db",
    )


def test_the_row_count_is_rendered_when_known() -> None:
    """Table size decides grain questions no description answers.

    Equal counts imply a 1:1 relationship, so a join cannot fan out; a large gap
    means it will, and that a plain COUNT(*) afterwards counts the wrong entity.
    """
    rendered = format_tables_for_prompt(
        [
            {"name": "client", "n_rows": 5369, "columns": []},
            {"name": "trans", "n_rows": 1056320, "columns": []},
        ],
        target_db="db",
    )
    assert "Rows: 5,369" in rendered
    assert "Rows: 1,056,320" in rendered


def test_an_empty_table_still_reports_its_size() -> None:
    """Zero is the most actionable count there is — do not build a query here."""
    rendered = format_tables_for_prompt(
        [{"name": "staging", "n_rows": 0, "columns": []}], target_db="db"
    )
    assert "Rows: 0" in rendered


def test_no_row_count_is_rendered_for_a_graph_without_one() -> None:
    """Ingested before the count existed, or a table whose COUNT(*) failed."""
    rendered = format_tables_for_prompt(
        [{"name": "orders", "columns": []}], target_db="db"
    )
    assert "Rows:" not in rendered


def test_values_already_in_the_description_are_not_repeated() -> None:
    """Candidate expansion sends both fields; printing both duplicates every value.

    613 of 798 BIRD columns arrive this way, so the redundant field was a quarter
    of the rendered schema block — and it is re-rendered for every candidate.
    """
    rendered = _render(
        description="bank of the recipient — one of: KL, GH",
        sample_values='["KL", "GH"]',
    )
    assert "one of: KL, GH" in rendered
    assert "sample values:" not in rendered


def test_open_samples_in_the_description_are_not_repeated() -> None:
    rendered = _render(
        description="the id of the account — samples: 1, 706",
        sample_values='["1", "706"]',
    )
    assert rendered.count("706") == 1


def test_values_still_render_when_the_description_omits_them() -> None:
    """A foreign key has no ColumnAttribute, so its description carries no values.

    The field is that column's only channel to the prompt and must survive — in the
    same form the compile path writes, not as raw JSON, so the model reads one
    convention across FK and non-FK columns alike.
    """
    rendered = _render(description="id number of client", sample_values='["1", "704"]')
    assert "id number of client — samples: 1, 704" in rendered


def test_values_render_when_there_is_no_description() -> None:
    rendered = _render(sample_values='["1"]')
    assert "samples: 1" in rendered


def test_an_fk_column_reports_its_cardinality() -> None:
    """The count is what says whether a join on this column can fan out.

    93 of 125 BIRD columns without a ColumnAttribute are FK sources, and they
    reached the prompt as a bare sentence: no values, no cardinality.
    """
    rendered = _render(
        description="the id of the away team", sample_values='["5", "9"]', n_distinct=299
    )
    assert "299 distinct values" in rendered


def test_a_unique_fk_column_is_marked_as_a_key() -> None:
    rendered = _render(description="client id", n_distinct=5369, is_unique=True)
    assert "unique per row" in rendered
    assert "distinct values" not in rendered


def test_a_closed_enum_is_stated_as_a_constraint() -> None:
    """"one of" is a permitted set; "samples" is only examples."""
    rendered = _render(
        description="account type", sample_values='["OWNER", "USER"]',
        exhaustive=True, n_distinct=2,
    )
    assert "one of: OWNER, USER" in rendered


def test_nothing_is_claimed_for_a_column_with_no_profile() -> None:
    """A graph ingested before the probe existed must not gain invented statistics."""
    rendered = _render(description="a plain column")
    assert "distinct values" not in rendered
    assert "unique per row" not in rendered
    assert "samples:" not in rendered
