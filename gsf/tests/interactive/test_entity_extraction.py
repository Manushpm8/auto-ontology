"""
Live test to compare reasoning vs non-reasoning model for entity extraction.

Run with:
    uv run pytest gsf/tests/interactive/test_entity_extraction.py -s -v

To test a custom prompt, set ENTITY_EXTRACTION_PROMPT_OVERRIDE to a string
containing {question} — it will replace the default prompt:
    ENTITY_EXTRACTION_PROMPT_OVERRIDE="Extract entities from: {question}" \
        uv run pytest gsf/tests/interactive/test_entity_extraction.py -s -v
"""
import os
import pytest
from langchain_core.messages import SystemMessage

from gsf.retrieval.text_to_sql.agents.entities_extraction import EntitiesExtractionModel
from gsf.retrieval.text_to_sql.prompts import create_entity_extraction_prompt
from gsf.utils.llm_invoke import invoke_with_structured_output, get_llm_client, get_non_reasoning_llm_client

QUESTIONS = [
    "Classify signals by their score level, and for each group, show the classification, "
    "signal count, average BFR measure, and the standard deviation of the anomaly metric.",
    # alien_8 turn-2 merged question — does "narrowband profiles" survive as an entity?
    "Could you scan our database for potential signals matching narrowband profiles? "
    "I need the signal identifiers, central frequency, drift rate, Bandwidth-Frequency Ratio (BFR), "
    "and the classification of NTM categories based on signal stability. "
    "Use the formula for BFR as BFR = BwHz/(CenterFreqMhz * 10^6), where narrow ratios (<0.001) "
    "often indicate technological signals. "
    "Classify NTM categories considering the spectral characteristics — specifically the "
    "Bandwidth-Frequency Ratio and frequency drift stability — without filtering by modulation type. "
    "The NTM classification tiers are: 'Strong NTM' (BFR < 0.0001 AND FreqDriftHzs < 0.1), "
    "'Moderate NTM' (BFR < 0.0005 AND FreqDriftHzs < 0.5), and 'Not NTM' (all other signals).",
]


def _build_prompt(question: str) -> str:
    override = os.environ.get("ENTITY_EXTRACTION_PROMPT_OVERRIDE")
    if override:
        return override.format(question=question)
    return create_entity_extraction_prompt(question)


def _extract(llm, question: str) -> list[str]:
    msgs = [SystemMessage(content=_build_prompt(question))]
    result = invoke_with_structured_output(llm, msgs, EntitiesExtractionModel)
    return result.required_entity_name


@pytest.mark.parametrize("question", QUESTIONS, ids=[f"q{i}" for i in range(len(QUESTIONS))])
def test_entity_extraction_comparison(question):
    reasoning = get_llm_client()
    non_reasoning = get_non_reasoning_llm_client()

    r_entities = _extract(reasoning, question)
    nr_entities = _extract(non_reasoning, question)

    print(f"\nQuestion: {question}")
    print(f"  Reasoning     : {r_entities}")
    print(f"  Non-reasoning : {nr_entities}")

    assert isinstance(r_entities, list)
    assert isinstance(nr_entities, list)
