"""Stub of the LangGraph text-to-SQL entry-points used by chat router."""

from __future__ import annotations

from typing import Any, Dict, Iterator


class _StubLangGraphApp:
    """Mimics the ``app`` exported by NeMo-Retriever's LangGraph pipeline."""

    def stream(
        self, _state: Dict[str, Any], config: Dict[str, Any] | None = None
    ) -> Iterator[Dict[str, Any]]:
        yield {
            "stub": {
                "path_state": {
                    "final_response": (
                        "nemo_retriever stub build: chat pipeline is not wired up. "
                        "Rebuild the backend image with the real nemo_retriever "
                        "source to get real LLM responses."
                    )
                }
            }
        }

    def invoke(
        self, _state: Dict[str, Any], config: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        return next(self.stream(_state, config))


class _StubLLMClient:
    """Placeholder for the LLM client object exported by the real package."""

    def __repr__(self) -> str:
        return "<nemo_retriever stub LLM client>"


app = _StubLangGraphApp()
llm_client = _StubLLMClient()
