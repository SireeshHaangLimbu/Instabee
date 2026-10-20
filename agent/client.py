"""Anthropic client from .env; supports user-scoped keys through ANTHROPIC_WORKSPACE_ID."""

from __future__ import annotations

import os

import anthropic
from dotenv import load_dotenv

from pipeline.common import ROOT


def make_client() -> anthropic.Anthropic:
    load_dotenv(ROOT / ".env")
    headers = {}
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    if workspace:
        headers["anthropic-workspace-id"] = workspace
    return anthropic.Anthropic(default_headers=headers or None)
