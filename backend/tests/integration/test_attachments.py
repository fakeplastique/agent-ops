"""S32 / FM26 / I16 / SC26: attachment upload and download (Pro, `attachments` flag)."""

import base64
import uuid
from datetime import timedelta

import pytest

from app.api import deps
from app.domain.attachments import KEY_RE, MAX_BYTES
from tests.api.harness import agent_auth, user_auth
from tests.integration.helpers import fetch, make_world, open_request, staff
from tests.mcp.conftest import mcp  # noqa: F401 (the fixture)

pytestmark = pytest.mark.anyio

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"


def url(request_id, *rest: object) -> str:
    return "/".join(["/api/v1/decision-requests", str(request_id), "attachments", *map(str, rest)])


async def world(owner, app_engine, plan="pro"):
    w = await make_world(owner, plan)
    req = await open_request(app_engine, w)
    return w, req["id"], await agent_auth(app_engine, w.org, w.identity)


async def up(web, h, request_id, name="a.png", body=PNG, ctype="image/png"):
    return await web.client.post(
        url(request_id), params={"filename": name}, content=body,
        headers={**h, "content-type": ctype},
    )  # fmt: skip


def stored_files(web) -> list[str]:
    root = web.storage.root
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize(
    "name",
    ["../../etc/passwd.png", "..\\..\\x.png", "/abs/x.png", "a\x00b.png", "rep\u202egnp.png",
     "nämé/\u2028.png", "..png", "a\r\nSet-Cookie: x=1.png"],
)  # fmt: skip
async def test_filename_with_traversal_nul_unicode_never_reaches_the_storage_key(
    name, web, owner_engine, app_engine
):
    # catches: the client filename used as a path or key
    w, rid, h = await world(owner_engine, app_engine)
    r = await up(web, h, rid, name)
    assert r.status_code == 201, r.text
    shown = r.json()["filename"]
    assert not any(c in shown for c in "/\\\x00\r\n\u202e\u2028"), shown
    [(key,)] = await fetch(
        owner_engine, "select storage_key from attachments where organization_id=:o", o=w.org
    )
    assert KEY_RE.fullmatch(key) and key.startswith(f"{w.org}/{rid}/"), key
    files = stored_files(web)
    assert files == [
        key
    ]  # the one file on disk is at the server-made key: nothing else was written


