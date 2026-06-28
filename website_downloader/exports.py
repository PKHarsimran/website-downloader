from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile


@dataclass
class SavedResource:
    url: str
    path: Path
    kind: str
    status_code: int
    content_type: str | None = None


def create_zip_archive(root: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_zip = zip_path.resolve()
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.resolve() == resolved_zip:
                continue
            archive.write(path, path.relative_to(root).as_posix())


def write_warc(records: list[SavedResource], warc_path: Path) -> None:
    warc_path.parent.mkdir(parents=True, exist_ok=True)
    with warc_path.open("wb") as file:
        for record in records:
            if not record.path.exists():
                continue
            payload = record.path.read_bytes()
            http_block = _http_response_block(record, payload)
            warc_header = _warc_header(record, len(http_block))
            file.write(warc_header)
            file.write(http_block)
            file.write(b"\r\n\r\n")


def _http_response_block(record: SavedResource, payload: bytes) -> bytes:
    content_type = record.content_type or "application/octet-stream"
    headers = (
        f"HTTP/1.1 {record.status_code} OK\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "\r\n"
    ).encode()
    return headers + payload


def _warc_header(record: SavedResource, content_length: int) -> bytes:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    header = (
        "WARC/1.1\r\n"
        "WARC-Type: response\r\n"
        f"WARC-Target-URI: {record.url}\r\n"
        f"WARC-Date: {now}\r\n"
        f"WARC-Record-ID: <urn:uuid:{uuid4()}>\r\n"
        "Content-Type: application/http; msgtype=response\r\n"
        f"Content-Length: {content_length}\r\n"
        f"WARC-Profile: {record.kind}\r\n"
        "\r\n"
    )
    return header.encode("utf-8")
