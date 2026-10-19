# Adapted from Odysseus (https://github.com/odysseus-dev/odysseus), AGPL-3.0-or-later.
"""Outbound URL checks for URLs that come from a vendor response.

Provider base URLs come from config and are trusted. The only other outbound
URL the server ever fetches is an image `url` returned inside an image API
response; a compromised or malicious endpoint could point it at cloud
metadata or another internal address, so it is checked here first.

Loopback/LAN targets stay allowed by default (local diffusion servers are the
normal setup). Always rejected: non-HTTP(S) schemes, link-local (the
169.254.0.0/16 metadata range), multicast, reserved and unspecified addresses.
Set IMAGE_BLOCK_PRIVATE_IPS=true to also reject private and loopback targets.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Callable
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")

# RFC 6598 shared address space; CPython does not flag it as private.
_SHARED_ADDRESS_SPACE_V4 = ipaddress.ip_network("100.64.0.0/10")
# RFC 6052 NAT64 well-known prefix: judge the embedded IPv4 destination.
_NAT64_WELL_KNOWN_PREFIX_V6 = ipaddress.ip_network("64:ff9b::/96")


def block_private_default() -> bool:
    return os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").strip().lower() == "true"


def _nat64_embedded_ipv4(ip: ipaddress._BaseAddress) -> ipaddress.IPv4Address | None:
    if isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64_WELL_KNOWN_PREFIX_V6:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _default_resolver(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def _classify(ip: ipaddress._BaseAddress, *, block_private: bool) -> str | None:
    """Return a rejection reason for an IP, or None if it is allowed."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    else:
        translated = _nat64_embedded_ipv4(ip)
        if translated is not None:
            reason = _classify(translated, block_private=True)
            return f"NAT64 translated destination blocked: {reason}" if reason else None
    if ip.is_link_local:
        return f"link-local address blocked (SSRF metadata risk): {ip}"
    if ip.is_loopback:
        return f"private/shared/loopback address blocked: {ip}" if block_private else None
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return f"disallowed address: {ip}"
    if block_private and (
        ip.is_private
        or (isinstance(ip, ipaddress.IPv4Address) and ip in _SHARED_ADDRESS_SPACE_V4)
    ):
        return f"private/shared/loopback address blocked: {ip}"
    return None


def check_outbound_url(
    url: str,
    *,
    block_private: bool = False,
    resolver: Callable[[str], list[str]] | None = None,
) -> tuple[bool, str]:
    """Return `(ok, reason)`; `ok` is True only when the URL is safe to fetch."""
    if not isinstance(url, str) or not url.strip():
        return False, "URL is required"
    try:
        parsed = urlparse(url.strip())
    except Exception as exc:  # pragma: no cover - urlparse is very tolerant
        return False, f"unparseable URL: {exc}"
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return False, f"scheme must be http or https, got '{parsed.scheme or '(none)'}'"
    host = parsed.hostname
    if not host:
        return False, "URL has no host"

    resolve = resolver or _default_resolver
    try:
        raw_ips = resolve(host)
    except Exception as exc:
        return False, f"host does not resolve: {exc}"
    saw_ip = False
    for raw in raw_ips or []:
        if not isinstance(raw, str):
            continue
        try:
            ip = ipaddress.ip_address(raw.split("%")[0])
        except ValueError:
            continue
        saw_ip = True
        reason = _classify(ip, block_private=block_private)
        if reason:
            return False, reason
    if not saw_ip:
        return False, "host does not resolve to an IP"
    return True, "ok"
