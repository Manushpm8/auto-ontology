# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prompts for the entity-coverage question_extraction node."""

from __future__ import annotations

import os

# Maximum number of entity noun phrases to extract per question during clarification.
# Raise via CLARIFICATION_MAX_ENTITIES env var for complex multi-metric queries.
# Falls back to SQL_GEN_MAX_ENTITIES (then 5) when unset.
_MAX_ENTITIES: int = int(
    os.environ.get(
        "CLARIFICATION_MAX_ENTITIES",
        os.environ.get("SQL_GEN_MAX_ENTITIES", "5"),
    )
)


def format_glossary_section(glossary: list[dict[str, str]] | None) -> str:
    """Render the user-curated Glossary, or "" when there is nothing to inject.

    Entries are a flat list rather than ``rules_to_text``'s ``## name`` headings,
    which would collide with surrounding ``##`` prompt sections.
    """
    entries = [
        f"- {name}: {(entry.get('description') or '').strip()}"
        for entry in glossary or []
        if (name := (entry.get("name") or "").strip())
    ]
    if not entries:
        return ""
    definitions = "\n".join(entries)
    return f"""## Glossary

Definitions the user's organization has registered. Use them to resolve \
abbreviations, shortcuts, and internal jargon in the question.

{definitions}

"""


def format_evidence_section(evidence: str | None) -> str:
    """Render narrowly scoped evidence guidance for question sanitization."""
    if not evidence:
        return ""
    return f"""## Evidence for sanitization

Use this evidence only to disambiguate the domain meaning of the input while producing
`sanitized_question`.

Rules:
- Add at most one concise disambiguating qualifier to the sanitized question, and only
  when it materially improves entity retrieval.
- Do not append or summarize the evidence.
- Do not copy background, explanations, formulas, SQL, values, unrelated terms, or
  inventories of tables or columns into the sanitized question.
- Evidence must not add a second interpretation or broaden the user's request.
- Part 2 must derive entities only from the completed sanitized question, never directly
  from this evidence section.

{evidence}

"""


_SANITIZE_AND_ENTITIES = f"""## Part 1 — sanitized_question

Rules:
- Remove personal background, narrative fluff, filler, politeness, and generic request
  framing such as "please", "can you", "show me", "find", or
  "get semantic objects related to". Keep only the underlying domain intent.
- Preserve every factual constraint: numbers, product names, brands, categories, and \
qualifiers such as "similar", "natural ingredients", or "expensive is okay".
- Do NOT invent constraints that are not in the original text.
- Output one concise question or search intent, not a paragraph.

Examples:

Input: We're planning a road trip next summer and my whole family loves hiking.
I need a tent that can fit 4 people, and lighter is better since we'll carry it.
sanitized_question: Find a 4-person tent, prioritizing lighter weight.

Input: How many shipments were delivered last month?
sanitized_question: How many shipments were delivered last month?

## Part 2 — required_entity_name

Populate "required_entity_name" with 1–{_MAX_ENTITIES} noun phrases that correspond to database \
tables, columns, or relationships. Extract only from the completed sanitized intent. \
Do not extract entities directly from Glossary or Evidence sections.

Preserve the exact casing of terms as they appear in the completed sanitized question.
Do not lowercase, uppercase, or normalize them.

Glossary rule: when a word or phrase in the question matches a Glossary entry — an
abbreviation, a shortcut, or internal jargon — resolve it in place using that entry's
definition instead of emitting the raw shortcut. Resolving rewrites an existing entry;
it never adds an extra one, so the number of entries stays what it would have been
without the Glossary. Leave a phrase untouched when no Glossary entry applies.
  Example (Glossary: "MRR" = "monthly recurring revenue"):
  "show MRR by region" → ["monthly recurring revenue", "region"], not ["MRR", "region"]

Guidelines for what to include in required_entity_name:
- Subject nouns and domain terms ("invoice", "customer", "shipment")
- Qualified entity phrases that combine a subject with its relevant action or attribute
  ("order shipment", "employee hire", "ticket resolution")
- Aggregation-qualified metric rule: when "count", "total", "average", "sum", "min",
  or "max" is attached to a domain noun as the name of a requested metric or column,
  keep it as one entity phrase. This does not
  apply when the aggregation is only how the user asks a question, such as
  "How many shipments..."; in that case, extract the subject entity "shipment".
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
  when standing alone; preserve them when the aggregation-qualified metric rule applies
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
"""

