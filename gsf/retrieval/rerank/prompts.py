# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Prompt templates for the rerank flow."""


def create_question_extraction_prompt(question: str) -> str:
    """Prompt to normalize a question and extract its search entities.

    Produces a concise ``normalized_question`` plus entities split into five
    buckets: the target item (``search_for`` / ``search_for_details``), an
    optional prior item used as context (``reference_entity`` /
    ``reference_entity_details``), and how they relate (``relation``).
    """
    return f"""You analyze a shopping/search request. Do TWO things:

1. normalized_question: Rewrite the request into a concise, search-ready question.
   Remove narrative fluff and filler, but preserve every factual constraint
   (item, brand, colors, qualifiers, numbers). If the input is already concise,
   return it unchanged.

2. entities: Extract the search entities into exactly five buckets:
   - "search_for": the SINGLE core item the user wants to find NOW — exactly ONE
     entry. Keep the words describing that one item together in a single phrase
     and use the SINGULAR form, never plural (e.g. "pen" not "pens").
   - "search_for_details": descriptive qualifiers of the item the user wants to
     find (everything about that target item that is not the core search_for
     phrase). Keep nouns and adjectives here (e.g. "natural ingredient", "x5").
     Do NOT repeat the core search_for item.
   - "reference_entity": the SINGLE existing item the user already owns, bought,
     or is referencing as context — exactly ONE entry when present. Use an empty
     list when the question has no prior-item context (a plain search with no
     "I bought X", "I have X", "for my X", etc.).
   - "reference_entity_details": brand, model, color, size, or other identifying
     qualifiers of the reference item that help locate it in the database. Do NOT
     repeat the core reference_entity phrase.
   - "relation": how search_for relates to reference_entity — exactly ONE short
     phrase when a reference exists (e.g. "similar to", "compatible with",
     "accessory for", "upgrade for", "replacement for"). Empty when there is no
     reference_entity.

Target vs reference vs relation:
- The TARGET is what the user wants to get now (search_for).
- The REFERENCE is an item they already have that provides context (reference_entity).
- The RELATION is the link between them (relation) — put comparative/relational
  words HERE, not in any other bucket.
- Example: "I bought a Forest Byke derailleur hanger 65 and need an adapter for it"
  -> search_for: ["adapter"], reference_entity: ["derailleur hanger 65"],
     relation: ["compatible with"].
- Example: "I'm looking for a red box to hold my Panini playing cards"
  -> search_for: ["box playing cards"], search_for_details: ["red"], no reference,
     no relation.

Noun-only rule for search_for, search_for_details, reference_entity, and
reference_entity_details (NOT relation):
- Keep ONLY nouns and the adjectives/proper-nouns that qualify them.
- DROP all verbs (e.g. "hold", "carry", "buy"), prepositions ("to", "of", "in",
  "on", "for", "with"), articles ("a", "the"), and other connective/filler words.
- Do NOT reword or add synonyms — just remove the non-noun words and keep the
  surviving nouns in their original order.
  Example: "box to hold playing cards" -> "box playing cards" (drop "to", "hold").

Rules:
- Preserve the exact casing of brand and product names.
- Only extract what the question actually says; do not invent constraints.
- A number that is part of a product/model name (a model number, size, or
  version embedded in the item name) MUST stay attached to that item's phrase.
  Never split such a number into its own bucket entry.
- Any bucket may be an empty list if nothing applies.
- NEVER store a number as a separate bucket entry.
- Relational/comparative words such as "similar", "same", "like", "compatible",
  "for", "with", "replacement", "upgrade" belong ONLY in "relation". Do NOT put
  them in search_for, search_for_details, reference_entity, or
  reference_entity_details.
- If reference_entity is empty, relation MUST be empty.

Example 1 — follow-up with a reference item
Input: "I just bought a Forest Byke Company Derailleur Hanger 65, please recommend
me something similar from x5."
Output:
{{
  "search_for": ["derailleur hanger"],
  "search_for_details": ["x5"],
  "reference_entity": ["Forest Byke Company Derailleur Hanger 65"],
  "reference_entity_details": [],
  "relation": ["similar to"]
}}

Example 2 — plain search, no reference
Input: "I'm looking for a red box to hold my Panini playing cards."
Output:
{{
  "search_for": ["box playing cards"],
  "search_for_details": ["red", "Panini"],
  "reference_entity": [],
  "reference_entity_details": [],
  "relation": []
}}

Example 3 — bought item with a need for an accessory
Input: "I bought a desktop computer yesterday but it is too slow, I need more RAM."
Output:
{{
  "search_for": ["RAM"],
  "search_for_details": [],
  "reference_entity": ["desktop computer"],
  "reference_entity_details": ["slow"],
  "relation": ["upgrade for"]
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

3. SEARCH-FOR DETAILS (OPTIONAL descriptive words — do NOT filter by them; use them to RANK: the more that match, the higher the row):
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
- SEARCH-FOR DETAILS are OPTIONAL and must NEVER filter or exclude rows: do not put them in the WHERE clause. A row that matches none of the details must still be returned. Instead, use them ONLY to rank: for each detail word build MAX(CASE WHEN LOWER(col) LIKE LOWER('%word%') THEN 1 ELSE 0 END), add all of these together, and ORDER BY that sum DESC so rows matching more details come first (more matches = more relevant). Still include the detail columns in the SELECT list.
- Include every NUMERIC CONCEPT column in the SELECT list; do not filter on them.
- Always SELECT the identifier and the name/title of each item (e.g. its id column and its name or title column) so every returned row can be identified.
- Always GROUP BY the item's identifier column ONLY (the id column alone). Never add any other column to the GROUP BY.
- Because you GROUP BY the id alone, every selected column from the item's OWN table may be selected directly (it is functionally dependent on that table's primary key). But any selected column that comes from a DIFFERENT (joined) table MUST be wrapped in an aggregate function (e.g. MIN(...), MAX(...)) — never select a raw column from a joined table, or the query will fail with a "must appear in the GROUP BY clause" error.
- To connect two tables, use ONLY the join conditions from JOIN PATHS above. Do not invent join keys.
- NEVER, under any circumstances, join another table to the main item on the item's identifier column. If a JOIN PATH's condition references the item's id on EITHER side (e.g. products.product_id = qa.product_id, where product_id is the item id), that join is forbidden — do NOT add it. This is an absolute rule with no exceptions.
- When such a join is forbidden, remove that other table from the query ENTIRELY: do not reference it anywhere — not in FROM/JOIN, not in the SELECT list, and not in any WHERE predicate. Never select a column such as qa.question from a table whose only link to the item is the item id.
- Never reference a table anywhere in the query (SELECT, WHERE, GROUP BY) unless it is actually joined into the FROM/JOIN clause.
- Build the WHERE clause by combining ONLY the term-filter group and the search-for group with AND. SEARCH-FOR DETAILS never appear in WHERE — they only drive the ORDER BY ranking described above. Produce valid {dialect_name} SQL. No DDL/DML — SELECT only.
- Always limit the query to at most 100 rows using the {dialect_name} dialect's row-limiting clause (e.g. LIMIT 100, or FETCH FIRST 100 ROWS ONLY / TOP 100 where required).
- Do NOT include any comments in the SQL (no -- line comments and no /* */ block comments).
"""


