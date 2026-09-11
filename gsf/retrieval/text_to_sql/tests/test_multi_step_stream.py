# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Answering a decomposed request: one full graph pass per sub-question.

The loop lives outside the graph, so what these tests pin down is the handover
between passes — which question each pass is asked, what evidence it is given,
and that nothing else leaks across.
"""

import importlib
import json
from types import ModuleType, SimpleNamespace
from typing import Any, Iterator, cast

import pytest

from gsf.retrieval.text_to_sql.state import TextToSQLPayload
from gsf.utils import llm_invoke


@pytest.fixture(name="main")
def main_fixture(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import the agent entry point without a configured LLM.

    ``main`` builds its reasoning client at import time and lets an unset
    ``REASONING_API_KEY`` raise, so importing it at module scope would fail
    collection on any machine without credentials.
    """
    monkeypatch.setattr(llm_invoke, "get_llm_client", lambda **_kwargs: None)
    module = importlib.import_module("gsf.retrieval.text_to_sql.main")
    monkeypatch.setattr(module, "_COMBINED_PRECHECK_IN_GRAPH", False)
    return module


def _answer_chunk(sql: str, rows: list[dict]) -> dict[str, Any]:
    """A pass reaching its final node with an answer attached."""
    return {
        "format_and_respond": {
            "path_state": {
                "thoughts_log": [{"node": "format_and_respond", "text": "Done."}],
                "final_response": {
                    "response": f"Ran {sql}",
                    "sql_code": sql,
                    "sql_response_from_db": [json.dumps(rows)],
                },
            }
        }
    }


class _FakeGraph:
    """Stands in for the compiled graph, recording the state of every pass."""

    def __init__(self, *answers: tuple[str, list[dict]]) -> None:
        self._answers = list(answers)
        self.states: list[dict] = []

    def stream(
        self, state: dict, stream_mode: Any = None, config: Any = None
    ) -> Iterator[tuple[str, Any]]:
        # Copied because the graph mutates path_state in place; keeping the
        # live object would make every recorded pass look like the last one.
        self.states.append({**state, "path_state": dict(state.get("path_state") or {})})
        sql, rows = self._answers[len(self.states) - 1]
        yield ("custom", {"type": "step_start", "node": "format_and_respond"})
        yield ("updates", _answer_chunk(sql, rows))


def _plan(main: ModuleType, monkeypatch: pytest.MonkeyPatch, *steps: str) -> None:
    """Turn the feature on and pin the planner's output to *steps*."""
    monkeypatch.setattr(main, "is_decomposition_enabled", lambda: True)
    monkeypatch.setattr(
        main,
        "decomposition_agent",
        SimpleNamespace(
            execute=lambda _state: {"path_state": {"sub_questions": list(steps)}}
        ),
    )


def _base_state(question: str = "whole question") -> dict:
    return {
        "initial_question": question,
        "evidence": "",
        "messages": ["<system>", "<human>"],
        "path_state": {"processing_question": question, "target_db": "wwi"},
        "decision": "",
    }


def _run(main: ModuleType, question: str = "whole question") -> list[dict]:
    return list(
        main.stream_agent_response(cast(TextToSQLPayload, {"question": question}))
    )


def test_each_sub_question_gets_its_own_pass(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _FakeGraph(("SELECT 1", [{"min": 8110}]), ("SELECT 2", [{"gap": 3000}]))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "What is the lowest salary?", "What is the gap?")

    _run(main)

    assert len(graph.states) == 2
    assert [s["path_state"]["processing_question"] for s in graph.states] == [
        "What is the lowest salary?",
        "What is the gap?",
    ]


def test_an_earlier_answer_becomes_the_next_steps_evidence(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _FakeGraph(
        ("SELECT MIN(A11) FROM district", [{"min": 8110}]),
        ("SELECT MAX(A11) - 8110 FROM district", [{"gap": 3000}]),
    )
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "What is the lowest salary?", "What is the gap?")

    _run(main)

    first, second = graph.states
    assert first["evidence"] == ""
    assert "### Step 1: What is the lowest salary?" in second["evidence"]
    assert "SELECT MIN(A11) FROM district" in second["evidence"]
    assert '{"min": 8110}' in second["evidence"]


def test_caller_evidence_survives_into_every_step(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]), ("SELECT 2", [{"b": 2}]))
    base = {**_base_state(), "evidence": "Gap = max - min."}
    monkeypatch.setattr(main, "_build_state", lambda _payload: base)
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "First?", "Second?")

    _run(main)

    assert all("Gap = max - min." in s["evidence"] for s in graph.states)


