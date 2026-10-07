"""Runtime configuration from environment variables, with an optional local ``.env`` file.

A tiny ``.env`` reader is used instead of a dotenv dependency: fewer moving parts and no
surprises in sandboxed environments. Real environment variables always win over the file.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse KEY=VALUE lines (ignoring comments/blank lines). Values may be quoted."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


class Settings(BaseModel):
    """All tunables in one place so behaviour is explicit and testable."""

    # LLM planner (OpenAI-compatible). Optional: without a key the rule-based planner is used.
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    llm_model: str = "gpt-4.1"
    llm_timeout_seconds: float = 40.0

    # ClinicalTrials.gov Data API v2
    ctgov_base_url: str = "https://clinicaltrials.gov/api/v2"
    ctgov_timeout_seconds: float = 30.0
    ctgov_page_size: int = Field(default=1000, ge=1, le=1000)
    ctgov_max_retries: int = 3
    ctgov_cache_ttl_seconds: int = 600

    # Safety limits on how much data one request may pull and emit
    default_max_trials: int = 2000
    hard_max_trials: int = 5000
    default_citations_per_datum: int = 25
    hard_max_citations_per_datum: int = 200
    default_top_n: int = 20

    # Cosmetic
    app_name: str = "ClinicalTrials.gov Query-to-Visualization Agent"
    app_version: str = "0.1.0"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        merged = {**load_env_file(), **(env if env is not None else os.environ)}
        kwargs = {name: merged[name.upper()] for name in cls.model_fields if name.upper() in merged and merged[name.upper()] != ""}
        return cls(**kwargs)


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
