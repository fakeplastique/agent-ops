from collections.abc import Mapping
from typing import Protocol

Prop = str | int | bool | None


class Analytics(Protocol):
    """Product analytics sink (spec 5.5). Props are ids and enums only, never titles, payloads or emails;
    `event_id` in props is the dedup key a provider can use (delivery is at least once)."""

    def emit(self, name: str, props: Mapping[str, Prop]) -> None: ...