_SUBJECT_AND_ACRONYMS = """## Part 3 — subject

Populate "subject" with one short noun phrase naming what the question is about — the
single thing being asked for. Derive it from the sanitized question, resolving any
Glossary entry that applies, and preserve casing the same way Part 2 does.

Exclude from the subject: filters and qualifiers, aggregation words ("count", "total",
"average"), date and time qualifiers, and number literals.

Examples:
  Q: "How many shipments were delivered last month?"
  → subject: "shipment"

  Q: "Find a waterproof hiking tent for family camping."
  → subject: "tent"

  Q: "Which vendors had the highest invoice totals in Q2?"
  → subject: "vendor"

## Part 4 — used_glossary_names

Populate "used_glossary_names" with the names of only the Glossary entries you actually used
to interpret, sanitize, resolve entities in, or determine the subject of this question.
Copy each name exactly as written in the Glossary. Do not infer entries by lexical
matching alone: include an entry only when its definition is semantically relevant.
Return an empty list when no Glossary definition applies.

Example (Glossary contains "MRR: monthly recurring revenue"):
  Q: "Show MRR by region."
  → used_glossary_names: ["MRR"]
"""


_DECOMPOSITION = """You split a user's request into the smallest ordered sequence of \
single-step questions that answers it.

A "single step" is one question answerable by one SQL query. Most requests are \
already a single step — say so rather than inventing structure.

## When to split

Split only when a later part needs a value an earlier part computes:
- A later part consumes a value an earlier part produces ("schools above this \
average", "the districts whose total exceeds that figure").
- It states a condition and then asks a question that only makes sense once the \
condition is settled ("Is it true that X? If so, by how much?").

Needing several *values* is not the same as needing several *steps*. Split on \
dependency, never on how many things the request mentions.

Do NOT split when:
- The request asks for several things about the same rows — "the amount and the \
status", "the score and the county", "the names along with the score". Those are \
columns of one answer, not steps. Only the last sub-question's answer is returned, \
so splitting them silently discards every value but the last.
- The request is one question with several filters, joins, or qualifiers. Filters \
are not steps.
- Splitting would only restate the same question in smaller words.
- A part is not answerable from the database on its own.

## Rules for each sub-question

- Write it as a complete, self-contained question. Do not use "it", "that", "this \
value", or "the above" to point at an earlier step — name the thing.
- Keep every literal, filter, and qualifier the original attached to that part. \
Never invent a constraint the user did not state.
- Order them so that anything a later step needs has already been computed. Earlier \
answers are supplied to later steps as authoritative evidence, so a later step may \
refer to an earlier result *by name* ("the lowest average salary found earlier").
- The LAST sub-question must be the one whose answer is the answer to the whole \
request. Everything before it exists to make it answerable.
- Emit at most {max_sub_questions} sub-questions. If the request needs more, it is \
too broad to decompose — return the original question unchanged as a single step.

## Output

Return a one-element list when the request is already a single step. That entry is \
what gets answered, so restate the request as one self-contained question:

- Preserve every filter, literal, qualifier, and requested output exactly. Never add \
a constraint, never drop one, never change what is being asked for.
- Make an implicit scope explicit when the request itself already determines it — \
above all, which population an "average", "total", or "highest" is computed over. \
"Teams with less than average passing" inside a question about 2014 normal-dribbling \
teams means the average *among those teams*; say so.
- Resolve pronouns and repair grammar or typos that obscure the meaning.
- Do not otherwise reword. If nothing above applies, return the request verbatim.

Examples:

Input: Name movie titles released in year 1945. Sort the listing by the descending \
order of movie popularity.
sub_questions: ["Name movie titles released in year 1945. Sort the listing by the \
descending order of movie popularity."]

(The second sentence is an instruction about how to present the answer, not a second \
thing to compute.)

Input: State the most popular movie? When was it released and who is the director \
for the movie?
sub_questions: ["State the most popular movie? When was it released and who is the \
director for the movie?"]

(Three outputs — title, release year, director — all describing the same movie. One \
query ordered by popularity returns all three. Splitting would return only the \
director.)

Input: How many movies were added to the list with the most number of movies? \
Indicate whether the user was a paying subscriber or not when he created the list.
sub_questions: ["How many movies were added to the list with the most number of \
movies? Indicate whether the user was a paying subscriber or not when he created the \
list."]

(A count *and* a flag, both about the same list. The superlative is a filter on which \
row to return, not an earlier step.)

Input: For the teams with normal build-up play dribbling class in 2014, list the \
names of the teams with less than average chance creation passing.
sub_questions: ["What are the names of the teams with normal build-up play dribbling \
class in 2014 whose chance creation passing is below the average chance creation \
passing among teams with normal build-up play dribbling class in 2014?"]

(One step, but the average is over the teams the question already named, not over \
every team. Nothing was added — the scope was already there, just unsaid.)

Input: List all the authors who wrote fewer pages than the average.
sub_questions: [
  "What is the average number of pages across all books?",
  "Which authors wrote books with fewer pages than the average number of pages \
across all books?"
]

Input: What is the total processed time of all solutions from the repository with \
the most forks?
sub_questions: [
  "Which repository has the most forks?",
  "What is the total processed time of all solutions belonging to the repository \
with the most forks?"
]

(Also a superlative, but the request wants one value, and the second step needs the \
first one's result to find it. That is a step, not a column.)
"""


