# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""DAIL-style question masking for cross-database few-shot retrieval.

Masks entity names / literals so similarity is driven by question shape rather
than domain-specific tokens. Uses cheap rule-based rewrites always, and spaCy
NER + noun chunks when ``en_core_web_sm`` is available.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

_QUOTED = re.compile(
    r"('([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\"|`([^`\\]|\\.)*`)",
)
# Avoid splitting alphanumerics like ``K-12`` / ``v2.0``.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.-])[-+]?\d+(?:[.,]\d+)?(?![A-Za-z0-9_.-])")
_LIKE_LITERAL = re.compile(
    r"(?i)\bLIKE\s+('([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\")",
)
_BROKEN_BRACKETS = re.compile(r"\[([A-Z_]+)\]\]+")

_NER_LABEL_TO_TOKEN = {
    "PERSON": "[PERSON]",
    "ORG": "[ORG]",
    "GPE": "[GPE]",
    "LOC": "[LOC]",
    "FAC": "[FAC]",
    "PRODUCT": "[PRODUCT]",
    "EVENT": "[EVENT]",
    "WORK_OF_ART": "[WORK]",
    "NORP": "[NORP]",
    "DATE": "[DATE]",
    "TIME": "[TIME]",
    "MONEY": "[MONEY]",
    "PERCENT": "[PERCENT]",
    "QUANTITY": "[QUANTITY]",
    "CARDINAL": "[NUMBER]",
    "ORDINAL": "[ORDINAL]",
}


@lru_cache(maxsize=1)
def _nlp():
    """Load spaCy English model once; return ``None`` when unavailable."""
    try:
        import spacy
    except ImportError:
        logger.info("spaCy not installed; using rule-only question masking.")
        return None
    try:
        return spacy.load("en_core_web_sm")
    except OSError:
        logger.warning(
            "spaCy model en_core_web_sm not found; using rule-only masking. "
            "Install with: python -m spacy download en_core_web_sm"
        )
        return None


def _mask_literals(text: str) -> str:
    """Replace quoted strings, LIKE literals, and bare numbers."""
    masked = _LIKE_LITERAL.sub("LIKE [VALUE]", text)
    masked = _QUOTED.sub("[VALUE]", masked)
    masked = _NUMBER.sub("[NUMBER]", masked)
    return masked


def _mask_with_spacy(text: str) -> str:
    """Replace NER entities (and leftover proper noun chunks) with typed tokens."""
    nlp = _nlp()
    if nlp is None or not text.strip():
        return text

    doc = nlp(text)
    replacements: list[tuple[int, int, str]] = []

    for ent in doc.ents:
        token = _NER_LABEL_TO_TOKEN.get(ent.label_)
        if token:
            replacements.append((ent.start_char, ent.end_char, token))

    # Cover proper-noun spans that NER missed (e.g. product / school names).
    covered = {(s, e) for s, e, _ in replacements}
    for chunk in doc.noun_chunks:
        if not any(t.pos_ == "PROPN" for t in chunk):
            continue
        span = (chunk.start_char, chunk.end_char)
        if any(not (span[1] <= s or span[0] >= e) for s, e in covered):
            continue
        replacements.append((span[0], span[1], "[ENTITY]"))

    if not replacements:
        return text

    replacements.sort(key=lambda x: x[0], reverse=True)
    out = text
    for start, end, token in replacements:
        out = out[:start] + token + out[end:]
    return out


def mask_question(text: str) -> str:
    """Return a masked copy of *text* suitable for few-shot embedding / search."""
    if not (text or "").strip():
        return ""
    masked = _mask_with_spacy(_mask_literals(text)).strip()
    return _BROKEN_BRACKETS.sub(r"[\1]", masked)
