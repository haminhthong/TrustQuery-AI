"""Small helpers shared by command-line entry points."""

from __future__ import annotations

import sys


def configure_utf8_output() -> None:
    """Use UTF-8 when the active Python streams support reconfiguration."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
