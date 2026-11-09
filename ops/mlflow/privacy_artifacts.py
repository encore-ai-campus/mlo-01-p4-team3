"""MLflow Garage 버킷에서 승인 목록의 객체만 정리한다."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re


BUCKET = "pj4-mlflow-artifacts"


def object_hash(client, key):
    """객체 내용을 해시하고 존재하지 않으면 None을 반환한다."""
    from botocore.exceptions import ClientError

    try:
        body = client.get_object(Bucket=BUCKET, Key=key)["Body"]
    except ClientError as error:
        if error.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise
    digest = hashlib.sha256()
    try:
        for chunk in body.iter_chunks():
            digest.update(chunk)
    finally:
        body.close()
    return digest.hexdigest()


def purge_objects(client, entries, *, apply=False):
    """모든 객체를 먼저 검증하고 삭제 뒤 실제로 읽을 수 없는지 확인한다."""
    if not entries:
        raise ValueError("explicit_garage_object_scope_required")
    seen, planned, missing = set(), [], 0
    for entry in entries:
        key, digest = entry["key"], entry["sha256"]
        path = PurePosixPath(key)
        if path.is_absolute() or not path.parts or ".." in path.parts or path.as_posix() != key or key in seen:
            raise ValueError("invalid_or_duplicate_object_key")
        if not re.fullmatch("[0-9a-f]{64}", digest):
            raise ValueError("object_sha256_required")
        seen.add(key)
        actual = object_hash(client, key)
        if actual is None:
            missing += 1
        elif actual != digest:
            raise ValueError("garage_object_hash_mismatch")
        else:
            planned.append(key)
    if apply:
        for key in planned:
            client.delete_object(Bucket=BUCKET, Key=key)
            if object_hash(client, key) is not None:
                raise ValueError("garage_object_still_readable")
    return {"verified": len(planned), "already_missing": missing, "deleted": len(planned) if apply else 0}


def main(argv=None):
    """보호 설정의 사설 Garage TLS 주소로만 요청한다."""
    from urllib.parse import urlsplit
    from ipaddress import ip_address
    import boto3

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-drained", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.writers_drained:
        parser.error("privacy_garage_writers_drain_required")
    if args.manifest.is_symlink() or (os.name != "nt" and args.manifest.stat().st_mode & 0o077):
        parser.error("privacy_protected_manifest_required")
    endpoint = urlsplit(os.environ.get("MLFLOW_S3_ENDPOINT_URL", ""))
    if (endpoint.scheme != "https" or not endpoint.hostname or not ip_address(endpoint.hostname).is_private
            or endpoint.username or endpoint.password):
        parser.error("private_garage_tls_required")
    client = boto3.client("s3", endpoint_url=endpoint.geturl())
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    print(json.dumps(purge_objects(client, manifest["objects"], apply=args.apply)))


if __name__ == "__main__":
    main()
