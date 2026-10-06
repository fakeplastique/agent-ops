from typing import Any

from app.domain.errors import InvalidInput


def check_text(name: str, value: Any, low: int, high: int) -> None:
    ok = isinstance(value, str) and low <= len(value) <= high and "\x00" not in value
    ok = ok and (low == 0 or bool(value.strip()))  # a required field is never blank
    if ok:
        try:
            value.encode("utf-8")  # a lone surrogate would reach asyncpg and fail as a 500
        except UnicodeEncodeError:
            ok = False
    if not ok:
        raise InvalidInput(f"{name} must be a string of {low} to {high} characters")
