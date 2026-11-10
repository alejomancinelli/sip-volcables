"""Tests de la lista de interfaces de red: qué se ofrece y qué se deja afuera.

psutil se reemplaza por un doble: la suite no depende de las interfaces de la máquina
que la corre.
"""

import socket
from types import SimpleNamespace

from tools import net_interfaces


def _addr(family, address: str):
    return SimpleNamespace(family=family, address=address)


def _fake_psutil(monkeypatch, addresses: dict, up: dict):
    monkeypatch.setattr(net_interfaces.psutil, "net_if_addrs", lambda: addresses)
    monkeypatch.setattr(net_interfaces.psutil, "net_if_stats",
                        lambda: {name: SimpleNamespace(isup=is_up) for name, is_up in up.items()})


class TestListInterfaces:
    def test_each_interface_comes_with_its_ipv4_and_link(self, monkeypatch):
        _fake_psutil(monkeypatch,
                     {"eth0": [_addr(socket.AF_INET, "192.168.0.10")]}, {"eth0": True})
        assert net_interfaces.list_interfaces() == [
            net_interfaces.NetInterface("eth0", "192.168.0.10", True)]

    def test_loopback_is_left_out(self, monkeypatch):
        _fake_psutil(monkeypatch, {
            "lo": [_addr(socket.AF_INET, "127.0.0.1")],
            "eth0": [_addr(socket.AF_INET, "10.0.0.2")],
        }, {"lo": True, "eth0": True})
        assert [i.name for i in net_interfaces.list_interfaces()] == ["eth0"]

    def test_an_interface_without_ipv4_is_kept_without_an_address(self, monkeypatch):
        """Un cable desenchufado no hace desaparecer la interfaz."""
        _fake_psutil(monkeypatch, {"eth1": [_addr(socket.AF_INET6, "fe80::1")]},
                     {"eth1": False})
        assert net_interfaces.list_interfaces() == [
            net_interfaces.NetInterface("eth1", "", False)]

    def test_they_come_sorted_by_name(self, monkeypatch):
        _fake_psutil(monkeypatch, {"wlan0": [], "eth0": []}, {})
        assert [i.name for i in net_interfaces.list_interfaces()] == ["eth0", "wlan0"]

    def test_a_psutil_failure_is_an_empty_list(self, monkeypatch):
        def _fail():
            raise OSError("sin permiso")
        monkeypatch.setattr(net_interfaces.psutil, "net_if_addrs", _fail)
        assert net_interfaces.list_interfaces() == []
