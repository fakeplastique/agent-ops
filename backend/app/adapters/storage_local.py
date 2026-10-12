import asyncio
import base64
import hashlib
import hmac
import json
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from app.domain.attachments import KEY_RE
from app.ports.clock import Clock

URL_PREFIX = "/api/v1/files/"


@dataclass(frozen=True)
class SignedFile:
    path: Path
    filename: str
    content_type: str


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class LocalStorage:
    """Files on disk under `<root>/<org>/<request>/<attachment>`. Its signed URLs are served by
    `api/v1/files.py`, which calls `open_signed`: the token is an HMAC over key, expiry, name and type."""

    def __init__(self, root: Path, secret: str, clock: Clock) -> None:
        self.root, self.secret, self.clock = root, secret.encode(), clock

    def _path(self, key: str) -> Path:
        if not KEY_RE.fullmatch(key):  # the one place a key becomes a path
            raise ValueError("not a storage key")
        return self.root / key

    async def put(self, key: str, chunks: AsyncIterator[bytes], content_type: str) -> None:
        dest = self._path(key)
        tmp = self.root / ".tmp" / uuid.uuid4().hex  # same filesystem: the rename below is atomic
        await asyncio.to_thread(tmp.parent.mkdir, parents=True, exist_ok=True)
        f = await asyncio.to_thread(open, tmp, "wb")
        try:
            async for chunk in chunks:
                await asyncio.to_thread(f.write, chunk)
            await asyncio.to_thread(f.close)
            await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(os.replace, tmp, dest)
        except BaseException:
            f.close()
            tmp.unlink(missing_ok=True)
            raise

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, True)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).is_file)

    def signed_url(self, key: str, *, filename: str, content_type: str, ttl_seconds: int) -> str:
        self._path(key)  # refuses a malformed key before it is signed
        body = {"k": key, "e": int(self.clock.now().timestamp()) + ttl_seconds, "n": filename}
        payload = _b64(json.dumps(body | {"t": content_type}, separators=(",", ":")).encode())
        return f"{URL_PREFIX}{payload}.{_b64(self._sign(payload))}"

    def _sign(self, payload: str) -> bytes:
        return hmac.new(self.secret, payload.encode(), hashlib.sha256).digest()

    def open_signed(self, token: str) -> SignedFile | None:
        """The file a valid, unexpired token names; None for anything else (one answer, no oracle)."""
        try:
            payload, sig = token.split(".")
            if not hmac.compare_digest(self._sign(payload), _unb64(sig)):
                return None
            body = json.loads(_unb64(payload))
            if int(body["e"]) < self.clock.now().timestamp():
                return None
            path = self._path(str(body["k"]))
            if not path.is_file():
                return None
            return SignedFile(path, str(body["n"]), str(body["t"]))
        except (ValueError, KeyError, TypeError):
            return None
