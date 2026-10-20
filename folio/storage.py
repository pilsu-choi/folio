"""Artifact storage: local directory or S3-compatible bucket."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class LocalStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _p(self, key: str) -> Path:
        return self.root / key

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, key: str, data: bytes) -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def get_bytes(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._p(key).exists()

    def list(self, prefix: str = "") -> list[str]:
        base = self._p(prefix)
        if base.is_file():
            return [prefix]
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file())

    def delete(self, prefix: str) -> None:
        p = self._p(prefix)
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()

    def uri(self, key: str) -> str:
        return str(self._p(key).resolve())


class S3Storage:
    def __init__(self, bucket: str, prefix: str = ""):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
            config=Config(s3={"addressing_style": "path"}),
        )

    def _k(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def ensure(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            try:
                self.client.create_bucket(Bucket=self.bucket)
            except Exception as exc:  # concurrent creation is fine
                if "BucketAlready" not in str(exc):
                    raise

    def put_bytes(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._k(key), Body=data)

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except Exception:
            return False

    def list(self, prefix: str = "") -> list[str]:
        out: list[str] = []
        strip = len(self.prefix) + 1 if self.prefix else 0
        for page in self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=self._k(prefix)
        ):
            out += [o["Key"][strip:] for o in page.get("Contents", [])]
        return sorted(out)

    def delete(self, prefix: str) -> None:
        for key in self.list(prefix):
            self.client.delete_object(Bucket=self.bucket, Key=self._k(key))

    def uri(self, key: str) -> str:
        return f"s3://{self.bucket}/{self._k(key)}"


def _put_json(self: Any, key: str, obj: Any) -> None:
    self.put_bytes(key, json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8"))


def _get_json(self: Any, key: str) -> Any:
    return json.loads(self.get_bytes(key).decode("utf-8"))


for _cls in (LocalStorage, S3Storage):
    _cls.put_json = _put_json  # type: ignore[attr-defined]
    _cls.get_json = _get_json  # type: ignore[attr-defined]

Storage = LocalStorage | S3Storage


def open_storage(uri: str) -> Storage:
    p = urlparse(uri)
    if p.scheme == "s3":
        return S3Storage(p.netloc, p.path)
    if p.scheme == "file":
        return LocalStorage(p.path)
    return LocalStorage(uri)


def read_uri(uri: str) -> bytes:
    p = urlparse(uri)
    if p.scheme == "s3":
        return S3Storage(p.netloc).get_bytes(p.path.lstrip("/"))
    if p.scheme == "file":
        return Path(p.path).read_bytes()
    return Path(uri).read_bytes()
