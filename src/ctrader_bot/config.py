"""Loads config/config.yaml (strategy/runtime settings) and .env (secrets)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Secrets:
    anthropic_api_key: str | None
    ctrader_account_id: str
    ctrader_login: str
    demo_mode: bool
    ctrader_mcp_url: str


def load_settings(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or (PROJECT_ROOT / "config" / "config.yaml")
    with open(path) as f:
        return yaml.safe_load(f)


def load_secrets(env_path: Path | None = None) -> Secrets:
    load_dotenv(env_path or (PROJECT_ROOT / ".env"))
    demo_mode_raw = os.environ.get("DEMO_MODE", "true").strip().lower()
    return Secrets(
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        ctrader_account_id=os.environ.get("CTRADER_ACCOUNT_ID", ""),
        ctrader_login=os.environ.get("CTRADER_LOGIN", ""),
        demo_mode=demo_mode_raw in ("1", "true", "yes"),
        ctrader_mcp_url=os.environ.get("CTRADER_MCP_URL", "http://127.0.0.1:9876/mcp/"),
    )
