"""AES-256-GCM box for secrets the server must read back (webhook signing secrets, D18).

Keys are `kid:base64(32 bytes),...`: the last seals, every listed key opens, so a key rotates by appending."""

import base64
import binascii
import os
import re
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.domain.errors import InvalidInput

_KID = re.compile(r"[A-Za-z0-9_-]{1,32}")
# 96-bit random nonce per seal; GCM's bound (~2^32 seals per key) is far off at this volume.
NONCE = 12


class SealError(RuntimeError):
    """A stored ciphertext that does not open: a key or data fault on our side, so a 500, never a 422."""


@dataclass(frozen=True)
class SecretBox:
    keys: dict[str, bytes] = field(repr=False)  # a traceback must not print key bytes
    current: str

    @classmethod
    def parse(cls, spec: str) -> "SecretBox":
        keys: dict[str, bytes] = {}
        for item in filter(None, (p.strip() for p in spec.split(","))):
            kid, _, b64 = item.partition(":")
            try:
                key = base64.b64decode(b64, validate=True)
            except (binascii.Error, ValueError):
                key = b""
            if not _KID.fullmatch(kid) or len(key) != 32 or kid in keys:
                raise InvalidInput("each key is a distinct kid:base64 of 32 bytes")
            keys[kid] = key
        if not keys:
            raise InvalidInput("at least one key is needed")
        return cls(keys, list(keys)[-1])

    def seal(self, plaintext: str, aad: bytes) -> str:
        """`kid.base64url(nonce || ciphertext+tag)`; `aad` binds it to its row (a copied value will not open)."""
        nonce = os.urandom(NONCE)
        ct = AESGCM(self.keys[self.current]).encrypt(nonce, plaintext.encode(), aad)
        return f"{self.current}.{base64.urlsafe_b64encode(nonce + ct).decode()}"

    def open(self, sealed: str, aad: bytes) -> str:
        kid, _, b64 = sealed.partition(".")
        try:
            raw = base64.urlsafe_b64decode(b64)
            return AESGCM(self.keys[kid]).decrypt(raw[:NONCE], raw[NONCE:], aad).decode()
        except (KeyError, InvalidTag, binascii.Error, ValueError) as e:
            raise SealError("the sealed secret does not open") from e
