"""FM20 / I17: a webhook URL can never make the server connect to a non-public address."""

import asyncio

import httpcore2
import pytest
from httpcore2._backends.auto import AutoBackend

from app.config import settings
from app.domain.errors import WebhookUrlRefused
from app.services import webhook_security as ws

pytestmark = pytest.mark.anyio

PUBLIC = "93.184.215.14"
PRIVATE = "private address range"
INTERNAL = "internal hostnames are not allowed"
OK_204 = [b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n"]


class resolver:  # lowercase: reads as a function at the call sites
    """Answers in order, one list per call (the last repeats); records every lookup."""

    def __init__(self, *answers: list[str]) -> None:
        self.queue, self.calls = list(answers), []

    async def __call__(self, host: str, port: int) -> list[str]:
        self.calls.append((host, port))
        return self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]


class Recording(httpcore2.AsyncNetworkBackend):
    """Records where a connect really goes; delegates to `inner` (optionally to a fixed local address)."""

    def __init__(self, inner, to: tuple[str, int] | None = None) -> None:
        self.inner, self.to, self.connects = inner, to, []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connects.append((host, port))
        host, port = self.to or (host, port)
        return await self.inner.connect_tcp(host, port, timeout, local_address, socket_options)

    async def sleep(self, seconds: float) -> None:
        await self.inner.sleep(seconds)


BLOCKED = [
    # loopback in every spelling libc accepts
    ("https://127.0.0.1/h", PRIVATE),
    ("https://127.1/h", PRIVATE),
    ("https://0x7f.1/h", PRIVATE),
    ("https://0x7f000001/h", PRIVATE),
    ("https://2130706433/h", PRIVATE),
    ("https://0177.0.0.1/h", PRIVATE),
    ("https://127.0.0.1./h", PRIVATE),
    ("https://１２７.０.０.１/h", PRIVATE),  # fullwidth digits fold to 127.0.0.1 under UTS 46
    ("https://10.1.2.3/h", PRIVATE),
    ("https://172.16.0.1/h", PRIVATE),
    ("https://172.31.255.255/h", PRIVATE),
    ("https://192.168.1.1/h", PRIVATE),
    ("https://169.254.169.254/latest/meta-data", PRIVATE),
    ("https://100.64.0.1/h", PRIVATE),
    ("https://0.0.0.0/h", PRIVATE),
    ("https://0/h", PRIVATE),
    ("https://192.0.0.8/h", PRIVATE),
    ("https://198.18.0.1/h", PRIVATE),
    (
        "https://224.0.0.1/h",
        PRIVATE,
    ),  # multicast is is_global in Python: the explicit table catches it
    ("https://240.0.0.1/h", PRIVATE),
    ("https://255.255.255.255/h", PRIVATE),
    ("https://[::1]/h", PRIVATE),
    ("https://[::]/h", PRIVATE),
    ("https://[fc00::1]/h", PRIVATE),
    ("https://[fd12:3456::1]/h", PRIVATE),
    ("https://[fe80::1]/h", PRIVATE),
    ("https://[ff02::1]/h", PRIVATE),
    ("https://[::ffff:127.0.0.1]/h", PRIVATE),
    ("https://[::ffff:7f00:1]/h", PRIVATE),
    ("https://[::127.0.0.1]/h", PRIVATE),  # IPv4-compatible (deprecated), is_global in Python
    ("https://[64:ff9b::7f00:1]/h", PRIVATE),  # NAT64 of 127.0.0.1
    ("https://[64:ff9b::a9fe:a9fe]/h", PRIVATE),  # NAT64 of the metadata address
    ("https://[2002:7f00:1::]/h", PRIVATE),  # 6to4 of 127.0.0.1
    ("https://[2002:a00:1::]/h", PRIVATE),  # 6to4 of 10.0.0.1
    ("https://[2001:0:4136:e378:8000:63bf:80ff:fffe]/h", PRIVATE),  # Teredo, client 127.0.0.1
    ("https://localhost/h", INTERNAL),
    ("https://LOCALHOST./h", INTERNAL),
    ("https://api.localhost/h", INTERNAL),
    ("https://metadata.google.internal/computeMetadata/v1", INTERNAL),
    ("https://postgres.railway.internal/h", INTERNAL),
    ("https://printer.local/h", INTERNAL),
    ("https://router.home.arpa/h", INTERNAL),
    ("https://redis/h", INTERNAL),  # a single label is a compose or cluster service name
    ("https://example.com:22/h", "this port is not allowed"),
    ("https://example.com:25/h", "this port is not allowed"),
    ("https://example.com:0/h", "this port is not allowed"),
    ("https://example.com:99999/h", "the URL is malformed"),
    ("https://exa mple.com/h", "the URL is malformed"),
    ("https://example.com/\nh", "the URL is malformed"),  # urlsplit would strip it silently
    ("https://example.com/\ud800", "the URL is malformed"),  # a lone surrogate would 500 in asyncpg
    ("ftp://example.com/h", "https is required"),
    ("file:///etc/passwd", "https is required"),
    ("https:///h", "the host is missing"),
    ("https://" + "a" * 64 + ".com/h", "the host is not a valid name"),
    ("https://example.com/" + "a" * 2048, "the URL is too long"),
]


