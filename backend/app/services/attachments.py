import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.authz.matrix import Action, Role, can
from app.db.session import tenant_session
from app.domain.attachments import (
    MAX_BYTES,
    SIGNED_URL_TTL,
    SNIFF_BYTES,
    check_head,
    check_name,
    new_key,
    sanitize_filename,
)
from app.domain.errors import (
    Forbidden,
    InvalidInput,
    NotFoundError,
    PayloadTooLarge,
    StorageUnavailable,
)
from app.ports.storage import FileStorage
from app.services import audit, decision_reads, outbox

logger = logging.getLogger(__name__)

_COLUMNS = (
    "id, decision_request_id, filename, content_type, size_bytes, uploaded_by_type, uploaded_by_id,"
    " created_at"
)
_INSERT = text(
    "insert into attachments (organization_id, decision_request_id, filename, content_type, size_bytes,"
    " storage_key, uploaded_by_type, uploaded_by_id) values (:o, :r, :n, :t, :s, :k, :bt, :bi)"
    f" returning {_COLUMNS}"
)
_LIST = text(
    f"select {_COLUMNS} from attachments where organization_id = :o and decision_request_id = :r"
    " order by created_at, id limit :limit offset :skip"
)
_COUNT = text(
    "select count(*) from attachments where organization_id = :o and decision_request_id = :r"
)
_ONE = text(
    f"select {_COLUMNS}, storage_key from attachments"
    " where organization_id = :o and decision_request_id = :r and id = :a"
)
_DELETE = text("delete from attachments where organization_id = :o and id = :a")


@dataclass(frozen=True)
class Uploader:
    """Who adds a file: an agent identity or a person, plus the role that may remove it later."""

    kind: str  # "agent" | "user"
    id: uuid.UUID
    role: Role | None = None


async def _vetted(
    chunks: AsyncIterator[bytes], kind: str, done: dict[str, int]
) -> AsyncIterator[bytes]:
    """Counts and checks the stream as it passes: over the cap it raises (nothing more is read), and the
    first SNIFF_BYTES must be what the extension promises before the first byte is stored."""
    size, head, vetted = 0, b"", False
    async for chunk in chunks:
        size += len(chunk)
        if size > MAX_BYTES:
            raise PayloadTooLarge(MAX_BYTES)
        if vetted:
            yield chunk
            continue
        head += chunk
        if len(head) >= SNIFF_BYTES:
            check_head(kind, head, final=False)
            vetted = True
            yield head
    if not vetted:  # the whole file is shorter than the sniff window
        check_head(kind, head, final=True)
        yield head
    done["size"] = size


async def _record(
    session: AsyncSession, o: uuid.UUID, request_id: uuid.UUID, name: str, content_type: str,
    size: int, key: str, by: Uploader,
) -> dict[str, Any]:  # fmt: skip
    row = (
        (
            await session.execute(
                _INSERT,
                {
                    "o": o,
                    "r": request_id,
                    "n": name,
                    "t": content_type,
                    "s": size,
                    "k": key,
                    "bt": by.kind,
                    "bi": by.id,
                },
            )
        )
        .mappings()
        .one()
    )
    # Events and audit name the file by id, type and size, never by its (agent-written) name.
    data = {
        "decision_request_id": str(request_id),
        "content_type": content_type,
        "size_bytes": size,
    }
    await audit.record(session, o, "attachment", row["id"], "added", by.kind, by.id, data)
    await outbox.emit(session, o, "attachment.added", data | {"id": str(row["id"])})
    return dict(row)


async def upload(
    engine: AsyncEngine, storage: FileStorage, o: uuid.UUID, *, request_id: uuid.UUID,
    filename: str, declared_type: str | None, chunks: AsyncIterator[bytes], by: Uploader,
) -> dict[str, Any]:  # fmt: skip
    """Check the request is readable, stream the file into storage, then record it. No transaction is open
    while the bytes arrive (a slow client must not hold a pooled connection)."""
    async with tenant_session(engine, o) as s:
        await decision_reads.get_decision(s, o, request_id)  # 404 for a missing or foreign request
    if len(filename) > 1024:
        raise InvalidInput("filename is too long")
    name = sanitize_filename(filename)
    content_type, kind = check_name(name, declared_type)  # refused before a byte is read
    key, done = new_key(o, request_id), {"size": 0}
    try:
        await storage.put(key, _vetted(chunks, kind, done), content_type)
    except OSError as e:  # local disk full or unwritable
        raise StorageUnavailable from e
    try:
        async with tenant_session(engine, o) as s:
            return await _record(s, o, request_id, name, content_type, done["size"], key, by)
    except BaseException:
        await drop_object(storage, key)
        raise


async def drop_object(storage: FileStorage, key: str) -> None:
    """Best effort: a failed delete leaves a blob no row points to, never a row without its file."""
    try:
        await storage.delete(key)
    except StorageUnavailable:
        logger.error("orphaned attachment object left in storage")  # no key: it names the tenant


async def list_attachments(
    session: AsyncSession, o: uuid.UUID, request_id: uuid.UUID, skip: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    await decision_reads.get_decision(session, o, request_id)  # 404, not an empty page
    args = {"o": o, "r": request_id}
    rows = (await session.execute(_LIST, args | {"skip": skip, "limit": limit})).mappings().all()
    return [dict(r) for r in rows], int(await session.scalar(_COUNT, args) or 0)


async def _get(
    session: AsyncSession, o: uuid.UUID, request_id: uuid.UUID, attachment_id: uuid.UUID
):
    await decision_reads.get_decision(
        session, o, request_id
    )  # the same read authorization as the request
    row = (
        (await session.execute(_ONE, {"o": o, "r": request_id, "a": attachment_id}))
        .mappings()
        .first()
    )
    if row is None:
        raise NotFoundError
    return row


async def download_url(
    session: AsyncSession, storage: FileStorage, o: uuid.UUID, request_id: uuid.UUID,
    attachment_id: uuid.UUID,
) -> str:  # fmt: skip
    row = await _get(session, o, request_id, attachment_id)
    return storage.signed_url(
        row["storage_key"], filename=row["filename"], content_type=row["content_type"],
        ttl_seconds=SIGNED_URL_TTL,
    )  # fmt: skip


async def remove(
    session: AsyncSession,
    o: uuid.UUID,
    request_id: uuid.UUID,
    attachment_id: uuid.UUID,
    by: Uploader,
) -> str:
    """The uploader or an org_admin deletes the row; returns the storage key for the caller to drop
    after the commit (an object is never removed for a row that still stands)."""
    row = await _get(session, o, request_id, attachment_id)
    mine = (row["uploaded_by_type"], row["uploaded_by_id"]) == (by.kind, by.id)
    if not mine and not (by.role is not None and can(by.role, Action.MANAGE_ORG)):
        raise Forbidden("only the uploader or an org_admin may remove this file")
    await session.execute(_DELETE, {"o": o, "a": attachment_id})
    data = {"decision_request_id": str(request_id), "size_bytes": row["size_bytes"]}
    await audit.record(session, o, "attachment", attachment_id, "removed", by.kind, by.id, data)
    return row["storage_key"]
