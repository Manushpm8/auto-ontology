"""Chat streaming endpoint — wraps the LangGraph text-to-SQL pipeline."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Generator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage

from nemo_retriever.tabular_data.retrieval.text_to_sql.main import app as langgraph_app
from nemo_retriever.tabular_data.retrieval.text_to_sql.main import llm_client
from nemo_retriever.tabular_data.retrieval.text_to_sql.prompts import (
    main_system_prompt_template,
)
from nemo_retriever.tabular_data.retrieval.text_to_sql.state import AgentState

from server.chat.models import NODE_LABELS, ChatRequest

logger = logging.getLogger(__name__)

router = APIRouter()


def _sse(data: str) -> str:
    return f"data: {data}\n\n"


def _build_state(request: ChatRequest) -> AgentState:
    acronyms_text = f"Acronyms:\n{request.acronyms}\n\n" if request.acronyms else ""
    custom_prompts_text = (
        f"{request.custom_prompts}\n\n" if request.custom_prompts else ""
    )

    main_system_prompt = main_system_prompt_template.format(
        date=datetime.now(),
        acronyms=acronyms_text,
        custom_prompts=custom_prompts_text,
        dialect=request.dialect,
    )

    messages = [
        SystemMessage(content=main_system_prompt),
        HumanMessage(content=request.question),
    ]

    return {
        "llm": llm_client,
        "initial_question": request.question,
        "dialect": request.dialect,
        "connector": None,
        "messages": messages,
        "decision": "",
        "path_state": {},
    }


def _extract_answer(final_state: dict) -> dict:
    """Pull the user-facing answer out of the final graph state."""
    path_state = final_state.get("path_state", {})
    final_response = path_state.get("final_response")

    if final_response is None:
        messages_out = final_state.get("messages", [])
        if messages_out:
            if isinstance(messages_out, dict):
                final_response = messages_out
            elif isinstance(messages_out[-1], dict):
                final_response = messages_out[-1]
            else:
                final_response = str(messages_out[-1])
        else:
            final_response = ""

    if isinstance(final_response, dict):
        return final_response
    return {"response": str(final_response)}


def _stream_graph(request: ChatRequest) -> Generator[str, None, None]:
    t0 = time.perf_counter()
    state = _build_state(request)
    final_state = dict(state)

    try:
        for step in langgraph_app.stream(state, config={"recursion_limit": 45}):
            for node_name, node_output in step.items():
                label = NODE_LABELS.get(node_name, node_name)
                yield _sse(
                    json.dumps({"type": "step", "node": node_name, "label": label})
                )

                if node_output:
                    if "path_state" in node_output:
                        if "path_state" not in final_state:
                            final_state["path_state"] = {}
                        final_state["path_state"].update(node_output["path_state"])
                    for key, value in node_output.items():
                        if key != "path_state":
                            final_state[key] = value

        answer = _extract_answer(final_state)
        elapsed = time.perf_counter() - t0
        logger.info("Chat completed in %.2fs", elapsed)

        yield _sse(json.dumps({"type": "result", "answer": answer}))

    except Exception:
        logger.exception("Error during chat stream")
        yield _sse(
            json.dumps({"type": "error", "message": "An internal error occurred."})
        )

    yield _sse("[DONE]")


@router.post("/chat/completions")
def chat_completions(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _stream_graph(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