def test_working_memory_does_not_leak_between_steps(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each pass starts clean.

    ``failed_attempts`` and friends describe the question the previous pass
    answered; carrying them would have step two start inside step one's retry
    budget. Only what ``build_step_evidence`` renders crosses the boundary.
    """
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]), ("SELECT 2", [{"b": 2}]))
    base = _base_state()
    monkeypatch.setattr(main, "_build_state", lambda _payload: base)
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "First?", "Second?")

    # Dirty the first pass only, the way a real one would, and let the second
    # run untouched so what it starts with is what the loop handed it.
    original_stream = graph.stream

    def dirty_stream(state: dict, **kwargs: Any) -> Iterator[tuple[str, Any]]:
        if not graph.states:
            state["path_state"]["failed_attempts"] = [{"error": "boom"}]
            state["path_state"]["sql_code"] = "SELECT stale"
        yield from original_stream(state, **kwargs)

    monkeypatch.setattr(graph, "stream", dirty_stream)

    _run(main)

    second = graph.states[1]["path_state"]
    assert "failed_attempts" not in second
    assert "sql_code" not in second
    # Session-level context does carry, since it is not about the question.
    assert second["target_db"] == "wwi"


def test_only_the_final_step_produces_a_result(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]), ("SELECT 2", [{"gap": 3000}]))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "First?", "Second?")

    events = _run(main)
    results = [e for e in events if e["type"] == "result"]

    assert len(results) == 1
    assert results[0]["answer"]["sql_code"] == "SELECT 2"


def test_step_events_carry_their_position_in_the_plan(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]), ("SELECT 2", [{"b": 2}]))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "First?", "Second?")

    events = _run(main)
    graph_steps = [
        e
        for e in events
        if e["type"] == "step" and e["node"] != main.DECOMPOSITION_NODE
    ]

    assert {e["step_total"] for e in graph_steps} == {2}
    assert [e["step_index"] for e in graph_steps] == [1, 1, 2, 2]
    assert graph_steps[0]["step_question"] == "First?"
    assert graph_steps[-1]["step_question"] == "Second?"


def test_the_plan_is_announced_as_a_step(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]), ("SELECT 2", [{"b": 2}]))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "First?", "Second?")

    events = _run(main)
    planning = [e for e in events if e.get("node") == main.DECOMPOSITION_NODE]

    assert [e["phase"] for e in planning] == ["start", "end"]
    assert "Answering in 2 steps" in planning[1]["thought"]
    assert "1. First?" in planning[1]["thought"]


def test_thoughts_from_every_step_reach_the_final_answer(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]), ("SELECT 2", [{"b": 2}]))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "First?", "Second?")

    events = _run(main)
    thoughts = [e for e in events if e["type"] == "result"][0]["answer"]["thoughts"]

    assert "Step 1 of 2: First?" in thoughts
    assert "Step 2 of 2: Second?" in thoughts


def test_a_single_step_plan_runs_one_untagged_pass(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One step means one pass, with no step tags to confuse a client."""
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "How many shipments shipped last month?")

    events = _run(main)
    graph_steps = [
        e
        for e in events
        if e["type"] == "step" and e["node"] != main.DECOMPOSITION_NODE
    ]

    assert len(graph.states) == 1
    assert all("step_index" not in e for e in graph_steps)


