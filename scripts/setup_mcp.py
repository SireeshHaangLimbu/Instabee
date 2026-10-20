"""Write .mcp.json so Claude Code (in this folder) can use the locker tools.

Run once: .venv/Scripts/python scripts/setup_mcp.py
Then open a new Claude Code session in this folder and approve the "instabee-lockers" server.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.claude_code import ensure_mcp_config  # noqa: E402

if __name__ == "__main__":
    print(f"wrote {ensure_mcp_config(overwrite=True)}")
