"""The network is off in tests (pytest-socket, configured in pyproject.toml)."""

import socket

import httpx
import pytest
from pytest_socket import SocketBlockedError

# pytest-socket warns on every blocked attempt; here the attempt is the point.
pytestmark = pytest.mark.filterwarnings("ignore:A test tried to use socket")


def test_a_test_cannot_open_an_internet_socket():
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_an_http_client_cannot_reach_a_service():
    # 192.0.2.1 is TEST-NET-1: no DNS lookup, and nothing would answer anyway.
    with pytest.raises(SocketBlockedError):
        httpx.get("http://192.0.2.1/", timeout=1)
