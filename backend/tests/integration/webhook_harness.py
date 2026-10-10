"""Local receivers for webhook delivery tests: real sockets, reached through post_pinned's pinned backend."""

import asyncio
import functools
import uuid
from dataclasses import dataclass, field

import httpcore2
from httpcore2._backends.auto import AutoBackend
from sqlalchemy import text

from app.db.session import tenant_session
from app.services import outbox, webhooks
from app.services.webhook_security import post_pinned

PUBLIC = "93.184.215.14"


async def public_dns(host: str, port: int) -> list[str]:
    return [PUBLIC]


async def answered_204(url, body, headers) -> int:
    """A send that never touches the network: every receiver says 204."""
    return 204


class Routed(httpcore2.AsyncNetworkBackend):
    """post_pinned dials the checked public IP; this carries it to the local receiver named by the URL's port."""

    def __init__(self, ports: dict[int, int]) -> None:
        self.ports, self.inner = ports, AutoBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        local = self.ports[port]
        return await self.inner.connect_tcp(
            "127.0.0.1", local, timeout, local_address, socket_options
        )

    async def sleep(self, seconds: float) -> None:
        await self.inner.sleep(seconds)


@dataclass
class Seen:
    headers: dict[str, str]
    body: bytes
    at: float


@dataclass
class Receiver:
    """Answers each request with `statuses[i]` (the last repeats), or misbehaves: `hang` never answers,
    `trickle` sends one byte a second, `huge` streams an endless body. Records every request."""

    behave: str = "ok"
    statuses: tuple[int, ...] = (204,)
    reply: bytes = b""  # the response body (which must never reach the delivery log)
    seen: list[Seen] = field(default_factory=list)
    sent: int = 0  # body bytes a `huge` receiver got out before the client hung up
    port: int = 0

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        loop = asyncio.get_running_loop()
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode()
            lines = [ln for ln in head.split("\r\n")[1:] if ln]
            headers = {k.lower(): v for k, v in (ln.split(": ", 1) for ln in lines)}
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            self.seen.append(Seen(headers, body, loop.time()))
            gone = asyncio.ensure_future(reader.read())  # completes when the client hangs up
            if self.behave == "hang":
                await gone
            elif self.behave == "trickle":
                writer.write(b"HTTP/1.1 200 OK\r\n")
                while not gone.done():
                    writer.write(b"X")
                    await asyncio.sleep(1)
            elif self.behave == "huge":
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 10000000000\r\n\r\n")
                chunk = b"x" * 65536
                while not gone.done() and self.sent < 10**10:
                    writer.write(chunk)
                    await writer.drain()
                    self.sent += len(chunk)
            else:
                status = self.statuses[min(len(self.seen) - 1, len(self.statuses) - 1)]
                head = f"HTTP/1.1 {status} X\r\nContent-Length: {len(self.reply)}\r\n\r\n"
                writer.write(head.encode() + self.reply)
                await writer.drain()
            gone.cancel()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass  # the client hung up: what the misbehaving modes wait for
        finally:
            writer.close()

    async def start(self) -> asyncio.Server:
        server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = server.sockets[0].getsockname()[1]
        return server


def sender(receivers: dict[int, Receiver], **kw):
    """post_pinned with fake public DNS and connects routed to the local receivers (keyed by URL port)."""
    ports = {p: r.port for p, r in receivers.items()}
    return functools.partial(
        post_pinned, resolver=public_dns, backend=Routed(ports), allow_http=True, **kw
    )


async def subscribe(app_engine, org, port: int, events=("decision.created",), admin=None) -> dict:
    async with tenant_session(app_engine, org) as s:
        row, secret = await webhooks.create_subscription(
            s, org, admin or uuid.uuid4(), f"http://hooks.example.com:{port}/h", list(events)
        )
    return row | {"secret": secret}


async def emit(app_engine, org, type="decision.created", payload=None) -> uuid.UUID:
    """An outbox event as the engine writes it; returns its id."""
    async with tenant_session(app_engine, org) as s:
        seq = await outbox.emit(s, org, type, payload or {"id": str(uuid.uuid4())})
        q = text("select id from outbox_events where organization_id = :o and seq = :s")
        return (await s.execute(q, {"o": org, "s": seq})).scalar_one()
