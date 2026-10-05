from fastapi import APIRouter

from app.api.schemas.base import BaseSchema
from tests.api.harness import probe_client

limits_router = APIRouter(prefix="/test-limits")


class SampleInput(BaseSchema):
    name: str


@limits_router.post("/echo")
def echo_payload(data: SampleInput):
    return {"name": data.name}


client = probe_client(limits_router)


def test_oversized_content_length_rejected_before_parsing():
    # 2 MB body when default max is 1 MB
    huge_data = b"x" * (2 * 1024 * 1024)
    res = client.post(
        "/test-limits/echo",
        content=huge_data,
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 413
    body = res.json()
    assert body["error"]["code"] == "payload_too_large"


def test_streamed_chunked_oversized_body_returns_413():
    def chunk_generator():
        for _ in range(30):
            yield b"x" * (64 * 1024)  # 30 * 64KB = 1.92 MB

    res = client.post(
        "/test-limits/echo",
        content=chunk_generator(),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 413
    body = res.json()
    assert body["error"]["code"] == "payload_too_large"


def test_unknown_field_422_and_deeply_nested_json_clean_4xx():
    # Unknown field
    res = client.post(
        "/test-limits/echo",
        json={"name": "valid", "extra_field": "disallowed"},
    )
    assert res.status_code == 422
    body = res.json()
    assert body["error"]["code"] == "invalid_input"

    # Deeply nested JSON
    deep_json = '{"name": "test", "nested": ' + ('{"k": ' * 200) + "1" + ("}" * 200) + "}"
    res_deep = client.post(
        "/test-limits/echo",
        content=deep_json.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert 400 <= res_deep.status_code < 500, f"Got {res_deep.status_code} for deep json"
    assert res_deep.json()["error"]["code"] in ("invalid_input", "bad_request")


def test_invalid_utf8_and_wrong_content_type_clean_4xx():
    # Invalid UTF-8
    res = client.post(
        "/test-limits/echo",
        content=b"\xff\xfe\xfd",
        headers={"Content-Type": "application/json"},
    )
    assert 400 <= res.status_code < 500, f"Got {res.status_code} for invalid utf8"
    assert "error" in res.json()

    # Wrong content-type
    res_type = client.post(
        "/test-limits/echo",
        content="plain text",
        headers={"Content-Type": "text/plain"},
    )
    assert 400 <= res_type.status_code < 500, f"Got {res_type.status_code} for wrong content type"
    assert "error" in res_type.json()