@pytest.mark.parametrize(("url", "rule"), BLOCKED, ids=[u[:60] for u, _ in BLOCKED])
async def test_blocked_table(url, rule):
    # Every name resolves public here: only the literal or name rule itself can refuse.
    resolve = resolver([PUBLIC])
    with pytest.raises(WebhookUrlRefused) as e:
        await ws.validate_url(url, resolver=resolve, allow_http=False)
    assert e.value.details == {"rule": rule}
    assert str(e.value) == f"webhook URL refused: {rule}"


@pytest.mark.parametrize(
    "answers",
    [["127.0.0.1"], ["10.0.0.5"], ["::1"], ["::ffff:169.254.169.254"], [PUBLIC, "10.0.0.5"],
     ["10.0.0.5", PUBLIC], ["fe80::1%eth0"]],
)  # fmt: skip
async def test_any_private_answer_rejects_the_name(answers):
    # catches: checking only the first DNS answer while the connect may use another
    with pytest.raises(WebhookUrlRefused) as e:
        await ws.validate_url("https://hooks.example.com/h", resolver=resolver(answers))
    assert e.value.details == {"rule": PRIVATE}


async def test_public_urls_pass_and_are_normalised():
    resolve = resolver([PUBLIC, "2606:2800:21f:cb07:6820:80da:af6b:8b2c"])
    assert (
        await ws.validate_url("HTTPS://Bücher.Example:8443/a?b=1#frag", resolver=resolve)
        == "https://xn--bcher-kva.example:8443/a?b=1"
    )
    assert await ws.validate_url("https://93.184.215.14", resolver=resolve) == (
        "https://93.184.215.14/"
    )
    assert resolve.calls == [("xn--bcher-kva.example", 8443)]  # an IP literal is never looked up


async def test_unresolvable_host_is_refused():
    async def fails(host, port):
        raise OSError("nxdomain")

    for r in (fails, resolver([])):
        with pytest.raises(WebhookUrlRefused) as e:
            await ws.validate_url("https://nope.example.com/h", resolver=r)
        assert e.value.details == {"rule": "the host does not resolve"}


async def test_dns_rebinding_resolves_once_and_connects_to_the_pinned_ip():
    # Public on the first lookup, private on any later one: a second lookup at connect would hit 127.0.0.1.
    resolve = resolver([PUBLIC], ["127.0.0.1"])
    backend = Recording(httpcore2.AsyncMockBackend(OK_204))
    status = await ws.post_pinned(
        "https://hooks.example.com/h", b"{}", {}, resolver=resolve, backend=backend
    )
    assert status == 204
    assert resolve.calls == [("hooks.example.com", 443)]  # resolved exactly once
    assert backend.connects == [(PUBLIC, 443)]  # connected to the checked answer, not the name


async def test_dns_that_later_flips_to_private_is_refused_at_connect():
    # Saved while public; at delivery the same name answers private: nothing is connected.
    url = await ws.validate_url("https://hooks.example.com/h", resolver=resolver([PUBLIC]))
    backend = Recording(httpcore2.AsyncMockBackend(OK_204))
    for answers in (["10.0.0.7"], [PUBLIC, "169.254.169.254"]):
        with pytest.raises(WebhookUrlRefused) as e:
            await ws.post_pinned(url, b"{}", {}, resolver=resolver(answers), backend=backend)
        assert e.value.details == {"rule": PRIVATE}
    assert backend.connects == []


