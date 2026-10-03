import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from app.domain.errors import InvalidInput
from app.domain.hashing import canonical_json
from app.domain.statuses import Status

ALG = "HS256"
_HASH = re.compile(r"[0-9a-f]{64}")
_SIG = re.compile(r"[0-9a-f]{64}")
_KID = re.compile(r"[A-Za-z0-9_.-]{1,32}")
_ENVELOPE = {"alg", "kid", "payload", "signature"}
MIN_SECRET = 32


@dataclass(frozen=True)
class Subject:
    """What a receipt attests; the live request supplies the same shape at verify time."""

    request_id: UUID
    version: int
    content_hash: str
    approved_at: datetime
    approvers: tuple[UUID, ...] | Literal["auto"]

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, UUID):
            raise InvalidInput("request_id must be a UUID")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise InvalidInput("version must be a positive int")
        if not isinstance(self.content_hash, str) or not _HASH.fullmatch(self.content_hash):
            raise InvalidInput("content_hash must be 64 lowercase hex chars")
        if not isinstance(self.approved_at, datetime) or self.approved_at.tzinfo is None:
            raise InvalidInput("approved_at must be a timezone-aware datetime")
        object.__setattr__(self, "approved_at", self.approved_at.astimezone(UTC))
        if self.approvers != "auto":
            ids = tuple(self.approvers)
            if not ids or len(set(ids)) != len(ids) or not all(isinstance(a, UUID) for a in ids):
                raise InvalidInput("approvers must be 'auto' or distinct user ids")
            object.__setattr__(
                self, "approvers", tuple(sorted(ids, key=str))
            )  # order is not content

    def payload(self) -> dict[str, Any]:
        return {
            "request_id": str(self.request_id),
            "version": self.version,
            "content_hash": self.content_hash,
            "approved_at": self.approved_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "approvers": "auto" if self.approvers == "auto" else [str(a) for a in self.approvers],
        }


@dataclass(frozen=True)
class KeyRing:
    secrets: Mapping[str, bytes]
    newest: str

    @classmethod
    def parse(cls, raw: str) -> "KeyRing":
        """`kid:secret,kid:secret`; the last one signs, all verify. Short or duplicate keys fail closed."""
        secrets: dict[str, bytes] = {}
        kid = ""
        for part in raw.split(","):
            kid, _, secret = part.partition(":")
            if (
                not _KID.fullmatch(kid)
                or len(secret) < MIN_SECRET
                or secret != secret.strip()
                or kid in secrets
            ):
                raise InvalidInput(
                    f"bad receipt key entry (kid {kid!r}): need a unique kid and 32+ char secret"
                )
            secrets[kid] = secret.encode()
        return cls(secrets, kid)


def _mac(secret: bytes, kid: str, payload: dict[str, Any]) -> str:
    # kid is inside the MAC: two kids sharing a secret still cannot be swapped on a signed receipt.
    body = canonical_json({"kid": kid, "payload": payload})
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def sign(subject: Subject, keys: KeyRing) -> dict[str, Any]:
    payload = subject.payload()
    kid = keys.newest
    return {
        "alg": ALG,
        "kid": kid,
        "payload": payload,
        "signature": _mac(keys.secrets[kid], kid, payload),
    }


def verify(receipt: Any, live: Subject, live_status: Status, keys: KeyRing) -> bool:
    """True only if the receipt is intact AND still describes the live approved request. Never raises."""
    if live_status is not Status.APPROVED:
        return False
    if not isinstance(receipt, dict) or set(receipt) != _ENVELOPE:
        return False
    kid, payload, signature = receipt["kid"], receipt["payload"], receipt["signature"]
    if receipt["alg"] != ALG or not isinstance(kid, str) or kid not in keys.secrets:
        return False
    if not isinstance(payload, dict) or type(payload.get("version")) is not int:
        return False  # 1.0 is value-equal to 1 but not the signed bytes
    if not isinstance(signature, str) or not _SIG.fullmatch(signature):
        return False  # also keeps a lone surrogate from reaching encode()
    try:
        expected = _mac(keys.secrets[kid], kid, payload)
    except InvalidInput:  # NaN, huge ints, non-JSON values
        return False
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        return False
    return payload == live.payload()
