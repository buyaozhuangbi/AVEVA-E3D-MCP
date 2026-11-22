"""Retain a configured UTF-8 log tail without loading its historical lines."""
from __future__ import annotations

from collections import deque
from pathlib import Path


_READ_CHUNK_SIZE = 64 * 1024
_LINE_ENDINGS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")


def read_log_tail(path: Path, tail: int) -> list[str]:
    """Match ``path.read_text(encoding='utf-8', errors='replace').splitlines()[-tail:]``.

    Text mode retains the old universal-newline and replacement-decoding
    behavior. Fixed-size reads and a rolling deque avoid retaining historical
    lines. The file is still scanned in full; retained lines and an unfinished
    line are not truncated. Fragments of a long line are joined only once.
    """
    recent: deque[str] = deque(maxlen=tail)
    fragments: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        while chunk := stream.read(_READ_CHUNK_SIZE):
            for piece in chunk.splitlines(keepends=True):
                if piece[-1] not in _LINE_ENDINGS:
                    fragments.append(piece)
                    continue
                # Universal newlines normally turn CR/CRLF into LF. Retain
                # splitlines' exact ending removal if either is encountered.
                line = piece[:-2] if piece.endswith("\r\n") else piece[:-1]
                if fragments:
                    fragments.append(line)
                    recent.append("".join(fragments))
                    fragments.clear()
                else:
                    recent.append(line)
    if fragments:
        recent.append("".join(fragments))
    return list(recent)
