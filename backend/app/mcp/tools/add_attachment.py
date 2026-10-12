import base64
import binascii
import uuid
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from app.api import deps
from app.api.v1.attachments import AttachmentOut, uploader_of
from app.domain.attachments import MAX_BYTES
from app.domain.errors import InvalidInput, PayloadTooLarge
from app.mcp.gating import gated
from app.mcp.tools.read import tool_errors
from app.mcp.tools.write import _hint, _start
from app.services import attachments

MAX_BASE64 = (MAX_BYTES + 2) // 3 * 4  # the base64 text of a file at the cap


async def _once(data: bytes):
    yield data


@gated(
    "attachments",
    probe={"decision_id": str(uuid.UUID(int=0)), "filename": "a.png", "content_base64": "AA=="},
)
async def add_attachment(
    decision_id: Annotated[uuid.UUID, Field(description="The decision request id")],
    filename: Annotated[str, Field(description="Shown to people; its extension picks the type")],
    content_base64: Annotated[
        str, Field(description="The file, base64 encoded; at most 10 MB decoded")
    ],
    content_type: Annotated[
        str | None,
        Field(
            description="Optional; must match the extension (pdf, png, jpg, gif, webp, txt, csv, json, docx, xlsx)"
        ),
    ] = None,
) -> AttachmentOut:
    """Attach a file to a request. Allowed: pdf, png, jpg, gif, webp, txt, csv, json, docx, xlsx (the first
    bytes must match). The name is for display only; the file is stored under a server-made key."""
    with tool_errors():
        p, _ = await _start()
        if (
            len(content_base64) > MAX_BASE64
        ):  # before decoding: a huge string must not become a huge buffer
            raise PayloadTooLarge(MAX_BYTES)
        try:
            data = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise InvalidInput("content_base64 is not valid base64") from e
        row = await attachments.upload(
            deps.get_engine(), deps.get_storage(), p.organization_id, request_id=decision_id,
            filename=filename, declared_type=content_type, chunks=_once(data), by=uploader_of(p),
        )  # fmt: skip
        return AttachmentOut(**row)


def register(server: MCPServer) -> None:
    server.add_tool(add_attachment, annotations=_hint(False))
