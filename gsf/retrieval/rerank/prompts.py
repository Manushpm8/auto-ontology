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

2. entities: Extract the search entities into exactly four buckets:
   - "search_for": the SINGLE core item the user wants to find — exactly ONE
     entry. Keep the words describing that one item together in a single phrase
     and use the SINGULAR form, never plural (e.g. "pen" not "pens").
   - "search_for_details": the remaining descriptive qualifiers of the item that
     are likely to appear in its free-text description (everything about the item
     that is not the core item and not a structured filter). Keep the nouns here
     (e.g. "natural ingredient"). Do NOT repeat the core item.
   - "terms": values that map to a structured, categorical filter such as a
     specific brand, color, or material the user wants to filter by (e.g. "red",
     "Panini"). Include a term ONLY if the question is actually asking to
     filter/constrain results by it. Do NOT put free-text descriptive attributes
     here — those belong in "search_for_details". If it is just background or
     narrative context, do NOT include it.
   - "numeric_concepts": measurable/numeric attributes the request cares about,
     named as concepts NOT values (e.g. "price", "quantity", "weight", "rating").
     Do NOT put literal numbers here — only the concept name.

Noun-only rule for "search_for", "search_for_details", and "terms":
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
  "search_for_details": [],
  "terms": ["red", "Panini"],
  "numeric_concepts": ["price", "quantity"]
}}

Example 2
Input: "yesterday I bought a desktop computer, but it is too slow machine."
Output:
{{
  "search_for": ["desktop computer"],
  "search_for_details": ["slow"],
  "terms": [],
  "numeric_concepts": []
}}

Example 3
Input: "I bought a grip enhancer from Grip Rx, but am looking for one with
more natural ingredients."
Output:
{{
  "search_for": ["grip enhancer"],
  "search_for_details": ["natural ingredient"],
  "terms": [],
  "numeric_concepts": []
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
    search_for_details_hints: str,
    numeric_hints: str,
) -> str:
    """Prompt to build (but not execute) a read-only SQL query for the request.

    The query is grounded in the resolved schema context: it filters ``terms``
    by their exact resolved values, matches the ``search_for`` core item (all
    words AND-ed) and the ``search_for_details`` qualifiers (OR-ed) on their
    mapped columns, and returns ``numeric_concepts`` columns. Joins are
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

2. SEARCH-FOR (the core item — match ALL of its words with AND, and SELECT the column):
{search_for_hints}

3. SEARCH-FOR DETAILS (extra descriptive words — match with OR between them, and SELECT the column):
{search_for_details_hints}

4. NUMERIC CONCEPTS (measurable attributes — include these columns in SELECT, do NOT filter):
{numeric_hints}

RULES:
- Use ONLY the tables and columns listed above. Never invent tables, columns, or values.
- Qualify every column with its table (or schema.table) and quote identifiers as the {dialect_name} dialect requires.
- TERM FILTERS are authoritative: apply each as a WHERE predicate using the exact value provided (e.g. col = 'value'). Where a term has no exact value, match it with LOWER(col) LIKE LOWER('%term%').
- Combine the TERM FILTER predicates with OR between them (not AND), and wrap that group in parentheses.
- Match SEARCH-FOR and SEARCH-FOR DETAILS with a case-insensitive CONTAINS, never equality: use ILIKE '%...%' where the dialect supports it, otherwise LOWER(col) LIKE LOWER('%...%').
- SEARCH-FOR is the core item: split it into words and require ALL of its words, each as its own contains predicate combined with AND (words may appear anywhere in the column, order does not matter). Include its column in the SELECT list.
- SEARCH-FOR DETAILS are extra descriptive words: split each detail into words and combine ALL of these contains predicates with OR between them, wrapped in parentheses. Include their columns in the SELECT list.
- Include every NUMERIC CONCEPT column in the SELECT list; do not filter on them.
- Always SELECT the identifier and the name/title of each item (e.g. its id column and its name or title column) so every returned row can be identified.
- To connect two tables, use ONLY the join conditions from JOIN PATHS above. Do not invent join keys.
- Combine the separate predicate groups (term-filter group, search-for group, search-for-details group) with AND. Produce valid {dialect_name} SQL. No DDL/DML — SELECT only.
- Do NOT include any comments in the SQL (no -- line comments and no /* */ block comments).
"""
