from __future__ import annotations
from pathlib import Path
from google.cloud import storage

def upload_file(bucket_name: str, local_path: Path, gcs_path: str) -> None:
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(str(local_path))

def upload_dir(bucket_name: str, local_dir: Path, gcs_prefix: str) -> int:
    """
    Sube recursivamente local_dir a gs://bucket/gcs_prefix/<relative_path>
    Retorna cantidad de archivos subidos.
    """
    client = storage.Client()
    bucket = client.bucket(bucket_name)

    count = 0
    for p in local_dir.rglob("*"):
        if p.is_file():
            rel = p.relative_to(local_dir).as_posix()
            blob = bucket.blob(f"{gcs_prefix.rstrip('/')}/{rel}")
            blob.upload_from_filename(str(p))
            count += 1
    return count