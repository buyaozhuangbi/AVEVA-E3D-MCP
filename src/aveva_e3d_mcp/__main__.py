from __future__ import annotations

import sys

from .config import Settings
from .tools import create_server


def main() -> int:
    try:
        settings = Settings.from_env()
        create_server(settings).serve(enforce_lifecycle=True)
    except Exception as exc:
        print(f"aveva-e3d-mcp: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