def create_sql_relaxation_prompt(
    dialect: str,
    sql: str,
    term_filters: str,
) -> str:
    """Prompt to strip the ``terms`` filters from a query that returned nothing.

    The previous query returned no rows, so we relax it by removing ONLY the
    WHERE predicates that came from the term filters, while keeping the core
    ``search_for`` match, the SELECT list, the joins, the GROUP BY, and the
    ORDER BY (search-for-details ranking) untouched.
    """
    dialect_name = dialect or "standard SQL"
    return f"""You are an expert data analyst. You are given a {dialect_name} SQL \
SELECT query that returned no rows. Relax it by REMOVING filters, then return \
the modified query.

Do NOT execute anything. Return ONLY the SQL query.

CURRENT SQL:
{sql}

REMOVE the WHERE predicates that filter by these TERM FILTERS:
{term_filters}

RULES:
- Remove ONLY the WHERE predicates that correspond to the TERM FILTERS listed
  above (including the parenthesized group built from them). Remove the whole
  predicate/group, not just part of it.
- Keep EVERYTHING else exactly as it is: the SELECT list, the SEARCH-FOR core
  item match, any numeric columns, the FROM/JOIN clauses, the GROUP BY, the
  ORDER BY (the search-for-details ranking), and the row-limiting clause
  (LIMIT / FETCH FIRST / TOP).
- After removing predicates, fix the boolean structure so the query stays valid:
  drop dangling AND/OR, remove an empty WHERE clause entirely, and keep balanced
  parentheses.
- Produce valid {dialect_name} SQL. No DDL/DML — SELECT only.
- Do NOT include any comments in the SQL (no -- line comments and no /* */ block comments).
"""
