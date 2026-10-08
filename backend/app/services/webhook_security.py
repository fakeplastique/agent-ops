"""Webhook egress (D18): the SSRF guard for subscription URLs and the pinned-DNS POST the delivery worker uses.

The attacker controls the URL and its DNS, so a name is resolved once, every answer must be public, and the
socket goes to the checked address while Host, SNI and the certificate check keep the original name."""

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import secrets
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpcore2
import httpx2
import idna
from httpcore2._backends.auto import AutoBackend

from app.config import settings
from app.domain.errors import WebhookUrlRefused

type IP = ipaddress.IPv4Address | ipaddress.IPv6Address
type Resolver = Callable[[str, int], Awaitable[list[str]]]

MAX_URL = 2048
TIMEOUT = 5.0  # spec 9: every webhook call is bounded
PRIVATE = "private address range"
# Belt and braces over `is_global`, which calls multicast and IPv4-compatible addresses global.
_BLOCKED = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
        "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15",
        "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
        "::/96", "::ffff:0:0/96", "64:ff9b:1::/48", "100::/64", "2001::/23", "2001:db8::/32",
        "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8",
    )
)  # fmt: skip
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".localdomain", ".lan")
# Below 1024 only the web ports: no SMTP, SSH or Redis-style protocol smuggling through a hook.
_WEB_PORTS = {80, 443}


@dataclass(frozen=True)
class Target:
    url: str  # normalised: ASCII host, canonical IP literal, no fragment
    host: str
    port: int
    ip: IP | None  # set when the URL names an address instead of a host


def _embedded_v4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped:
        return ip.ipv4_mapped
    if ip.sixtofour:
        return ip.sixtofour
    if ip.teredo:
        return ip.teredo[1]
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def is_blocked(ip: IP) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        ip = ipaddress.IPv6Address(int(ip))  # drop a zone id
        if (v4 := _embedded_v4(ip)) is not None and is_blocked(v4):
            return True  # a v6 wrapper around a private v4 still lands on it
    return not ip.is_global or ip.is_multicast or any(ip in n for n in _BLOCKED)


def check_url(url: str, *, allow_http: bool | None = None) -> Target:
    """Everything decidable without DNS. Raises WebhookUrlRefused naming the rule."""
    allow_http = settings.webhooks_allow_http if allow_http is None else allow_http
    if len(url) > MAX_URL:
        raise WebhookUrlRefused("the URL is too long")
    # urlsplit silently drops tabs and newlines: refuse them (and any space or control) instead.
    if any(ord(c) <= 0x20 or ord(c) == 0x7F or 0xD800 <= ord(c) <= 0xDFFF for c in url):
        raise WebhookUrlRefused(
            "the URL is malformed"
        )  # a lone surrogate cannot be stored as UTF-8
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as e:
        raise WebhookUrlRefused("the URL is malformed") from e
    scheme = parts.scheme.lower()
    if scheme != "https" and not (scheme == "http" and allow_http):
        raise WebhookUrlRefused("https is required")
    if "@" in parts.netloc:
        raise WebhookUrlRefused("credentials in the URL are not allowed")
    raw = (parts.hostname or "").rstrip(".")
    if not raw:
        raise WebhookUrlRefused("the host is missing")
    port = port if port is not None else (443 if scheme == "https" else 80)
    if port < 1024 and port not in _WEB_PORTS:
        raise WebhookUrlRefused("this port is not allowed")
    ip: IP | None = None
    if parts.netloc.rsplit("@", 1)[-1].startswith("["):
        try:
            ip = ipaddress.IPv6Address(raw)
        except ValueError as e:
            raise WebhookUrlRefused("the host is not a valid name") from e
        host = f"[{ip.compressed}]"
    else:
        try:  # UTS 46 folds case and full-width forms, so "１２７.０.０.１" becomes 127.0.0.1 here
            host = idna.encode(raw, uts46=True).decode("ascii")
        except idna.IDNAError as e:
            raise WebhookUrlRefused("the host is not a valid name") from e
        try:  # what libc would accept: 127.1, 0x7f.1, 2130706433, 0177.0.0.1
            ip = ipaddress.IPv4Address(socket.inet_aton(host))
            host = str(ip)
        except OSError:
            if "." not in host or host == "localhost" or host.endswith(_INTERNAL_SUFFIXES):
                raise WebhookUrlRefused("internal hostnames are not allowed") from None
    if ip is not None and is_blocked(ip):
        raise WebhookUrlRefused(PRIVATE)
    default = port == (443 if scheme == "https" else 80)
    netloc = host if default else f"{host}:{port}"
    query = f"?{parts.query}" if parts.query else ""
    return Target(f"{scheme}://{netloc}{parts.path or '/'}{query}", host, port, ip)


