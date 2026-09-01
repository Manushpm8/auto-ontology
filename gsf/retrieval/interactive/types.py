from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class InteractivePhase(str, Enum):
    PHASE1_CLARIFY = "phase1_clarify"
    PHASE2_CLARIFY = "phase2_clarify"


class TurnType(str, Enum):
    INITIAL = "initial"  # first message for a phase (clarify + submit)
    DEBUG = "debug"  # prior submission was rejected; retry with feedback
    FOLLOW_UP = "follow_up"  # prior submission completed; a new question follows


@dataclass
class AskUserAction:
    question: str


@dataclass
class SubmitSQLAction:
    sql: str
