import logging
import time
from functools import lru_cache
from typing import BinaryIO

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)
_NOT_FOUND = {"404", "NoSuchKey", "NotFound"}


def _make_client(settings: Settings, endpoint_url: str | None):
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


class Storage:
    """Thin wrapper around S3-compatible storage (AWS S3, MinIO, R2...).

    Two clients on purpose: inside Docker the API reaches MinIO at http://minio:9000,
    but a presigned URL must carry a host the *browser* can reach (http://localhost:9000).
    The signature covers the host, so the URL has to be signed with the public one.
    """

    def __init__(self, settings: Settings):
        self.bucket = settings.s3_bucket
        self.region = settings.s3_region
        self.expires = settings.presigned_expiration_seconds
        self._client = _make_client(settings, settings.s3_endpoint_url)
        self._signer = _make_client(
            settings, settings.s3_public_endpoint_url or settings.s3_endpoint_url
        )

    def ensure_bucket(self, retries: int = 10, delay: float = 1.0) -> None:
        for attempt in range(1, retries + 1):
            try:
                try:
                    self._client.head_bucket(Bucket=self.bucket)
                except ClientError as exc:
                    if exc.response["Error"]["Code"] not in _NOT_FOUND | {"NoSuchBucket"}:
                        raise
                    kwargs: dict = {"Bucket": self.bucket}
                    if self.region != "us-east-1":
                        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
                    self._client.create_bucket(**kwargs)
                    logger.info("Created bucket %s", self.bucket)
                return
            except (ClientError, BotoCoreError):
                if attempt == retries:
                    raise
                logger.warning("Storage not ready (attempt %s/%s)", attempt, retries)
                time.sleep(delay)

    def upload_fileobj(self, fileobj: BinaryIO, key: str, content_type: str) -> None:
        self._client.upload_fileobj(
            fileobj, self.bucket, key, ExtraArgs={"ContentType": content_type}
        )

    def presigned_post(self, key: str, content_type: str, size_bytes: int) -> dict:
        """Presigned POST lets S3 itself enforce the exact size and content type."""
        return self._signer.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", size_bytes, size_bytes],
            ],
            ExpiresIn=self.expires,
        )

    def presigned_get(self, key: str, filename: str, content_type: str) -> str:
        return self._signer.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentType": content_type,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=self.expires,
        )

    def head(self, key: str) -> dict | None:
        try:
            return self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in _NOT_FOUND:
                return None
            raise

    def read_head(self, key: str, n: int) -> bytes:
        obj = self._client.get_object(Bucket=self.bucket, Key=key, Range=f"bytes=0-{n - 1}")
        return obj["Body"].read()

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)


@lru_cache
def get_storage() -> Storage:
    return Storage(get_settings())
