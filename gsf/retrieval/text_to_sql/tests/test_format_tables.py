"""Schema-block rendering: what the generation prompt actually shows the model."""

from gsf.retrieval.text_to_sql.agents.sql_from_semantic import format_tables_for_prompt


def _render(**col) -> str:
    return format_tables_for_prompt(
        [{"name": "orders", "columns": [{"name": "c", "data_type": "TEXT", **col}]}],
        target_db="db",
    )


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

    The field is that column's only channel to the prompt and must survive.
    """
    rendered = _render(description="id number of client", sample_values='["1", "704"]')
    assert "sample values: [\"1\", \"704\"]" in rendered


def test_values_render_when_there_is_no_description() -> None:
    rendered = _render(sample_values='["1"]')
    assert "sample values:" in rendered
