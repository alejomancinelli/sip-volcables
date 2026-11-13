"""
Las interfaces de red del equipo: nombre, dirección IPv4 y si tienen enlace.

Lo usan las pantallas que eligen por dónde escucha un servidor —Modbus TCP, los streams de
video— para ofrecer las interfaces que el equipo tiene en vez de pedir que alguien tipee
un nombre o una dirección: los nombres no se cross-portean ('eth0' en la Jetson,
'Ethernet' en Windows) y una IP mal tipeada da un servidor que no levanta.

Maquinaria genérica: no conoce la app ni su config. Nunca propaga una excepción: si psutil
no puede leer las interfaces devuelve una lista vacía, y quien la muestra cae a lo que diga
el config.
"""

from __future__ import annotations

import socket
from typing import NamedTuple

import psutil

_LOOPBACK_PREFIX = "127."


class NetInterface(NamedTuple):
    name: str
    ipv4: str          # la primera IPv4 de la interfaz; '' si no tiene
    is_up: bool        # el sistema la reporta con enlace


def list_interfaces() -> list[NetInterface]:
    """
    Las interfaces del equipo sin la de loopback, ordenadas por nombre. Una interfaz sin
    IPv4 aparece igual, con `ipv4` vacío: un cable desenchufado no la hace desaparecer.
    """
    try:
        addresses = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
    except (OSError, RuntimeError):
        return []
    interfaces = []
    for name in sorted(addresses):
        ipv4 = next((addr.address for addr in addresses[name]
                     if addr.family == socket.AF_INET), "")
        if ipv4.startswith(_LOOPBACK_PREFIX):
            continue
        stat = stats.get(name)
        interfaces.append(NetInterface(name, ipv4, bool(stat and stat.isup)))
    return interfaces
