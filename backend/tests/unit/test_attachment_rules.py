"""S32 / I16: the upload rules are pure: filename sanitizing, the allowlist and the magic-byte check."""

import uuid

import pytest

from app.domain import attachments as a
from app.domain.errors import InvalidInput, UnsupportedFileType

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
HOSTILE = [
    "../../etc/passwd", "..\\..\\windows\\system32\\cmd.exe", "/abs/path/report.pdf",
    "a\x00b.pdf", "rep\u202eftd.pdf", "name\r\nX-Evil: 1.pdf", "\u0000", "   ", "...", "a" * 900 + ".pdf",
    "caf\u00e9/\u0301/\u202e.png", "C:\\x\\y.png", "x.pdf\x00.html",
]  # fmt: skip


@pytest.mark.parametrize("name", HOSTILE)
def test_sanitized_name_is_a_plain_display_name(name):
    # catches: a client filename carrying a path, a NUL, a bidi override or a header break through to storage
    out = a.sanitize_filename(name)
    assert 1 <= len(out) <= 120
    assert not any(c in out for c in "/\\\x00\r\n\u202e"), out
    assert not out.startswith(".")


def test_sanitized_name_keeps_the_extension_when_truncating():
    assert a.sanitize_filename("a" * 900 + ".pdf").endswith(".pdf")
    assert a.sanitize_filename("../x/report.final.PDF") == "report.final.PDF"


def test_storage_key_is_org_request_and_a_fresh_uuid():
    org, req = uuid.uuid4(), uuid.uuid4()
    k1, k2 = a.new_key(org, req), a.new_key(org, req)
    assert k1 != k2 and k1.startswith(f"{org}/{req}/") and a.KEY_RE.fullmatch(k1)
    for bad in ("../x", f"{org}/../{req}/x", f"{org}/{req}/{uuid.uuid4()}/x", "", f"{org}\\{req}"):
        assert not a.KEY_RE.fullmatch(bad), bad


def test_png_and_pdf_pass_with_their_own_type():
    assert a.check_upload("a.png", "image/png", PNG, final=True) == "image/png"
    assert a.check_upload("a.pdf", None, PDF, final=True) == "application/pdf"  # type derived
    assert (
        a.check_upload("a.TXT", "text/plain; charset=utf-8", b"hello", final=True) == "text/plain"
    )


@pytest.mark.parametrize(
    "name, declared, head",
    [
        ("a.svg", "image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
        ("a.png", "image/png", b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
        ("a.png", "image/png", b"<!doctype html><script>alert(1)</script>"),
        ("a.txt", "text/plain", b"  \n<html><body>x</body></html>"),
        ("a.txt", "text/plain", b"\xef\xbb\xbf<html></html>"),
        ("a.html", "text/html", b"<html></html>"),
        ("a.png", "application/pdf", PNG),  # declared type is not the extension's
        ("a.pdf", "application/pdf", PNG),  # magic bytes of another type
        ("a.png", "image/png", PDF),
        ("a.png", "image/png", b"GIF89a" + b"\x00" * 10),
        ("a.exe", "application/octet-stream", b"MZ\x90\x00"),
        ("a", "image/png", PNG),  # no extension
        ("a.txt", "text/plain", b"binary\x00\x01\x02"),
        ("a.txt", "text/plain", b"\xff\xfe<\x00h\x00t\x00m\x00l\x00>\x00"),  # UTF-16
        ("a.json", "application/json", b"plain words"),
        ("a.webp", "image/webp", b"RIFF\x00\x00\x00\x00WAVEfmt "),
    ],
)
def test_svg_html_and_mismatched_magic_bytes_refused(name, declared, head):
    # catches: the allowlist checked by extension or declared type only
    with pytest.raises(UnsupportedFileType):
        a.check_upload(name, declared, head, final=True)


def test_empty_file_refused_and_truncated_utf8_tail_tolerated():
    with pytest.raises(InvalidInput):
        a.check_upload("a.txt", "text/plain", b"", final=True)
    cut = "h\u00e9llo \u20ac".encode()[:-1]  # the euro sign cut mid-character by the sniff window
    assert a.check_upload("a.txt", "text/plain", cut, final=False) == "text/plain"
    with pytest.raises(UnsupportedFileType):
        a.check_upload("a.txt", "text/plain", cut, final=True)


def test_hosted_local_storage_refuses_the_dev_url_secret_and_s3_needs_its_bucket(monkeypatch):
    # catches: forgeable download URLs in production, or s3 selected with nothing to talk to
    from app.config import Settings

    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    monkeypatch.setenv("RECEIPT_KEYS", "k:" + "r" * 40)
    monkeypatch.setenv("WEBHOOK_SECRET_KEYS", f"k:{'A' * 43}=")
    with pytest.raises(ValueError, match="FILE_URL_SECRET"):
        Settings()
    monkeypatch.setenv("FILE_STORAGE", "s3")
    with pytest.raises(ValueError, match="S3_BUCKET"):
        Settings()
    monkeypatch.setenv("S3_BUCKET", "b")
    monkeypatch.setenv("S3_ACCESS_KEY", "k")
    assert Settings().file_storage == "s3"  # the URL secret is unused with s3
    monkeypatch.setenv("FILE_STORAGE", "ftp")
    with pytest.raises(ValueError, match="FILE_STORAGE"):
        Settings()
