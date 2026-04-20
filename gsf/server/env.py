"""Environment bootstrap helpers for server integrations."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_server_dir = Path(__file__).resolve().parent
_gsf_dir = _server_dir.parent
_repo_root = _gsf_dir.parent


def load_server_env() -> None:
    """Load repo and app ``.env`` files into process environment."""
    load_dotenv(_repo_root / ".env", override=True)
    load_dotenv(_gsf_dir / ".env", override=True)
