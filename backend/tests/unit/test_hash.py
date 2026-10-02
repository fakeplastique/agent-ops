import hashlib
import json
import math

import pytest

from app.domain.errors import InvalidInput
from app.domain.hashing import content_hash, loads_strict
from app.domain.statuses import Risk


def h(payload, title="T", action="refund", risk=Risk.LOW):
    return content_hash(title, action, payload, risk)


def test_hash_stable_across_key_order_unicode_and_1_vs_1_0():
    assert h({"a": 1, "b": [1, 2]}) == h(
        {"b": [1, 2], "a": 1}
    )  # catches: key order changing the hash
    assert h({"n": 1}) == h(
        {"n": 1.0}
    )  # catches: the same amount hashing differently (receipt flaps)
    assert h({"n": -0.0}) == h({"n": 0})
    assert h({"s": "é"}) == h(loads_strict('{"s": "\\u00e9"}'))
    assert h({"s": "é"}) != h({"s": "é"})  # exact content is signed, no silent normalisation
    assert h({"n": 1.5}) != h({"n": 1})
    assert h({"ok": True}) != h({"ok": 1})  # bool is not the integer 1


def test_hash_uses_declared_risk_not_effective():
    expected = hashlib.sha256(
        json.dumps(
            {"action_type": "refund", "payload": {"a": 1}, "risk_level": "low", "title": "T"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    # Pins the exact bytes; catches: a floor change (effective risk) invalidating old receipts.
    assert h({"a": 1}) == expected
    assert h({"a": 1}, risk=Risk.HIGH) != expected
    assert h({"a": 1}, risk="low") == expected  # type: ignore[arg-type]


def test_every_signed_field_changes_the_hash():
    base = h({"a": 1})
    assert len({base, h({"a": 1}, title="U"), h({"a": 1}, action="x"), h({"a": 2})}) == 4


def test_duplicate_keys_and_nan_in_payload_rejected():
    with pytest.raises(InvalidInput):  # catches: parsers differing on which duplicate wins
        loads_strict('{"a": 1, "a": 2}')
    with pytest.raises(InvalidInput):
        loads_strict('{"a": {"b": 1, "b": 1}}')
    for bad in ('{"a": NaN}', '{"a": Infinity}', '{"a": -Infinity}', "{"):
        with pytest.raises(InvalidInput):
            loads_strict(bad)
    with pytest.raises(InvalidInput):
        h({"a": math.nan})
    with pytest.raises(InvalidInput):
        h({"a": {1, 2}})  # not JSON
    with pytest.raises(InvalidInput):
        h({"a": "\ud800"})  # lone surrogate cannot be encoded
