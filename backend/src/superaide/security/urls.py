"""Checking an address the owner typed before the server connects to it (ADR 0007, section 9).

The server sends the owner's API key to this address, and it runs on the same machine as the
vault, so an address must not lead to the cloud metadata service or, unless it's a local model
server, to other machines on the private network.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

METADATA = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")}


class UnsafeURL(ValueError):
    """A plain-language reason the address can't be used."""


def resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError) as exc:
        raise UnsafeURL(f"Couldn't find {host}. Check the address.") from exc
    out = []
    for info in infos:
        addr, _, scope = str(info[4][0]).partition("%")
        if scope.startswith("lo"):
            continue  # a link-local alias on the loopback interface (macOS lists fe80::1%lo0 for localhost)
        out.append(ipaddress.ip_address(addr))
    if not out:
        raise UnsafeURL(f"Couldn't find {host}. Check the address.")
    return out


def hosted() -> bool:
    from superaide.config import get_settings

    return get_settings().deploy_mode == "hosted"


def check_base_url(url: str, *, allow_private: bool, hosted_mode: bool | None = None) -> str:
    """Return the address, cleaned, or raise UnsafeURL. `allow_private` is for local model servers
    on a self-hosted install; when hosted (many owners on our servers) nothing private is reachable."""
    if hosted_mode if hosted_mode is not None else hosted():
        allow_private = False
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise UnsafeURL("The address must start with https:// (or http:// for a server on this computer).")
    if parts.username or parts.password:
        raise UnsafeURL("Put the key in the key field, not in the address.")
    if parts.query or parts.fragment:
        raise UnsafeURL("The address can't have a ? or # part.")
    ips = resolve(parts.hostname)
    for ip in ips:
        if (
            ip in METADATA
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
            or (ip.is_reserved and not ip.is_loopback)
        ):
            raise UnsafeURL("That address isn't allowed.")
        if (ip.is_private or ip.is_loopback) and not allow_private:
            raise UnsafeURL(
                "That address is on a private network. Only a local model server on your own install may use one."
            )
    if parts.scheme == "http" and not all(ip.is_loopback or ip.is_private for ip in ips):
        raise UnsafeURL("Use https:// for a service on the internet, so your key isn't sent in the clear.")
    return url.rstrip("/")
