import hashlib
import json
import math
from typing import Any, NoReturn

from app.domain.errors import InvalidInput
from app.domain.statuses import Risk


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:  # parsers disagree on which duplicate wins; refuse to pick
            raise InvalidInput(f"duplicate key {key!r}")
        out[key] = value
    return out


def _no_constant(name: str) -> NoReturn:
    raise InvalidInput(f"{name} is not valid JSON")


def loads_strict(raw: str | bytes) -> Any:
    """Parse JSON, rejecting duplicate keys, NaN and Infinity."""
    try:
        return json.loads(raw, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except InvalidInput:
        raise
    except (
        ValueError,
        RecursionError,
    ) as e:  # bad syntax or bytes, 4300+ digit ints, absurd nesting
        raise InvalidInput(f"invalid JSON: {type(e).__name__}") from None


def _normalise(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidInput("NaN and Infinity cannot be hashed")
        return int(value) if value.is_integer() else value  # 1.0 and 1 are the same amount
    if isinstance(value, list | tuple):
        return [_normalise(v) for v in value]
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise InvalidInput("object keys must be strings")
        return {k: _normalise(v) for k, v in value.items()}
    raise InvalidInput(f"{type(value).__name__} is not JSON")


def canonical_json(value: Any) -> bytes:
    """Sorted keys, no whitespace, UTF-8; the exact bytes that get hashed and signed."""
    try:
        text = json.dumps(
            _normalise(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return text.encode("utf-8")
    except InvalidInput:
        raise
    except (ValueError, RecursionError) as e:  # lone surrogates, huge ints, absurd nesting
        raise InvalidInput(f"cannot canonicalise: {type(e).__name__}") from None


def content_hash(title: str, action_type: str, payload: Any, declared_risk: Risk) -> str:
    """SHA-256 over title, action type, payload and the DECLARED risk (a floor change must not void receipts)."""
    if not isinstance(title, str) or not isinstance(action_type, str):
        raise InvalidInput("title and action_type must be strings")
    doc = {
        "title": title,
        "action_type": action_type,
        "payload": payload,
        "risk_level": Risk.of(declared_risk).name.lower(),
    }
    return hashlib.sha256(canonical_json(doc)).hexdigest()
