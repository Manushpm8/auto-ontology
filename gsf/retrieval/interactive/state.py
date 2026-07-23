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
    clarify_history: list[dict] = field(default_factory=list)   # [{"q": ..., "a": ...}]
    phase: InteractivePhase = InteractivePhase.PHASE1_CLARIFY
    phase1_sql: Optional[str] = None
    phase1_question: Optional[str] = None    # working_question at phase1 submit
    latest_feedback: Optional[str] = None    # message from Bird :6002/submit
    path_state: dict = field(default_factory=dict)  # durable across GSF calls
    _pending_question: Optional[str] = None  # last AskUserAction (for apply_user_answer)
    data_retriever: Any = None
    semantic_retriever: Any = None
    connectors: list = field(default_factory=list)
