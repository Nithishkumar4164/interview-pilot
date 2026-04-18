import re
import uuid
from io import BytesIO

import boto3
from botocore.config import Config as BotoConfig

from src.config import settings
from src.utils.logging import get_logger

logger = get_logger("storage")

ALLOWED_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB

_SAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._\- ]+")


def sanitize_filename(name: str | None, *, max_len: int = 120) -> str:
    """Return a filename safe to store in S3 metadata or echo back to a
    client. Strips path separators and non-alphanumeric characters,
    collapses whitespace, and bounds the length.

    Returns ``"resume"`` for empty or all-filtered inputs so downstream
    code never has to handle an empty filename.
    """
    if not name:
        return "resume"
    # Drop any directory components a client might try to smuggle in.
    base = name.replace("\\", "/").split("/")[-1]
    cleaned = _SAFE_FILENAME_CHARS.sub("_", base).strip()
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned[:max_len] if cleaned else "resume"


class StorageService:
    """S3-compatible storage service for resume uploads (MinIO in dev)."""

    def __init__(self) -> None:
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=BotoConfig(signature_version="s3v4"),
            region_name="us-east-1",
        )
        self.bucket = settings.s3_bucket

    def ensure_bucket(self) -> None:
        """Create the bucket if it doesn't exist."""
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)
            logger.info("bucket_created", bucket=self.bucket)

    def upload_file(self, file_content: bytes, content_type: str, original_filename: str) -> str:
        """Upload a file and return its reference ID."""
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ValueError(f"Unsupported file type: {content_type}. Allowed: {list(ALLOWED_CONTENT_TYPES.keys())}")

        if len(file_content) > MAX_FILE_SIZE_BYTES:
            raise ValueError(f"File too large: {len(file_content)} bytes. Max: {MAX_FILE_SIZE_BYTES}")

        ext = ALLOWED_CONTENT_TYPES[content_type]
        reference_id = str(uuid.uuid4())
        key = f"resumes/{reference_id}{ext}"
        safe_filename = sanitize_filename(original_filename)

        self.client.upload_fileobj(
            BytesIO(file_content),
            self.bucket,
            key,
            ExtraArgs={
                "ContentType": content_type,
                "Metadata": {"original_filename": safe_filename},
            },
        )

        logger.info(
            "file_uploaded",
            reference_id=reference_id,
            key=key,
            size_bytes=len(file_content),
        )
        return reference_id

    def download_file(self, reference_id: str, ext: str) -> bytes:
        """Download a file by its reference ID."""
        key = f"resumes/{reference_id}{ext}"
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def get_file_key(self, reference_id: str) -> str | None:
        """Find the actual key for a reference ID (tries both .pdf and .docx)."""
        for ext in [".pdf", ".docx"]:
            key = f"resumes/{reference_id}{ext}"
            try:
                self.client.head_object(Bucket=self.bucket, Key=key)
                return key
            except Exception:
                continue
        return None
