from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .types import InteractivePhase


@dataclass
class InteractiveSessionState:
    session_id: str
    task_id: str
    db_name: str
    db_schema: str
    external_kg: str
    original_question: str
    working_question: str
    max_clarify_turns: int = 5
    clarify_history: list[dict] = field(default_factory=list)   # [{"q": ..., "a": ...}]
    phase: InteractivePhase = InteractivePhase.PHASE1_CLARIFY
    phase1_sql: Optional[str] = None
    phase1_question: Optional[str] = None    # working_question at phase1 submit
    latest_feedback: Optional[str] = None    # message from Bird :6002/submit
    path_state: dict = field(default_factory=dict)  # durable across GSF calls
    _pending_question: Optional[str] = None  # last AskUserAction (for apply_user_answer)
    _cached_unresolvable: Optional[list] = None  # cached per working_question
    _cached_unresolvable_for: Optional[str] = None  # working_question at cache time
    _cached_resolved_hits: Optional[list] = None  # VDB resolved hits for current question
    _grounded_kg: Optional[str] = None  # relevant KB text extracted during coverage check
    _grounded_kg_for: Optional[str] = None  # working_question when _grounded_kg was set
    phase1_grounded_kg: str = ""  # snapshot of cumulative_grounded_kg at Phase 1 PROCEED, carried into Phase 2
    cumulative_grounded_kg: str = ""  # union of all _grounded_kg values seen this phase (never replaced, only grows)
    _vdb_resolved_hits: str = ""  # KB-formatted confident VDB resolutions (score<=0.63) for evidence
    data_retriever: Any = None
    semantic_retriever: Any = None
    connectors: list = field(default_factory=list)
