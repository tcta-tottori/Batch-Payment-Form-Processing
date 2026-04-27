"""Convenience entrypoint for local development.

    python -m app.run            # listens on http://127.0.0.1:8000
    python -m app.run --port=80  # custom port
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="一括納付明細書チェックリストサーバー")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="開発用 --reload")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "app.backend.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
