# SPDX-License-Identifier: Apache-2.0
"""The hermeticity guard itself needs a test, or it can rot into a no-op."""

from __future__ import annotations

import socket

import pytest

from tests.conftest import NetworkAccessInTestError


def test_outbound_connection_is_blocked() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(NetworkAccessInTestError):
        sock.connect(("example.com", 80))
    sock.close()


def test_create_connection_is_blocked() -> None:
    with pytest.raises(NetworkAccessInTestError):
        socket.create_connection(("example.com", 80), timeout=0.01)


def test_loopback_is_still_allowed() -> None:
    """Guard must not break a test that spins up a local server."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect(server.getsockname())
    finally:
        client.close()
        server.close()