@pytest.mark.parametrize(
    "name, ctype, body",
    [
        ("a.svg", "image/svg+xml", b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>"),
        ("a.png", "image/png", b"<svg xmlns='http://www.w3.org/2000/svg'/>"),
        ("a.png", "image/png", b"<html><script>alert(1)</script></html>"),
        ("a.html", "text/html", b"<html></html>"),
        ("a.txt", "text/plain", b"<!DOCTYPE html><html></html>"),
        ("a.png", "application/pdf", PNG),
        ("a.pdf", "application/pdf", PNG),
        ("a.exe", "application/octet-stream", b"MZ\x90\x00"),
        ("noext", "image/png", PNG),
        ("x.png\x00.html", "image/png", PNG),  # a NUL that would cut the name short in C code
    ],
)
async def test_svg_html_and_mismatched_magic_bytes_refused(
    name, ctype, body, web, owner_engine, app_engine
):
    # catches: an allowlist checked by extension or declared type only
    w, rid, h = await world(owner_engine, app_engine)
    r = await up(web, h, rid, name, body, ctype)
    assert r.status_code == 415, r.text
    assert r.json()["error"]["code"] == "unsupported_media_type"
    assert not await fetch(
        owner_engine, "select 1 from attachments where organization_id=:o", o=w.org
    )
    assert stored_files(web) == []  # and nothing was left on disk


async def test_empty_file_is_422(web, owner_engine, app_engine):
    _, rid, h = await world(owner_engine, app_engine)
    r = await up(web, h, rid, "a.png", b"")
    assert r.status_code == 422, r.text


async def test_exactly_10mb_is_accepted(web, owner_engine, app_engine):
    _, rid, h = await world(owner_engine, app_engine)
    r = await up(web, h, rid, "big.png", PNG + b"\x00" * (MAX_BYTES - len(PNG)))
    assert r.status_code == 201 and r.json()["size_bytes"] == MAX_BYTES, r.text


async def test_over_10mb_rejected_while_streaming(web, owner_engine, app_engine):
    # catches: buffering the whole file before looking at its size
    w, rid, h = await world(owner_engine, app_engine)
    sent = 0
    chunk = 64 * 1024

    async def body():
        nonlocal sent
        yield PNG
        for _ in range(12 * 1024 * 1024 // chunk):  # 12 MB on offer, no Content-Length
            sent += chunk
            yield b"\x00" * chunk

    r = await web.client.post(
        url(rid), params={"filename": "big.png"}, content=body(),
        headers={**h, "content-type": "image/png"},
    )  # fmt: skip
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large", r.text
    assert sent < MAX_BYTES + 2 * 1024 * 1024, sent  # the server stopped reading near the cap
    assert not await fetch(
        owner_engine, "select 1 from attachments where organization_id=:o", o=w.org
    )
    assert stored_files(web) == []  # the partial file is gone


async def test_declared_content_length_over_cap_is_413_before_any_read(
    web, owner_engine, app_engine
):
    _, rid, h = await world(owner_engine, app_engine)
    r = await up(web, h, rid, "big.png", PNG + b"\x00" * (MAX_BYTES + 1))
    assert r.status_code == 413, r.text


async def test_free_plan_402_on_every_attachment_route(web, owner_engine, app_engine):
    # catches: an ungated upload, list, download or delete
    _w, rid, h = await world(owner_engine, app_engine, "free")
    any_id = uuid.uuid4()
    for r in (
        await up(web, h, rid),
        await web.client.get(url(rid), headers=h),
        await web.client.get(url(rid, any_id, "download"), headers=h),
        await web.client.delete(url(rid, any_id), headers=h),
    ):
        assert r.status_code == 402, r.text
        assert r.json()["error"]["details"] == {"flag": "attachments", "required_plan": "pro"}


async def test_unauthenticated_and_unknown_request(web, owner_engine, app_engine):
    _w, rid, h = await world(owner_engine, app_engine)
    assert (await up(web, {}, rid)).status_code == 401
    assert (await up(web, h, uuid.uuid4())).status_code == 404  # before the body is read


async def test_list_shows_metadata_only_and_pages(web, owner_engine, app_engine):
    _w, rid, h = await world(owner_engine, app_engine)
    await up(web, h, rid, "a.png")
    await up(web, h, rid, "b.pdf", PDF, "application/pdf")
    r = await web.client.get(url(rid), params={"limit": 1}, headers=h)
    body = r.json()
    assert (
        r.status_code == 200 and body["total"] == 2 and len(body["items"]) == 1 and body["nextLink"]
    )
    item = body["items"][0]
    assert set(item) == {
        "id", "decision_request_id", "filename", "content_type", "size_bytes", "uploaded_by_type",
        "uploaded_by_id", "created_at",
    }  # fmt: skip
    assert "storage_key" not in r.text  # the key never leaves


async def test_download_requires_request_read_access_then_redirects_to_signed_url_5min(
    web, owner_engine, app_engine
):
    # catches: a bearer-less direct URL, or a URL that outlives 5 minutes
    w, rid, h = await world(owner_engine, app_engine)
    att = (await up(web, h, rid, "report.pdf", PDF, "application/pdf")).json()["id"]
    assert (
        await web.client.get(url(rid, att, "download"))
    ).status_code == 401  # no credential, no URL
    r = await web.client.get(url(rid, att, "download"), headers=h, follow_redirects=False)
    assert r.status_code == 307, r.text
    signed = r.headers["location"]
    assert r.headers["cache-control"] == "no-store"
    got = await web.client.get(signed)  # no credential: the URL is the capability
    assert got.status_code == 200 and got.content == PDF
    web.clock.at += timedelta(seconds=299)
    assert (await web.client.get(signed)).status_code == 200
    web.clock.at += timedelta(seconds=2)  # 301 s after it was minted
    expired = await web.client.get(signed)
    assert expired.status_code == 404, expired.text
    # a person of the same organization (session-less PAT) is authorized the same way
    member = await user_auth(app_engine, w.org, await staff(owner_engine, w.org, "member"))
    again = await web.client.get(url(rid, att, "download"), headers=member, follow_redirects=False)
    assert again.status_code == 307


async def test_signed_url_served_as_attachment_with_nosniff(web, owner_engine, app_engine):
    # catches: stored XSS through inline serving
    _w, rid, h = await world(owner_engine, app_engine)
    att = (await up(web, h, rid, 'résumé "x".pdf', PDF, "application/pdf")).json()["id"]
    r = await web.client.get(url(rid, att, "download"), headers=h)
    got = await web.client.get(r.headers["location"])
    assert got.headers["content-type"] == "application/pdf"
    assert got.headers["content-disposition"].startswith("attachment;")
    assert "\r" not in got.headers["content-disposition"]
    assert got.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in got.headers["content-security-policy"]


async def test_tampered_or_foreign_signed_urls_are_404(web, owner_engine, app_engine):
    _w, rid, h = await world(owner_engine, app_engine)
    att = (await up(web, h, rid)).json()["id"]
    r = await web.client.get(url(rid, att, "download"), headers=h)
    signed = r.headers["location"]
    payload, sig = signed.rsplit("/", 1)[1].split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    other = await world(owner_engine, app_engine)
    other_key = (await up(web, other[2], other[1])).json()["id"]
    [(k2,)] = await fetch(
        owner_engine, "select storage_key from attachments where id=:i", i=other_key
    )
    forged = base64.urlsafe_b64encode(
        base64.urlsafe_b64decode(payload + "==").replace(
            (await fetch(owner_engine, "select storage_key from attachments where id=:i", i=att))[0][0].encode(),
            k2.encode(),
        )
    ).rstrip(b"=").decode()  # fmt: skip
    for bad in (f"{payload}.{flipped}", f"{forged}.{sig}", "x.y", "....", payload, ""):
        got = await web.client.get(f"/api/v1/files/{bad}")
        assert got.status_code == 404, (bad, got.text)
    assert (await web.client.get(signed)).status_code == 200  # the honest one still works


async def test_cross_tenant_attachment_404_and_key_prefix_is_org(web, owner_engine, app_engine):
    # catches: tenant B guessing or replaying a key or id
    a_w, a_rid, a_h = await world(owner_engine, app_engine)
    b_w, b_rid, b_h = await world(owner_engine, app_engine)
    att = (await up(web, a_h, a_rid)).json()["id"]
    [(key,)] = await fetch(owner_engine, "select storage_key from attachments where id=:i", i=att)
    assert key.startswith(f"{a_w.org}/")
    nowhere = (
        await web.client.get(url(uuid.uuid4(), uuid.uuid4(), "download"), headers=b_h)
    ).json()
    for path in (
        url(a_rid, att, "download"),
        url(b_rid, att, "download"),
    ):  # A's request, or B's own
        r = await web.client.get(path, headers=b_h)
        assert r.status_code == 404 and r.json() == nowhere, r.text
    assert (await web.client.delete(url(a_rid, att), headers=b_h)).status_code == 404
    assert (
        await web.client.get(url(a_rid), headers=b_h)
    ).status_code == 404  # list: not an empty page
    assert (await up(web, b_h, a_rid)).status_code == 404
    assert len(await fetch(owner_engine, "select 1 from attachments where id=:i", i=att)) == 1
    # the database refuses a key outside the row's own organization prefix
    with pytest.raises(Exception, match="key_in_org_prefix"):
        await fetch(
            owner_engine,
            "insert into attachments (organization_id, decision_request_id, filename, content_type,"
            " size_bytes, storage_key, uploaded_by_type, uploaded_by_id)"
            " values (:o, :r, 'x.png', 'image/png', 1, :k, 'user', :u) returning id",
            o=b_w.org, r=b_rid, k=key, u=b_w.owner_user,
        )  # fmt: skip


async def test_delete_by_uploader_or_org_admin_only_removes_row_and_file(
    web, owner_engine, app_engine
):
    w, rid, h = await world(owner_engine, app_engine)
    att = (await up(web, h, rid)).json()["id"]
    member = await user_auth(app_engine, w.org, await staff(owner_engine, w.org, "member"))
    r = await web.client.delete(url(rid, att), headers=member)
    assert r.status_code == 403, r.text  # another member is not the uploader and no admin
    assert len(stored_files(web)) == 1
    admin = await user_auth(app_engine, w.org, await staff(owner_engine, w.org, "org_admin"))
    assert (await web.client.delete(url(rid, att), headers=admin)).status_code == 204
    assert stored_files(web) == []
    assert (await web.client.get(url(rid), headers=h)).json()["total"] == 0
    att2 = (await up(web, h, rid)).json()["id"]
    assert (await web.client.delete(url(rid, att2), headers=h)).status_code == 204  # the uploader
    assert (await web.client.delete(url(rid, att2), headers=h)).status_code == 404
    acts = await fetch(
        owner_engine,
        "select action from audit_logs where organization_id=:o and entity_type='attachment' order by created_at",
        o=w.org,
    )  # fmt: skip
    assert [a for (a,) in acts] == ["added", "removed", "added", "removed"]


async def test_added_event_and_audit_row_name_no_file_content_or_filename(
    web, owner_engine, app_engine
):
    w, rid, h = await world(owner_engine, app_engine)
    await up(web, h, rid, "secret-name.png")
    [(typ, payload)] = await fetch(
        owner_engine,
        "select type, payload from outbox_events where organization_id=:o and type='attachment.added'",
        o=w.org,
    )  # fmt: skip
    assert typ == "attachment.added" and "secret-name" not in str(payload)
    assert set(payload) == {"id", "decision_request_id", "content_type", "size_bytes"}
    [(data,)] = await fetch(
        owner_engine,
        "select data from audit_logs where organization_id=:o and entity_type='attachment'",
        o=w.org,
    )  # fmt: skip
    assert "secret-name" not in str(data)


async def test_storage_failure_is_503_and_leaves_no_row(web, owner_engine, app_engine, monkeypatch):
    from app.domain.errors import StorageUnavailable

    w, rid, h = await world(owner_engine, app_engine)

    async def boom(*a, **k):
        raise StorageUnavailable

    monkeypatch.setattr(web.storage, "put", boom)
    r = await up(web, h, rid)
    assert r.status_code == 503 and r.json()["error"]["code"] == "storage_unavailable"
    assert not await fetch(
        owner_engine, "select 1 from attachments where organization_id=:o", o=w.org
    )


async def test_row_insert_failure_removes_the_stored_object(
    web, owner_engine, app_engine, monkeypatch
):
    # catches: an orphaned blob when the database refuses the row after the bytes were stored
    from app.services import attachments

    _w, rid, h = await world(owner_engine, app_engine)

    async def refuse(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(attachments, "_record", refuse)
    with pytest.raises(
        RuntimeError
    ):  # the in-process client re-raises what the 500 handler answered
        await up(web, h, rid)
    assert stored_files(web) == []


# MCP: the same service behind add_attachment (content as base64).


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def test_mcp_add_attachment_stores_lists_and_refuses_bad_input(
    mcp,  # noqa: F811
    web,
    owner_engine,
    app_engine,
    monkeypatch,
):
    monkeypatch.setattr(
        deps, "_storage", web.storage
    )  # the tool reads the store itself, not by injection
    w, rid, h = await world(owner_engine, app_engine)
    async with mcp.client(h) as c:
        ok = await c.call_tool(
            "add_attachment",
            {"decision_id": str(rid), "filename": "../x/shot.png", "content_base64": b64(PNG)},
        )
        assert not ok.is_error, ok.content
        assert ok.structured_content["filename"] == "shot.png"
        for args in (
            {"filename": "a.svg", "content_base64": b64(b"<svg/>")},
            {"filename": "a.png", "content_base64": b64(PDF)},
            {"filename": "a.png", "content_base64": "not base64!!"},
            {"filename": "a.png", "content_base64": ""},
            {"filename": "a.png", "content_base64": b64(PNG), "content_type": "application/pdf"},
        ):
            r = await c.call_tool("add_attachment", {"decision_id": str(rid)} | args)
            assert r.is_error, (args, r.content)
        other = await c.call_tool(
            "add_attachment",
            {"decision_id": str(uuid.uuid4()), "filename": "a.png", "content_base64": b64(PNG)},
        )
        assert other.is_error and "not found" in other.content[0].text
    rows = await fetch(
        owner_engine,
        "select filename, uploaded_by_type from attachments where organization_id=:o",
        o=w.org,
    )
    assert rows == [("shot.png", "agent")]
    got = await web.client.get(url(rid), headers=h)
    assert got.json()["total"] == 1


async def test_mcp_oversize_base64_refused_before_decoding(mcp, owner_engine, app_engine):  # noqa: F811
    _w, rid, h = await world(owner_engine, app_engine)
    async with mcp.client(h) as c:
        r = await c.call_tool(
            "add_attachment",
            {
                "decision_id": str(rid),
                "filename": "a.png",
                "content_base64": "A" * (MAX_BYTES * 4 // 3 + 100),
            },
        )
    assert r.is_error and "payload too large" in r.content[0].text
