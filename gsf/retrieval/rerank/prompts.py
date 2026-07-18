# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prompt templates for the rerank flow."""


def create_question_extraction_prompt(question: str) -> str:
    """Prompt to normalize a question and extract its search entities.

    Produces a concise ``normalized_question`` plus entities split into three
    buckets: ``search_for`` (the item being searched for), ``terms``
    (descriptive/qualifier words), and ``numeric_concepts`` (measurable
    attributes).
    """
    return f"""You analyze a shopping/search request. Do TWO things:

1. normalized_question: Rewrite the request into a concise, search-ready question.
   Remove narrative fluff and filler, but preserve every factual constraint
   (item, brand, colors, qualifiers, numbers). If the input is already concise,
   return it unchanged.

2. entities: Extract the search entities into exactly three buckets:
   - "search_for": the core item(s) the user wants to find. Keep the words that
     describe a single item together in one phrase.
   - "terms": descriptive, non-numeric qualifiers such as colors, brands, and
     materials (e.g. "red", "Panini", "waterproof").
   - "numeric_concepts": measurable/numeric attributes the request cares about,
     named as concepts NOT values (e.g. "price", "quantity", "weight", "rating").
     Do NOT put literal numbers here — only the concept name.

Noun-only rule for "search_for" and "terms":
- Keep ONLY nouns and the adjectives/proper-nouns that qualify them.
- DROP all verbs (e.g. "hold", "carry", "buy"), prepositions ("to", "of", "in",
  "on", "for", "with"), articles ("a", "the"), and other connective/filler words.
- Do NOT reword or add synonyms — just remove the non-noun words and keep the
  surviving nouns in their original order.
  Example: "box to hold playing cards" -> "box playing cards" (drop "to", "hold").

Rules:
- Preserve the exact casing of brand and product names.
- Only extract what the question actually says; do not invent constraints.
- Any bucket may be an empty list if nothing applies.

Example
Input: "I'm looking for a red box to hold my Panini playing cards, and I care
about the price and how many are in the pack."
Output:
{{
  "search_for": ["box playing cards"],
  "terms": ["red", "Panini"],
  "numeric_concepts": ["price", "quantity"]
}}

Question: {question}
"""


def create_sql_generation_prompt(
    dialect: str,
    question: str,
    tables_section: str,
    join_paths_section: str,
    term_filters: str,
    search_for_hints: str,
    numeric_hints: str,
) -> str:
    """Prompt to build (but not execute) a read-only SQL query for the request.

    The query is grounded in the resolved schema context: it filters ``terms``
    by their exact resolved values, fuzzy-matches ``search_for`` entities on
    their mapped columns, and returns ``numeric_concepts`` columns. Joins are
    restricted to the provided semantic join paths.
    """
    dialect_name = dialect or "standard SQL"
    return f"""You are an expert data analyst. Write a SINGLE read-only SQL \
SELECT query, in the {dialect_name} dialect, that answers the request below.

Do NOT execute anything. Return ONLY the SQL query.

REQUEST:
{question}

AVAILABLE TABLES (use ONLY these tables and columns):
{tables_section}

JOIN PATHS (the ONLY joins you may use to connect tables):
{join_paths_section}

FILTERS AND COLUMNS TO USE:

1. TERM FILTERS (authoritative — apply every one of these exactly):
{term_filters}

2. SEARCH-FOR (the item being searched — add a fuzzy match AND SELECT the column):
{search_for_hints}

3. NUMERIC CONCEPTS (measurable attributes — include these columns in SELECT, do NOT filter):
{numeric_hints}

RULES:
- Use ONLY the tables and columns listed above. Never invent tables, columns, or values.
- Qualify every column with its table (or schema.table) and quote identifiers as the {dialect_name} dialect requires.
- TERM FILTERS are authoritative: apply each as a WHERE predicate using the exact value provided (e.g. col = 'value'). Where a term has no exact value, match it with LOWER(col) LIKE LOWER('%term%').
- For each SEARCH-FOR entity, add a case-insensitive fuzzy predicate LOWER(col) LIKE LOWER('%entity%') and include its column in the SELECT list.
- Include every NUMERIC CONCEPT column in the SELECT list; do not filter on them.
- To connect two tables, use ONLY the join conditions from JOIN PATHS above. Do not invent join keys.
- Combine multiple predicates with AND. Produce valid {dialect_name} SQL. No DDL/DML — SELECT only.
"""
