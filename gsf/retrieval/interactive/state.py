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
    _json_shared_notes: list[str] = field(default_factory=list)  # collision-resolution notes ("shared column" + "resolved to these distinct columns") — accumulated across turns (never replaced), injected verbatim into Evidence
    _collision_hit_verdicts: dict = field(default_factory=dict)  # column id -> True/False, persists collision-resolution verdicts across turns so a later collision on the same id (even under different entity-name phrasing) skips re-deriving it
    _cached_vdb_only_norms: set = field(default_factory=set)  # VDB-resolved but KB-uncovered norms
    # KB+VDB disambiguation bookkeeping, accumulated across turns within a phase (see
    # clarify.py) — a normalized term's KB coverage and VDB hit are re-derived fresh
    # each turn and can land on different turns, so these persist both signals instead
    # of only checking the current turn's snapshot. Reset at Phase 1→2 handoff, same
    # as cumulative_grounded_kg, since Phase 2 is a different question.
    _ever_kb_covered_norms: set = field(default_factory=set)
    _ever_vdb_hit_norms: dict = field(default_factory=dict)  # norm -> (col_text, score)
    _ever_term_to_kb_entry: dict = field(default_factory=dict)  # norm -> (entry_name, entry_text)
    _grounded_kg: Optional[str] = None  # relevant KB text extracted during coverage check
    _grounded_kg_for: Optional[str] = None  # working_question when _grounded_kg was set
    phase1_grounded_kg: str = ""  # snapshot of cumulative_grounded_kg at Phase 1 PROCEED, carried into Phase 2
    cumulative_grounded_kg: str = ""  # union of all _grounded_kg values seen this phase (never replaced, only grows)
    incomplete_formula_terms: list = field(default_factory=list)  # [(term, what_is_missing)]
    persistent_unresolved: list[str] = field(default_factory=list)  # terms never resolved by KB/VDB; pruned after each answered turn
    resolved_persistent: set[str] = field(default_factory=set)  # terms pruned from persistent; blocked from re-accumulation
    initial_extracted_entities: list[str] = field(default_factory=list)  # all entities extracted on the first clarify call (turn 0)
    external_kg_children_map: dict[str, list[str]] = field(default_factory=dict)  # parent entry name → [full child texts]
    _named_column_evidence: str = ""  # direct column→schema hints extracted from user answers
    scalar_hint: bool = False          # True when output type is detected as scalar (one-way: False→True only)
    data_retriever: Any = None
    semantic_retriever: Any = None
    connectors: list = field(default_factory=list)
