from collections.abc import AsyncIterator
from typing import Protocol


class FileStorage(Protocol):
    """Blob storage behind attachments. Keys are server-made (`domain.attachments.new_key`); an adapter never
    sees a client filename except as the display name inside a signed URL."""

    async def put(self, key: str, chunks: AsyncIterator[bytes], content_type: str) -> None:
        """Store the stream under the key. A stream that raises leaves nothing behind."""
        ...

    async def delete(self, key: str) -> None:
        """Remove the object; a missing key is not an error."""
        ...

    async def exists(self, key: str) -> bool: ...

    def signed_url(self, key: str, *, filename: str, content_type: str, ttl_seconds: int) -> str:
        """A URL that serves the object as a download (never inline) until `ttl_seconds` pass."""
        ...
