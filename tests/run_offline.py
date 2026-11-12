"""Run the suite with live market access, external sockets and paid API calls blocked.

Usage: python tests/run_offline.py -q --basetemp test-output-offline
Injected fake clients/providers in the existing tests remain usable.
"""

import sys
import socket
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest


def forbidden(*args, **kwargs):
    raise AssertionError("Live network/paid API access is forbidden in the offline suite")


_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex
_create_connection = socket.create_connection


def loopback_only(function):
    def guarded(*args, **kwargs):
        address = args[1] if isinstance(args[0], socket.socket) else args[0]
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1", "localhost"}:
            return forbidden()
        return function(*args, **kwargs)
    return guarded


if __name__ == "__main__":
    # Match `python -m pytest`: older tests also import helpers as tests.test_*.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with ExitStack() as stack:
        # Windows asyncio uses loopback socket pairs even for in-process ASGI tests.
        for target, function in (
            ("socket.create_connection", _create_connection),
            ("socket.socket.connect", _connect), ("socket.socket.connect_ex", _connect_ex),
        ):
            stack.enter_context(patch(target, loopback_only(function)))
        for target in (
            "yfinance.Ticker.history", "openai.resources.responses.Responses.create",
            "openai.resources.chat.completions.Completions.create",
        ):
            stack.enter_context(patch(target, forbidden))
        raise SystemExit(pytest.main(["-p", "no:cacheprovider", *sys.argv[1:]]))