async def system_resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def pin(target: Target, resolver: Resolver = system_resolve) -> IP:
    """The one address to connect to: resolved once, refused if any answer is non-public."""
    if target.ip is not None:
        return target.ip
    try:
        answers = await asyncio.wait_for(resolver(target.host, target.port), TIMEOUT)
        ips = [ipaddress.ip_address(a) for a in answers]
    except (OSError, ValueError, TimeoutError) as e:
        raise WebhookUrlRefused("the host does not resolve") from e
    if not ips:
        raise WebhookUrlRefused("the host does not resolve")
    if any(is_blocked(ip) for ip in ips):
        raise WebhookUrlRefused(PRIVATE)  # one bad answer taints the name: the OS may pick any
    return ips[0]


async def validate_url(
    url: str, *, resolver: Resolver = system_resolve, allow_http: bool | None = None
) -> str:
    """Save-time check (create and PATCH): the normalised URL, or WebhookUrlRefused."""
    target = check_url(url, allow_http=allow_http)
    await pin(target, resolver)
    return target.url


class _PinnedBackend(httpcore2.AsyncNetworkBackend):
    """Ignores the host httpcore asks for and dials the checked address; TLS above it still uses the name."""

    def __init__(self, ip: IP, inner: httpcore2.AsyncNetworkBackend) -> None:
        self._ip, self._inner = str(ip), inner

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return await self._inner.connect_tcp(self._ip, port, timeout, local_address, socket_options)

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class PinnedTransport(httpx2.AsyncHTTPTransport):
    """An httpx2 transport whose every connection goes to one address. No env proxy: it would re-resolve."""

    def __init__(self, ip: IP | str, backend: httpcore2.AsyncNetworkBackend | None = None) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore2.AsyncConnectionPool(
            ssl_context=httpx2.create_ssl_context(trust_env=False),
            max_connections=1,
            network_backend=_PinnedBackend(ipaddress.ip_address(ip), backend or AutoBackend()),
        )


async def post_pinned(
    url: str, body: bytes, headers: Mapping[str, str], *, resolver: Resolver = system_resolve,
    backend: httpcore2.AsyncNetworkBackend | None = None, allow_http: bool | None = None,
    timeout: float = TIMEOUT,
) -> int:  # fmt: skip
    """Delivery (S28): re-check the URL, pin DNS, POST once, return the status. The body is never read.

    A 3xx is refused, never followed: the Location is attacker-chosen and was never checked."""
    target = check_url(url, allow_http=allow_http)
    transport = PinnedTransport(await pin(target, resolver), backend)
    async with (
        httpx2.AsyncClient(
            transport=transport, follow_redirects=False, timeout=timeout, trust_env=False
        ) as client,  # trust_env off: an env proxy would be mounted over the pinned transport
        client.stream("POST", target.url, content=body, headers=dict(headers)) as response,
    ):
        status = response.status_code
    if 300 <= status < 400:
        raise WebhookUrlRefused("redirects are not followed")
    return status


# Signing: Standard Webhooks (D18), checked against the official vectors in tests/unit/test_webhook_signing.py.
SECRET_TAG = "whsec_"
TOLERANCE = (
    300  # seconds a receiver accepts around webhook-timestamp (the reference libraries' window)
)


def new_secret() -> str:
    return SECRET_TAG + base64.b64encode(secrets.token_bytes(32)).decode()


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    """`v1,` + base64 HMAC-SHA256 over `id.timestamp.body`, keyed by the base64 part of the whsec_ secret."""
    key = base64.b64decode(secret.removeprefix(SECRET_TAG))
    signed = f"{msg_id}.{timestamp}.".encode() + body
    return "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


def signed_headers(secret: str, msg_id: str, timestamp: int, body: bytes) -> dict[str, str]:
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": sign(secret, msg_id, timestamp, body),
        "content-type": "application/json",
    }


def verify(secret: str, msg_id: str, timestamp: int, body: bytes, header: str, *, now: int) -> bool:
    """What a receiver does: any listed v1 signature matches, and the timestamp is within the window."""
    if abs(now - timestamp) > TOLERANCE:
        return False
    want = sign(secret, msg_id, timestamp, body)
    return any(hmac.compare_digest(want, s) for s in header.split())
