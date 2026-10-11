"""Guards the "no network in tests" rule enforced by pytest-socket in pyproject.toml."""

from __future__ import annotations

import socket

import pytest
from pytest_socket import SocketBlockedError

# pytest-socket warns as well as raising; the attempt is expected here.
pytestmark = pytest.mark.filterwarnings("ignore:A test tried to use socket:UserWarning")


def test_network_access_is_blocked() -> None:
    with pytest.raises(SocketBlockedError):
        socket.create_connection(("example.com", 80), timeout=1)


def test_inet_socket_creation_is_blocked() -> None:
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_unix_sockets_are_allowed() -> None:
    # --allow-unix-socket keeps local IPC (e.g. asyncio event loops) usable.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM):
        pass
