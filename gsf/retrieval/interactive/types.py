from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class InteractivePhase(str, Enum):
    PHASE1_CLARIFY = "phase1_clarify"
    PHASE1_SUBMIT  = "phase1_submit"
    PHASE1_DEBUG   = "phase1_debug"
    PHASE2_CLARIFY = "phase2_clarify"
    PHASE2_SUBMIT  = "phase2_submit"
    PHASE2_DEBUG   = "phase2_debug"
    DONE           = "done"


class TurnType(str, Enum):
    INITIAL   = "initial"    # Phase 1 or Phase 2 first message (clarify + submit)
    DEBUG     = "debug"      # "Your SQL is not executable/correct"
    FOLLOW_UP = "follow_up"  # "Phase 1 is complete. Here is a follow-up"


@dataclass
class AskUserAction:
    question: str


@dataclass
class SubmitSQLAction:
    sql: str
