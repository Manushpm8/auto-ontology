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
   - "search_for": the core item(s) the user wants to find, as noun phrases.
     Keep the words that describe a single item together in one phrase.
   - "terms": descriptive, non-numeric qualifiers such as colors, brands,
     materials, and adjectives (e.g. "red", "Panini", "waterproof").
   - "numeric_concepts": measurable/numeric attributes the request cares about,
     named as concepts NOT values (e.g. "price", "quantity", "weight", "rating").
     Do NOT put literal numbers here — only the concept name.

Rules:
- Preserve the exact casing of brand and product names.
- Only extract what the question actually says; do not invent constraints.
- Any bucket may be an empty list if nothing applies.

Example
Input: "I'm looking for a red box of Panini playing cards, and I care about the
price and how many are in the pack."
Output:
{{
  "search_for": ["playing cards box"],
  "terms": ["red", "Panini"],
  "numeric_concepts": ["price", "quantity"]
}}

Question: {question}
"""
