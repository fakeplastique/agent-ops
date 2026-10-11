from typing import Protocol


class Notifier(Protocol):
    """A chat channel for approvals (Telegram). Calls raise on failure; callers log the type name only."""

    async def send(self, chat_id: int, text: str, buttons: list[tuple[str, str]]) -> int:
        """Post `text` (HTML) with inline (label, callback data) buttons; returns the message id."""
        ...

    async def edit(self, chat_id: int, message_id: int, text: str) -> None:
        """Replace a message's text and drop its buttons."""
        ...
