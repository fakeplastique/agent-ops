"""Attachment rules (I16): what may be stored, how a name is shown, and what a storage key looks like. Pure."""

import codecs
import re
import unicodedata
import uuid

from app.domain.errors import InvalidInput, UnsupportedFileType

MAX_BYTES = 10 * 1024 * 1024
SNIFF_BYTES = 4096  # the head that is checked before the first byte is stored
NAME_MAX = 120
SIGNED_URL_TTL = 300  # seconds
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# extension -> (content type, kind of bytes it must start with). SVG, HTML and XML are not here on purpose:
# they run script when opened. The stored type comes from this table, never from the client.
ALLOWED: dict[str, tuple[str, str]] = {
    ".pdf": ("application/pdf", "pdf"),
    ".png": ("image/png", "png"),
    ".jpg": ("image/jpeg", "jpeg"),
    ".jpeg": ("image/jpeg", "jpeg"),
    ".gif": ("image/gif", "gif"),
    ".webp": ("image/webp", "webp"),
    ".txt": ("text/plain", "text"),
    ".csv": ("text/csv", "text"),
    ".json": ("application/json", "json"),
    ".docx": (DOCX, "zip"),
    ".xlsx": (XLSX, "zip"),
}
_UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
KEY_RE = re.compile(f"{_UUID}/{_UUID}/{_UUID}")  # <organization>/<request>/<attachment>
_DROP = {
    "Cc",
    "Cf",
    "Cs",
    "Co",
    "Cn",
    "Zl",
    "Zp",
}  # controls, format (bidi), unassigned, line separators
_BAD = str.maketrans({c: "_" for c in '<>:"|?*'})
_TEXT_CONTROLS = {9, 10, 13}  # tab, LF, CR are text; every other C0 control is not


def new_key(organization_id: uuid.UUID, request_id: uuid.UUID) -> str:
    """Server-made: nothing the client sent is in it, and the first segment is the tenant."""
    return f"{organization_id}/{request_id}/{uuid.uuid4()}"


def sanitize_filename(name: str) -> str:
    """A display name: last path segment, no control or format characters (NUL, bidi), no leading dots."""
    name = unicodedata.normalize("NFC", name).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if unicodedata.category(c) not in _DROP).translate(_BAD)
    name = name.rstrip(" .")  # a trailing dot or space is dropped by some filesystems
    if name.startswith("."):
        name = "attachment" + name  # no dotfiles, and ".png" keeps its extension
    if len(name) > NAME_MAX:
        stem, dot, ext = name.rpartition(".")
        ext = ext[:16] if dot else ""
        name = stem[: NAME_MAX - len(ext) - 1] + "." + ext if ext else name[:NAME_MAX]
    return name or "attachment"


def _media_type(declared: str | None) -> str | None:
    return declared.split(";", 1)[0].strip().lower() if declared else None


def _is_text(head: bytes, final: bool) -> bool:
    try:
        text = codecs.getincrementaldecoder("utf-8")().decode(head, final=final)
    except UnicodeDecodeError:
        return False
    # A markup start is HTML or SVG whatever the extension says; a BOM may precede it.
    if text.lstrip("﻿ \t\r\n").startswith("<"):
        return False
    return not any(ord(c) < 32 and ord(c) not in _TEXT_CONTROLS for c in text)


def _kind_matches(kind: str, head: bytes, final: bool) -> bool:
    match kind:
        case "pdf":
            return head.startswith(b"%PDF-")
        case "png":
            return head.startswith(b"\x89PNG\r\n\x1a\n")
        case "jpeg":
            return head.startswith(b"\xff\xd8\xff")
        case "gif":
            return head[:6] in (b"GIF87a", b"GIF89a")
        case "webp":
            return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
        case "zip":
            return head.startswith(b"PK\x03\x04")
        case "json":
            return _is_text(head, final) and head.lstrip(b"\xef\xbb\xbf \t\r\n")[:1] in (b"{", b"[")
        case _:
            return _is_text(head, final)


def check_name(filename: str, declared: str | None) -> tuple[str, str]:
    """(content type to store, kind of bytes expected) from the extension, or UnsupportedFileType. The
    declared type may be None (the extension decides) but never disagrees with it."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED:
        raise UnsupportedFileType("this file type is not allowed")
    content_type, kind = ALLOWED[ext]
    if declared is not None and _media_type(declared) != content_type:
        raise UnsupportedFileType("the declared content type does not match the file name")
    return content_type, kind


def check_head(kind: str, head: bytes, *, final: bool) -> None:
    """The first bytes must be what the extension promises. `final`: the head is the whole file."""
    if not head:
        raise InvalidInput("the file is empty")
    if not _kind_matches(kind, head, final):
        raise UnsupportedFileType("the file content does not match its type")


def check_upload(filename: str, declared: str | None, head: bytes, *, final: bool) -> str:
    content_type, kind = check_name(filename, declared)
    check_head(kind, head, final=final)
    return content_type
