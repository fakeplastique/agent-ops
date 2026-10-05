from pathlib import Path

DOCKERFILE = Path(__file__).parents[2] / "Dockerfile"


def test_image_trusts_proxy_headers_so_ip_limits_see_the_client():
    # catches: every caller behind Railway/Next sharing one per-IP bucket
    cmd = DOCKERFILE.read_text()
    assert "--proxy-headers" in cmd and "--forwarded-allow-ips" in cmd