def test_a_genuine_step_becomes_its_own_reference_question(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One step answers one step, not the whole request.

    Leaving the multi-part question as ``initial_question`` would have intent
    validation reject each step's SQL for failing to answer the other steps,
    burning the reconstruction budget on a correct query.
    """
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]), ("SELECT 2", [{"b": 2}]))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "First?", "Second?")

    _run(main)

    assert [s["initial_question"] for s in graph.states] == ["First?", "Second?"]


def test_a_single_step_restatement_is_what_gets_answered(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The useful part of a one-step plan is the scope it makes explicit.

    The planner routinely turns "less than average X" into "less than the
    average X among <the population the question already named>". Discarding
    that would throw away the main thing planning buys on questions that do not
    split.
    """
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    restatement = "What is the average salary among districts in 2013?"
    _plan(main, monkeypatch, restatement)

    _run(main)

    assert graph.states[0]["path_state"]["processing_question"] == restatement


def test_a_restatement_leaves_the_users_own_words_as_the_reference(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Intent validation must still measure the SQL against what was asked.

    ``get_original_question`` reads ``initial_question``, so keeping it is what
    catches a restatement that quietly dropped a constraint.
    """
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "A restatement that is not the original")

    _run(main)

    assert graph.states[0]["initial_question"] == "whole question"


def test_a_verbatim_single_step_plan_changes_nothing(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]))
    base = _base_state()
    monkeypatch.setattr(main, "_build_state", lambda _payload: base)
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "whole question")

    events = _run(main)
    planning = [e for e in events if e.get("node") == main.DECOMPOSITION_NODE]

    assert graph.states[0]["path_state"]["processing_question"] == "whole question"
    assert graph.states[0]["initial_question"] == "whole question"
    # Nothing to report when the planner left the question alone.
    assert planning[1]["thought"] is None


def test_a_restatement_is_announced(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "The clearer phrasing")

    events = _run(main)
    planning = [e for e in events if e.get("node") == main.DECOMPOSITION_NODE]
    thoughts = [e for e in events if e["type"] == "result"][0]["answer"]["thoughts"]

    assert planning[1]["thought"] == "Read the question as: The clearer phrasing"
    assert "Read the question as: The clearer phrasing" in thoughts


def test_planning_is_absent_entirely_when_the_feature_is_off(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default path must be indistinguishable from before this existed."""
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    monkeypatch.setattr(main, "is_decomposition_enabled", lambda: False)

    def fail(_state: dict) -> dict:
        raise AssertionError("planner must not be called when the feature is off")

    monkeypatch.setattr(main, "decomposition_agent", SimpleNamespace(execute=fail))

    events = _run(main)

    assert len(graph.states) == 1
    assert not [e for e in events if e.get("node") == main.DECOMPOSITION_NODE]
    assert graph.states[0]["path_state"]["processing_question"] == "whole question"


def test_empty_steps_fall_back_to_answering_the_whole_question(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No rows means a step probably narrowed the question past the answer.

    The steps cannot tell: each was resolved against its own population, and
    every one of them succeeded. Only the unsplit question still carries the
    constraint that went missing, so it gets the last word.
    """
    graph = _FakeGraph(
        ("SELECT 1", [{"school": "Gompers"}]),
        ("SELECT 2 WHERE school = 'Gompers'", []),
        ("SELECT 3", [{"county": "Los Angeles"}]),
    )
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "Which school?", "What county is it in?")

    events = _run(main)
    results = [e for e in events if e["type"] == "result"]

    assert len(graph.states) == 3
    # The retry is the request as asked, not the step that came back empty.
    assert graph.states[2]["path_state"]["processing_question"] == "whole question"
    assert len(results) == 1
    assert results[0]["answer"]["sql_code"] == "SELECT 3"


def test_the_fallback_pass_is_tagged_so_a_client_can_tell_it_apart(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]), ("SELECT 2", []), ("SELECT 3", [{}]))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "First?", "Second?")

    events = _run(main)
    retried = [e for e in events if e.get("retry") == "single_pass"]

    assert retried and all("step_index" not in e for e in retried)


def test_steps_that_found_rows_are_left_alone(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback costs a whole extra pass, so only an empty result buys it.

    An earlier step returning nothing is not enough on its own — the last step
    is the one that answers the request.
    """
    graph = _FakeGraph(("SELECT 1", []), ("SELECT 2", [{"gap": 3000}]))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "First?", "Second?")

    events = _run(main)

    assert len(graph.states) == 2
    assert not [e for e in events if e.get("retry")]


def test_a_single_pass_that_finds_nothing_has_nothing_to_fall_back_to(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unsplit question is already the widest form of itself."""
    graph = _FakeGraph(("SELECT 1", []))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "Only one step?")

    _run(main)

    assert len(graph.states) == 1


def test_a_broken_fallback_leaves_the_stepped_answer_standing(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retry is an optimization; failing it must not fail the request."""
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]), ("SELECT 2", []))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "First?", "Second?")

    original_stream = graph.stream

    def explode_on_retry(state: dict, **kwargs: Any) -> Iterator[tuple[str, Any]]:
        if len(graph.states) == 2:
            graph.states.append(state)
            raise RuntimeError("database went away")
        yield from original_stream(state, **kwargs)

    monkeypatch.setattr(graph, "stream", explode_on_retry)

    events = _run(main)
    results = [e for e in events if e["type"] == "result"]

    assert not [e for e in events if e["type"] == "error"]
    assert len(results) == 1
    assert results[0]["answer"]["sql_code"] == "SELECT 2"


def test_a_failing_step_reports_the_node_that_broke(
    main: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _FakeGraph(("SELECT 1", [{"a": 1}]), ("SELECT 2", [{"b": 2}]))
    monkeypatch.setattr(main, "_build_state", lambda _payload: _base_state())
    monkeypatch.setattr(main, "app", graph)
    _plan(main, monkeypatch, "First?", "Second?")

    def explode(state: dict, **_kwargs: Any) -> Iterator[tuple[str, Any]]:
        graph.states.append(state)
        yield ("custom", {"type": "step_start", "node": "execute_sql_query"})
        raise RuntimeError("database went away")

    monkeypatch.setattr(graph, "stream", explode)

    events = _run(main)
    errors = [e for e in events if e["type"] == "error"]

    assert len(errors) == 1
    assert errors[0]["node"] == "execute_sql_query"
    assert "database went away" in errors[0]["message"]
