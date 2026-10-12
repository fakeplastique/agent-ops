import asyncio
import logging
import tempfile
from collections.abc import AsyncIterator
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.domain.attachments import KEY_RE
from app.domain.errors import StorageUnavailable

logger = logging.getLogger(__name__)
_SPOOL = 1024 * 1024  # held in memory up to this size, on disk beyond it


class S3Storage:
    """Any S3-compatible store (Garage in compose, a bucket on Railway). Presigned URLs are built for
    `public_endpoint` (what a browser can reach), requests for `endpoint` (what the API can reach)."""

    def __init__(
        self, *, endpoint: str, public_endpoint: str, bucket: str, access_key: str, secret_key: str,
        region: str,
    ) -> None:  # fmt: skip
        self.bucket = bucket
        cfg = Config(
            signature_version="s3v4", s3={"addressing_style": "path"},
            connect_timeout=5, read_timeout=30, retries={"max_attempts": 2},
        )  # fmt: skip

        def client(url: str) -> Any:
            return boto3.client(
                "s3", endpoint_url=url or None, region_name=region, aws_access_key_id=access_key,
                aws_secret_access_key=secret_key, config=cfg,
            )  # fmt: skip

        self._c = client(endpoint)
        self._signer = client(public_endpoint or endpoint)

    @staticmethod
    def _key(key: str) -> str:
        if not KEY_RE.fullmatch(key):
            raise ValueError("not a storage key")
        return key

    async def put(self, key: str, chunks: AsyncIterator[bytes], content_type: str) -> None:
        # The stream is checked as it arrives; the object is only sent once it is complete, so a stream
        # that raises never reaches the bucket.
        with tempfile.SpooledTemporaryFile(max_size=_SPOOL) as buf:
            async for chunk in chunks:
                buf.write(chunk)
            buf.seek(0)
            await self._call(
                self._c.put_object, Bucket=self.bucket, Key=self._key(key), Body=buf,
                ContentType=content_type, ContentDisposition="attachment",
            )  # fmt: skip

    async def delete(self, key: str) -> None:
        await self._call(self._c.delete_object, Bucket=self.bucket, Key=self._key(key))

    async def exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(self._c.head_object, Bucket=self.bucket, Key=self._key(key))
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            logger.warning("storage head failed: %s", e.response.get("Error", {}).get("Code"))
            raise StorageUnavailable from e
        except BotoCoreError as e:
            raise StorageUnavailable from e
        return True

    def signed_url(self, key: str, *, filename: str, content_type: str, ttl_seconds: int) -> str:
        quoted = filename.encode("ascii", "replace").decode().replace('"', "_").replace("?", "_")
        return self._signer.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket, "Key": self._key(key), "ResponseContentType": content_type,
                "ResponseContentDisposition": f'attachment; filename="{quoted}"',
            },
            ExpiresIn=ttl_seconds,
        )  # fmt: skip

    async def _call(self, fn: Any, **kw: Any) -> None:
        try:
            await asyncio.to_thread(fn, **kw)
        except (BotoCoreError, ClientError) as e:
            logger.warning("storage call failed: %s", type(e).__name__)
            raise StorageUnavailable from e
