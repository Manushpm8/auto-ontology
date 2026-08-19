# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Flag parsing must reproduce what the per-module readers did before.

The boolean flags used to be parsed four different ways. Two of those dialects
covered every flag but four, and this file pins the claim that the single parser
in :mod:`gsf.flags` agrees with both of them on every input -- including values
that were never valid, like ``maybe``. The four exceptions were bugs; their new
answers are asserted explicitly.
"""

from __future__ import annotations

import pytest

from gsf import flags

# Every distinct token the old parsers could disagree on, plus junk.
INPUTS = ("", " ", "0", "1", "off", "on", "no", "yes", "false", "true", "maybe", "2")


def old_dialect_a(raw: str) -> bool:
    """Parser used by every flag that defaulted to off (``""`` counted falsy)."""
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def old_dialect_b(raw: str) -> bool:
    """Parser used by every flag that defaulted to on, incl. ``read_env_bool``."""
    return raw.strip().lower() not in {"0", "false", "no", "off"}


@pytest.mark.parametrize("raw", INPUTS)
def test_bool_flag_matches_old_default_off_dialect(monkeypatch, raw):
    monkeypatch.setenv("BIRD_TEST_OFF", raw)
    flag = flags.BoolFlag("BIRD_TEST_OFF", default=False)
    assert flag() == old_dialect_a(raw)


@pytest.mark.parametrize("raw", INPUTS)
def test_bool_flag_matches_old_default_on_dialect(monkeypatch, raw):
    monkeypatch.setenv("BIRD_TEST_ON", raw)
    flag = flags.BoolFlag("BIRD_TEST_ON", default=True)
    assert flag() == old_dialect_b(raw)


@pytest.mark.parametrize("default", (True, False))
def test_unset_yields_the_default(monkeypatch, default):
    monkeypatch.delenv("BIRD_TEST_UNSET", raising=False)
    assert flags.BoolFlag("BIRD_TEST_UNSET", default=default)() is default


# --- flags whose old parsers were buggy -----------------------------------


def test_off_now_disables_prompt_sql_attrs(monkeypatch):
    """Also omitted ``off``, so the ablation could not be turned off."""
    monkeypatch.setenv("BIRD_PROMPT_SQL_ATTRS", "off")
    assert flags.PROMPT_SQL_ATTRS() is False


def test_on_now_enables_schema_slots(monkeypatch):
    """Used a truthy set of {1,true,yes} that silently rejected ``on``."""
    monkeypatch.setenv("BIRD_SCHEMA_SLOTS", "on")
    assert flags.SCHEMA_SLOTS() is True


def test_entity_columns_agrees_across_former_read_sites(monkeypatch):
    monkeypatch.setenv("BIRD_ENTITY_COLUMNS", "")
    assert flags.ENTITY_COLUMNS() is False
    monkeypatch.setenv("BIRD_ENTITY_COLUMNS", "1")
    assert flags.ENTITY_COLUMNS() is True


# --- numeric parsing ------------------------------------------------------


def test_int_flag_clamps_to_range(monkeypatch):
    monkeypatch.setenv("BIRD_HARVEST_FORCE_K", "99")
    assert flags.HARVEST_FORCE_K() == 5
    monkeypatch.setenv("BIRD_HARVEST_FORCE_K", "0")
    assert flags.HARVEST_FORCE_K() == 1


def test_int_flag_falls_back_on_junk(monkeypatch):
    monkeypatch.setenv("BIRD_NCAND", "not-a-number")
    assert flags.NCAND() == 1


def test_synthetic_n_no_longer_crashes_at_import(monkeypatch):
    """``BIRD_SYNTHETIC_N=abc`` used to raise ValueError while importing models."""
    monkeypatch.setenv("BIRD_SYNTHETIC_N", "abc")
    assert flags._SYNTHETIC_N() == 3
    monkeypatch.setenv("BIRD_SYNTHETIC_N", "1")
    assert flags._SYNTHETIC_N() == 2


def test_float_flag_falls_back_on_junk(monkeypatch):
    monkeypatch.setenv("BIRD_NCAND_TEMP", "warm")
    assert flags.NCAND_TEMP() == pytest.approx(0.8)
    monkeypatch.setenv("BIRD_NCAND_TEMP", "0.3")
    assert flags.NCAND_TEMP() == pytest.approx(0.3)


# --- BIRD_ORACLE_PRUNE_COLS used to be read twice with different defaults --


@pytest.mark.parametrize(
    "raw,expected",
    [("", None), ("abc", None), ("-1", None), ("0", 0), ("5", 5), ("12", 12)],
)
def test_oracle_prune_cols(monkeypatch, raw, expected):
    monkeypatch.setenv("BIRD_ORACLE_PRUNE_COLS", raw)
    assert flags.ORACLE_PRUNE_COLS() == expected


def test_oracle_prune_cols_unset(monkeypatch):
    monkeypatch.delenv("BIRD_ORACLE_PRUNE_COLS", raising=False)
    assert flags.ORACLE_PRUNE_COLS() is None


# --- lists and sets -------------------------------------------------------


def test_csv_set_lowercases_by_default(monkeypatch):
    monkeypatch.setenv("BIRD_OPEN_REASONING_DBS", " Formula_1 , California_Schools ,")
    assert flags.OPEN_REASONING_DBS() == {"formula_1", "california_schools"}


def test_projection_order_dbs_stays_case_sensitive(monkeypatch):
    """This one matched ``str(db_id)`` verbatim, so casing must survive."""
    monkeypatch.setenv("BIRD_PROJECTION_ORDER_DBS", "California_Schools, *")
    assert flags.PROJECTION_ORDER_DBS() == {"California_Schools", "*"}


def test_csv_list_keeps_order(monkeypatch):
    monkeypatch.setenv("BIRD_SLOT_PLAN", "baseline, query_plan ,decomposition")
    assert flags.SLOT_PLAN() == ("baseline", "query_plan", "decomposition")


def test_csv_flags_are_empty_when_unset(monkeypatch):
    monkeypatch.delenv("BIRD_SLOT_PLAN", raising=False)
    monkeypatch.delenv("BIRD_OPEN_REASONING_DBS", raising=False)
    assert flags.SLOT_PLAN() == ()
    assert flags.OPEN_REASONING_DBS() == set()


def test_float_list_flag(monkeypatch):
    monkeypatch.setenv("BIRD_SLOT_VOTE_WEIGHTS_JSON", "[1.0, 0.1, 0.1]")
    assert flags.SLOT_VOTE_WEIGHTS_JSON() == [1.0, 0.1, 0.1]
    monkeypatch.setenv("BIRD_SLOT_VOTE_WEIGHTS_JSON", "not json")
    assert flags.SLOT_VOTE_WEIGHTS_JSON() == []


# --- modes ----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", "smart"),
        ("smart", "smart"),
        ("needed", "smart"),
        ("all", "always"),
        ("always", "always"),
        ("1", "always"),
        ("empty", "broken"),
        ("broken", "broken"),
        ("dissent", "disagree"),
        ("nonsense", "smart"),
    ],
)
def test_verify_revise_when_modes(monkeypatch, raw, expected):
    monkeypatch.setenv("BIRD_VERIFY_REVISE_WHEN", raw)
    assert flags.VERIFY_REVISE_WHEN() == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", "off"),
        ("0", "off"),
        ("solo", "solo_acc"),
        ("accuracy", "solo_acc"),
        ("1", "solo_acc"),
        ("on", "solo_acc"),
        ("vq", "voter_q"),
        ("voter_quality", "voter_q"),
        ("nonsense", "off"),
    ],
)
def test_slot_vote_weight_modes(monkeypatch, raw, expected):
    monkeypatch.setenv("BIRD_SLOT_VOTE_WEIGHTS", raw)
    assert flags.SLOT_VOTE_WEIGHTS() == expected


@pytest.mark.parametrize(
    "select,judge,expected",
    [
        ("", "0", "majority"),
        ("", "1", "llm_judge"),
        ("majority", "1", "majority"),
        ("rerank", "0", "rerank"),
        ("judge", "0", "llm_judge"),
        ("llm_judge", "0", "llm_judge"),
        ("nonsense", "1", "llm_judge"),
        ("nonsense", "0", "majority"),
    ],
)
def test_sql_select_mode(monkeypatch, select, judge, expected):
    monkeypatch.setenv("BIRD_SQL_SELECT", select)
    monkeypatch.setenv("BIRD_SQL_JUDGE", judge)
    assert flags.sql_select_mode() == expected


# --- model fallback chain -------------------------------------------------


def test_wrongness_model_prefers_the_bird_name(monkeypatch):
    monkeypatch.setenv("BIRD_WRONGNESS_MODEL", "bird-judge")
    monkeypatch.setenv("ENTITY_EXTRACTION_MODEL", "extract")
    assert flags.WRONGNESS_MODEL() == "bird-judge"


def test_wrongness_model_falls_through_legacy_names(monkeypatch):
    monkeypatch.delenv("BIRD_WRONGNESS_MODEL", raising=False)
    monkeypatch.setenv("ENTITY_EXTRACTION_MODEL", "  ")
    monkeypatch.setenv("JUDGE_MODEL_NAME", "legacy-judge")
    assert flags.WRONGNESS_MODEL() == "legacy-judge"


def test_wrongness_model_is_none_when_unset(monkeypatch):
    for name in ("BIRD_WRONGNESS_MODEL", "ENTITY_EXTRACTION_MODEL", "JUDGE_MODEL_NAME"):
        monkeypatch.delenv(name, raising=False)
    assert flags.WRONGNESS_MODEL() is None


def test_harvest_force_model_falls_back_to_wrongness_model(monkeypatch):
    monkeypatch.delenv("BIRD_HARVEST_FORCE_MODEL", raising=False)
    monkeypatch.setenv("BIRD_WRONGNESS_MODEL", "shared-judge")
    assert flags.HARVEST_FORCE_MODEL() == "shared-judge"
    monkeypatch.setenv("BIRD_HARVEST_FORCE_MODEL", "own-judge")
    assert flags.HARVEST_FORCE_MODEL() == "own-judge"


# --- registry -------------------------------------------------------------


def test_every_flag_is_registered_once():
    names = [flag.name for flag in flags.REGISTRY]
    assert len(names) == len(set(names)), "a flag name is declared twice"


def test_registry_covers_only_bird_flags_and_legacy_aliases():
    legacy = {"ENTITY_EXTRACTION_MODEL", "JUDGE_MODEL_NAME"}
    assert {n for n in flags.names() if not n.startswith("BIRD_")} == legacy


def test_describe_lists_every_flag():
    described = flags.describe()
    for flag in flags.REGISTRY:
        assert flag.name in described
