"""Stub of nemo_retriever — imports succeed, runtime calls raise."""

from __future__ import annotations

__version__ = "0.0.0+stub"
__is_stub__ = True


def _stub(symbol: str):
    """Return a callable that raises a clear error when invoked."""

    def _raise(*_args, **_kwargs):
        raise NotImplementedError(
            f"nemo_retriever stub: '{symbol}' is not implemented in this build. "
            "Build the image with --build-arg NEMO_RETRIEVER_SRC=<real source> "
            "to use the real NeMo-Retriever package."
        )

    _raise.__name__ = symbol
    return _raise
