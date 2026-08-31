from unittest.mock import MagicMock, patch

from gsf.retrieval.interactive.coordinator import (
    _classify_message,
    _apply_debug_seed,
    _apply_follow_up_seed,
    apply_user_answer,
    step,
)
from gsf.retrieval.interactive.types import SubmitSQLAction, TurnType
from gsf.retrieval.interactive.state import InteractiveSessionState


def _make_session(**kwargs):
    defaults = dict(
        session_id="s1",
        task_id="t1",
        db_name="alien",
        db_schema="TABLE aliens ...",
        external_kg="[]",
        original_question="test question",
        working_question="test question",
    )
    defaults.update(kwargs)
    return InteractiveSessionState(**defaults)


def test_classify_debug_executable():
    msg = "Your SQL is not executable: syntax error at line 3\nPlease fix and call submit_sql."
    assert _classify_message(msg) == TurnType.DEBUG


def test_classify_debug_incorrect():
    msg = "Your SQL is not correct. You have one more chance. Please fix and call submit_sql."
    assert _classify_message(msg) == TurnType.DEBUG


def test_classify_follow_up():
    msg = "Phase 1 is complete. Here is a follow-up question:\n\nShow totals.\n\nGenerate the PostgreSQL query and call submit_sql."
    assert _classify_message(msg) == TurnType.FOLLOW_UP


def test_classify_initial():
    msg = "User Query:\nHow many aliens?\n\nYou have 3 clarification turns."
    assert _classify_message(msg) == TurnType.INITIAL


def test_debug_seed_sets_resume_from():
    sess = _make_session()
    _apply_debug_seed(sess, "Your SQL is not correct.")
    assert sess.path_state["_resume_from"] == "reconstruct_sql"
    assert sess.path_state["sql_attempts"] == 0
    assert sess.path_state["error_analysis_done"] is False


def test_debug_seed_sets_error():
    sess = _make_session()
    msg = "Your SQL is not executable: column X does not exist\nPlease fix and call submit_sql."
    _apply_debug_seed(sess, msg)
    # Only the first line after the marker is extracted as the DB error —
    # the trailing "Please fix..." instruction is not part of it.
    assert sess.path_state["error"] == "column X does not exist"


def test_step_routes_exec_error_through_debug_path():
    """End-to-end (no live DB/orchestrator): mirrors how eval_bird_interact's
    _build_debug_message() turns a real submit_sql exec-error response into the
    orchestrator message, then drives it through the real coordinator.step() to
    confirm the 'not executable' branch — not the generic 'not correct' hint
    branch — is what actually runs.
    """
    sess = _make_session()
    sess.path_state["sql_code"] = (
        "SELECT bad_column FROM aliens"  # prior failed attempt
    )

    last_submit_raw = '[exec_err_flg] column "bad_column" does not exist'
    actual_error = last_submit_raw.split("[exec_err_flg]", 1)[1].strip()
    orchestrator_message = (
        f"Your SQL is not executable: {actual_error}\nPlease fix and call submit_sql."
    )

    with patch(
        "gsf.retrieval.interactive.coordinator._run_sql_generation",
        return_value="SELECT column FROM aliens",
    ) as mock_gen:
        action = step(sess, orchestrator_message)

    mock_gen.assert_called_once()
    assert isinstance(action, SubmitSQLAction)
    assert action.sql == "SELECT column FROM aliens"
    # The real DB error was extracted and seeded, not the generic wrong-result hint.
    assert sess.path_state["error"] == actual_error
    assert sess.path_state["_resume_from"] == "reconstruct_sql"


def test_follow_up_seed_clears_sql_keys():
    sess = _make_session()
    sess.path_state["sql_code"] = "SELECT 1"
    sess.path_state["normalized_question"] = "old"
    msg = "Phase 1 is complete. Here is a follow-up question:\n\nShow totals.\n\nGenerate the PostgreSQL query and call submit_sql."
    _apply_follow_up_seed(sess, msg)
    assert "sql_code" not in sess.path_state
    assert "normalized_question" not in sess.path_state


def test_apply_user_answer_merges_question():
    sess = _make_session()
    sess._pending_question = "Which year?"
    mock_llm = MagicMock()
    mock_llm.invoke.return_value.content = "How many aliens were observed in 2023?"
    with (
        patch("gsf.retrieval.interactive.coordinator._get_llm", return_value=mock_llm),
        # merge_clarification is called with _get_fast_llm(), not _get_llm() —
        # both must be mocked or the real (unmocked) LLM client gets used instead.
        patch(
            "gsf.retrieval.interactive.coordinator._get_fast_llm", return_value=mock_llm
        ),
    ):
        apply_user_answer(sess, "2023")
    assert sess.working_question == "How many aliens were observed in 2023?"
    assert len(sess.clarify_history) == 1
    assert sess.clarify_history[0] == {"q": "Which year?", "a": "2023"}
    assert sess._pending_question is None


def test_apply_user_answer_no_pending_still_merges():
    """Even with no pending question, apply_user_answer should not crash."""
    sess = _make_session()
    sess._pending_question = None
    mock_llm = MagicMock()
    mock_llm.invoke.return_value.content = "test question"
    with patch("gsf.retrieval.interactive.coordinator._get_llm", return_value=mock_llm):
        apply_user_answer(sess, "some answer")  # should not raise