def create_question_decomposition_prompt(
    question: str,
    glossary: list[dict[str, str]] | None = None,
    *,
    evidence: str | None = None,
    max_sub_questions: int = 5,
) -> str:
    """Prompt the model to split *question* into ordered single-step questions.

    Glossary and evidence are injected for the same reason extraction gets them:
    a request whose multi-step shape is only visible once an abbreviation or a
    domain formula is resolved would otherwise look like a single step.
    """
    glossary_section = format_glossary_section(glossary)
    evidence_section = (
        f"""## Evidence

Domain context for reading the request. Use it to recognize that a term implies a
computed intermediate value. Do not turn the evidence itself into sub-questions, and
do not add steps it mentions that the user did not ask for.

{evidence}

"""
        if evidence
        else ""
    )
    body = _DECOMPOSITION.format(max_sub_questions=max_sub_questions)
    return f"""{body}
{glossary_section}{evidence_section}## Input

{question}
"""


def create_question_extraction_prompt(
    question: str,
    glossary: list[dict[str, str]] | None = None,
    *,
    evidence: str | None = None,
    include_subject: bool = True,
) -> str:
    """Sanitize and extract entity noun phrases; optionally also name subject/acronyms.

    Glossary is always injected so the model can resolve abbreviations. Evidence,
    when provided, is restricted to refining the sanitized question before entity
    extraction, including when ``include_subject`` is False.
    """
    glossary_section = format_glossary_section(glossary)
    evidence_section = format_evidence_section(evidence)
    if include_subject:
        intro = (
            "You rewrite conversational user requests into concise, SQL-ready "
            "questions, extract database entity noun phrases from the sanitized "
            "intent, AND name the question's main subject."
        )
        trailing = _SUBJECT_AND_ACRONYMS
    else:
        intro = (
            "You rewrite conversational user requests into concise, SQL-ready "
            "questions and extract database entity noun phrases from the "
            "sanitized intent. Do not produce a subject field or list of used "
            "acronyms."
        )
        trailing = ""

    return f"""{intro}

{_SANITIZE_AND_ENTITIES}
{trailing}
{glossary_section}{evidence_section}## Input

{question}
"""