async def test_delivery_rechecks_the_stored_url_itself():
    # catches: a row edited past the API (or an old rule) going straight to the connect
    backend = Recording(httpcore2.AsyncMockBackend(OK_204))
    with pytest.raises(WebhookUrlRefused):
        await ws.post_pinned(
            "https://127.1/h", b"{}", {}, resolver=resolver([PUBLIC]), backend=backend
        )
    assert backend.connects == []


async def test_redirect_to_private_address_is_not_followed():
    seen: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        seen.append(await reader.readuntil(b"\r\n\r\n"))
        port = writer.get_extra_info("sockname")[1]
        writer.write(
            f"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:{port}/internal\r\n"
            "Content-Length: 0\r\n\r\n".encode()
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    # The name pins to a public answer; the test backend carries that connect to the local server.
    backend = Recording(AutoBackend(), to=("127.0.0.1", port))
    async with server:
        with pytest.raises(WebhookUrlRefused) as e:
            await ws.post_pinned(
                "http://hooks.example.com:8080/hook", b"{}", {}, allow_http=True,
                resolver=resolver([PUBLIC]), backend=backend,
            )  # fmt: skip
    assert e.value.details == {"rule": "redirects are not followed"}
    assert len(seen) == 1 and seen[0].startswith(b"POST /hook ")  # never a request to /internal
    assert b"host: hooks.example.com:8080" in seen[0].lower()  # the original Host, not the IP
    assert backend.connects == [(PUBLIC, 8080)]


async def test_http_refused_outside_dev_and_url_with_credentials_refused(monkeypatch):
    resolve = resolver([PUBLIC])
    monkeypatch.setattr(settings, "webhooks_allow_http", False)  # the default
    with pytest.raises(WebhookUrlRefused) as e:
        await ws.validate_url("http://hooks.example.com/h", resolver=resolve)
    assert e.value.details == {"rule": "https is required"}
    monkeypatch.setattr(settings, "webhooks_allow_http", True)  # dev only
    assert await ws.validate_url("http://hooks.example.com/h", resolver=resolve) == (
        "http://hooks.example.com/h"
    )
    for url in (
        "https://user:pass@hooks.example.com/h",
        "https://user@hooks.example.com/h",
        "https://:pass@hooks.example.com/h",
        "https://hooks.example.com@127.0.0.1/h",  # the classic "looks like example.com" smuggle
        "http://hooks.example.com%40evil.example/h",
    ):
        with pytest.raises(WebhookUrlRefused) as e:
            await ws.validate_url(url, resolver=resolve)
        assert e.value.details["rule"] in {
            "credentials in the URL are not allowed",
            "the host is not a valid name",
        }, url
    assert type(settings).model_fields["webhooks_allow_http"].default is False


def test_https_pins_sni_and_certificate_to_the_hostname():
    # The pinned transport changes only where the socket goes; TLS still checks the URL's name.
    t = ws.PinnedTransport(PUBLIC)
    assert t._pool._ssl_context.check_hostname is True  # type: ignore[attr-defined]


async def test_a_trickling_receiver_is_cut_at_the_total_deadline():
    # Per-read timeouts never fire if each byte arrives in time: the whole call needs one deadline.
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        try:
            writer.write(b"HTTP/1.1 200 OK\r\n")
            for _ in range(100):
                writer.write(b"X-Slow: 1\r\n")
                await writer.drain()
                await asyncio.sleep(0.05)
        except ConnectionError:
            pass  # the client hung up at its deadline: what this test wants
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    backend = Recording(AutoBackend(), to=("127.0.0.1", server.sockets[0].getsockname()[1]))
    loop = asyncio.get_running_loop()
    async with server:
        start = loop.time()
        with pytest.raises(TimeoutError):
            await ws.post_pinned(
                "http://hooks.example.com:8080/h", b"{}", {}, allow_http=True,
                resolver=resolver([PUBLIC]), backend=backend, timeout=0.3,
            )  # fmt: skip
        assert loop.time() - start < 1.5
