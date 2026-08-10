# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prompts for the entity-coverage question_extraction node."""

import os

# Maximum number of entity noun phrases to extract per question.
# Raise via ENTITY_EXTRACTION_MAX_ENTITIES env var for complex multi-metric queries.
_MAX_ENTITIES: int = int(os.environ.get("ENTITY_EXTRACTION_MAX_ENTITIES", "5"))


def create_question_extraction_prompt(question: str) -> str:
    """Single prompt: sanitize, extract entity noun phrases, and name the subject."""
    return f"""You rewrite conversational user requests into concise, SQL-ready questions, \
extract database entity noun phrases from the sanitized intent, AND name the question's \
main subject.

## Part 1 — sanitized_question

Rules:
- Remove personal background, narrative fluff, and filler.
- Preserve every factual constraint: numbers, product names, brands, categories, and \
qualifiers such as "similar", "natural ingredients", or "expensive is okay".
- Do NOT invent constraints that are not in the original text.
- If the input is already a direct question, return it unchanged.
- Output one concise question or search intent, not a paragraph.

Examples:

Input: We're planning a road trip next summer and my whole family loves hiking.
I need a tent that can fit 4 people, and lighter is better since we'll carry it.
sanitized_question: Find a 4-person tent, prioritizing lighter weight.

Input: How many shipments were delivered last month?
sanitized_question: How many shipments were delivered last month?

## Part 2 — required_entity_name

Populate "required_entity_name" with 1–{_MAX_ENTITIES} noun phrases that correspond to database \
tables, columns, or relationships. Extract from the sanitized intent.

Preserve the exact casing of terms as they appear in the question. Do not lowercase,
uppercase, or normalize them.

Guidelines for what to include in required_entity_name:
- Subject nouns and domain terms ("invoice", "customer", "shipment")
- Qualified entity phrases that combine a subject with its relevant action or attribute
  ("order shipment", "employee hire", "ticket resolution")
- Filter-item rule: when several words together describe a single item the user wants to
  filter or search for, keep them in one phrase. Do not split modifier, noun, and purpose
  of the same filter item into separate entries.
  Example: "waterproof hiking tent for family camping" → \
["waterproof hiking tent for family camping"],
  not ["waterproof hiking tent", "family camping"].
- Keep names and descriptive text that identify something: brand names, product names,
  vendor names, categories, and other named constants (e.g. "Salomon Speedcross").
- For interrogative words (who/what/which/whose), resolve to the implied entity type
  AND, if the question contains a qualifying descriptor, include it twice: once alone
  and once combined with the resolved type.
  Example: "who are the active assignees" → ["assignee", "active assignee"]

Guidelines for what to exclude from required_entity_name:
- Bare action verbs ("submitted", "approved", "closed", "assigned")
- Numeric values: counts, amounts, prices, years, and other number literals
- Date/time values when they are numeric or calendar literals
- Aggregation indicators ("count", "total", "average", "sum", "min", "max")
  when standing alone
- Status and filter adjectives when standing alone ("open", "active", "high-priority")
- Bare schema-generic words with no domain meaning on their own: "id", "name",
  "type", "code", "key", "value", "description", "label", "title", "flag",
  "uuid", "pk", "fk". Do not emit these as standalone entities.

Date rule: When a question references a time-qualified event, collapse subject + action
+ granularity into one compact phrase ending with "date".
  Example: "invoices closed in Q2" → ["invoice", "invoice closure quarter date"]
  Example: "orders placed last year" → ["order", "order placement date"]

Examples:
  Q: "How many shipments were delivered last month?"
  → required_entity_name: ["shipment", "shipment delivery month date"]

  Q: "Find a waterproof hiking tent for family camping."
  → required_entity_name: ["waterproof hiking tent for family camping"]

## Part 3 — subject

Populate "subject" with one short noun phrase naming what the question is about — the
single thing being asked for. Derive it from the sanitized question and preserve casing
the same way Part 2 does.

Exclude from the subject: filters and qualifiers, aggregation words ("count", "total",
"average"), date and time qualifiers, and number literals.

Examples:
  Q: "How many shipments were delivered last month?"
  → subject: "shipment"

  Q: "Find a waterproof hiking tent for family camping."
  → subject: "tent"

  Q: "Which vendors had the highest invoice totals in Q2?"
  → subject: "vendor"

## Input

{question}
"""
