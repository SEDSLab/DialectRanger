"""All retained tests are offline; real sockets (including model APIs) are forbidden."""
import socket
import pytest


@pytest.fixture(autouse=True)
def forbid_external_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('Real network access is forbidden in the offline test suite')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
