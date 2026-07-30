# SPDX-License-Identifier: Apache-2.0
"""Shared pytest configuration.

The suite is hermetic by contract: every adapter test feeds recorded fixture
payloads through a mocked ``httpx.Client``. This module enforces that contract
instead of trusting it — any test that opens a real socket fails loudly rather
than making CI depend on a live job board being up (and on the shape of
whatever it happens to return today).

Escape hatch, for a test that genuinely needs the network::

    @pytest.mark.allow_network
    def test_live_thing() -> None:
        ...

Such tests are also deselected by ``-m "not allow_network"``, which is how CI
runs the suite.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection


class NetworkAccessInTestError(RuntimeError):
    """Raised when a test tries to open a real network connection."""


def _is_local(address: Any) -> bool:
    """Allow loopback and unix sockets — local servers and IPC are not the network."""
    if isinstance(address, (str, bytes)):  # AF_UNIX
        return True
    if isinstance(address, tuple) and address:
        host = address[0]
        return host in ("127.0.0.1", "::1", "localhost", "", None)
    return False


def _blocked(address: Any) -> NetworkAccessInTestError:
    return NetworkAccessInTestError(
        f"A test attempted a real network connection to {address!r}. "
        "Adapter tests must mock httpx (see tests/test_remoteok.py for the pattern). "
        "If the call is deliberate, mark the test with @pytest.mark.allow_network."
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "allow_network: test may open real network connections (deselected in CI).",
    )


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("allow_network"):
        return

    def guard_connect(self: socket.socket, address: Any) -> Any:
        if _is_local(address):
            return _REAL_CONNECT(self, address)
        raise _blocked(address)

    def guard_connect_ex(self: socket.socket, address: Any) -> Any:
        if _is_local(address):
            return _REAL_CONNECT_EX(self, address)
        raise _blocked(address)

    def guard_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        if _is_local(address):
            return _REAL_CREATE_CONNECTION(address, *args, **kwargs)
        raise _blocked(address)

    monkeypatch.setattr(socket.socket, "connect", guard_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guard_connect_ex)
    monkeypatch.setattr(socket, "create_connection", guard_create_connection)
