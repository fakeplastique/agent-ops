"""S32: the FileStorage contract, run identically against local disk, an in-process S3 (moto) and, when
TEST_S3_ENDPOINT points at one, a real S3-compatible server (Garage from `docker compose --profile storage`)."""

import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import boto3
import httpx
import pytest
from moto import mock_aws

from app.adapters.storage_local import LocalStorage
from app.adapters.storage_s3 import S3Storage
from app.domain.attachments import new_key
from tests.integration.helpers import FakeClock, utc

pytestmark = pytest.mark.anyio

REAL = os.environ.get("TEST_S3_ENDPOINT")
BUCKET = "contract-test"


@dataclass
class Store:
    storage: Any
    kind: str
    fetch: Callable | None  # url -> bytes, where the server can be reached from the test


async def chunks(*parts: bytes):
    for p in parts:
        yield p


def _s3(endpoint: str, bucket: str, key: str, secret: str, region: str = "garage") -> S3Storage:
    return S3Storage(
        endpoint=endpoint, public_endpoint="", bucket=bucket, access_key=key, secret_key=secret,
        region=region,
    )  # fmt: skip


@pytest.fixture(
    params=[
        "local",
        "moto",
        pytest.param(
            "garage", marks=pytest.mark.skipif(not REAL, reason="TEST_S3_ENDPOINT not set")
        ),
    ]
)
def store(request, tmp_path) -> Iterator[Store]:
    if request.param == "local":
        local = LocalStorage(tmp_path / "files", "contract-secret", FakeClock(utc(2026, 1, 1)))

        def read(url: str) -> bytes:
            found = local.open_signed(url.rsplit("/", 1)[1])
            assert found is not None
            return found.path.read_bytes()

        yield Store(local, "local", read)
    elif request.param == "moto":
        with mock_aws():
            boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
            yield Store(
                _s3("", BUCKET, "k", "s", "us-east-1"), "s3", None
            )  # moto cannot serve a presigned GET to httpx
    else:
        e = os.environ
        s3 = _s3(e["TEST_S3_ENDPOINT"], e["TEST_S3_BUCKET"], e["TEST_S3_KEY"], e["TEST_S3_SECRET"])
        yield Store(s3, "s3", lambda url: httpx.get(url).raise_for_status().content)


def key() -> str:
    return new_key(uuid.uuid4(), uuid.uuid4())


async def test_put_exists_delete_roundtrip(store):
    k = key()
    assert not await store.storage.exists(k)
    await store.storage.put(k, chunks(b"hello ", b"world"), "text/plain")
    assert await store.storage.exists(k)
    await store.storage.delete(k)
    assert not await store.storage.exists(k)
    await store.storage.delete(k)  # a missing key is not an error


async def test_a_stream_that_raises_leaves_nothing_behind(store):
    # catches: a half-written object left readable after a rejected upload
    k = key()

    async def broken():
        yield b"partial"
        raise RuntimeError("client went away")

    with pytest.raises(RuntimeError):
        await store.storage.put(k, broken(), "text/plain")
    assert not await store.storage.exists(k)


@pytest.mark.parametrize("bad", ["../x", "a/b/c", "", "x" * 300, "/etc/passwd"])
async def test_a_key_the_server_did_not_make_is_refused(store, bad):
    # catches: a client-shaped key reaching a path or an object name
    with pytest.raises(ValueError):
        await store.storage.put(bad, chunks(b"x"), "text/plain")
    with pytest.raises(ValueError):
        store.storage.signed_url(bad, filename="a.txt", content_type="text/plain", ttl_seconds=300)


async def test_signed_url_is_a_five_minute_download_of_the_same_bytes(store):
    k = key()
    await store.storage.put(k, chunks(b"payload"), "text/plain")
    url = store.storage.signed_url(
        k, filename='ré"sumé?.txt', content_type="text/plain", ttl_seconds=300
    )
    if store.kind == "s3":
        q = parse_qs(urlparse(url).query)
        assert q["X-Amz-Expires"] == ["300"]
        assert q["response-content-disposition"][0].startswith("attachment;")
        assert q["response-content-type"] == ["text/plain"]
        assert '"' not in q["response-content-disposition"][0].split("filename=")[1].strip('"')
    if store.fetch:
        assert store.fetch(url) == b"payload"


async def test_local_url_expires_after_ttl_and_names_no_path(tmp_path):
    clock = FakeClock(utc(2026, 1, 1))
    local = LocalStorage(tmp_path / "files", "s", clock)
    k = key()
    await local.put(k, chunks(b"x"), "text/plain")
    url = local.signed_url(k, filename="a.txt", content_type="text/plain", ttl_seconds=300)
    token = url.rsplit("/", 1)[1]
    assert (
        k not in url and str(tmp_path) not in url
    )  # the key is inside the signed payload, not a path
    assert local.open_signed(token) is not None
    clock._now += timedelta(seconds=301)
    assert local.open_signed(token) is None
    assert LocalStorage(tmp_path / "files", "other-secret", clock).open_signed(token) is None


@pytest.mark.skipif(not REAL, reason="TEST_S3_ENDPOINT not set")
async def test_a_real_s3_server_serves_the_signed_url_as_an_attachment():
    # catches: a presigned URL whose response overrides the server ignores (inline serving)
    e = os.environ
    s3 = _s3(e["TEST_S3_ENDPOINT"], e["TEST_S3_BUCKET"], e["TEST_S3_KEY"], e["TEST_S3_SECRET"])
    k = key()
    await s3.put(k, chunks(b"<b>x</b>"), "text/plain")
    url = s3.signed_url(k, filename="a.txt", content_type="text/plain", ttl_seconds=300)
    async with httpx.AsyncClient() as c:
        got = await c.get(url)
    assert got.headers["content-disposition"].startswith("attachment;")
    assert got.headers["content-type"] == "text/plain"
    await s3.delete(k)
