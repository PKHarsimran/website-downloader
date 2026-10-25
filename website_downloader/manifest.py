from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import __version__


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CrawlManifest:
    """Collect resource outcomes across workers without recording request credentials."""

    def __init__(self, path: Path | None, root: Path, start_urls: list[str]) -> None:
        self.path = path
        self.root = root.resolve()
        self.start_urls = start_urls
        self.started_at = utc_now()
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, dict]] = {"page": {}, "asset": {}}

    def record(self, kind: str, url: str, *, only_new: bool = False, **fields) -> None:
        if self.path is None:
            return
        with self._lock:
            records = self._records[kind]
            if only_new and url in records:
                return
            if url not in records:
                records[url] = {
                    "url": url,
                    "local_path": None,
                    "status": "pending",
                    "status_code": None,
                    "content_type": None,
                    "reason": None,
                    "errors": [],
                }
                if kind == "page":
                    records[url].update(title=None, headings=[], assets=None)
            path = fields.pop("path", None)
            if path is not None:
                fields["local_path"] = Path(os.path.relpath(path.resolve(), self.root)).as_posix()
            records[url].update(fields)

    def write(self, stats, *, completed: bool, termination_reason: str) -> None:
        if self.path is None:
            return
        with self._lock:
            for records in self._records.values():
                for record in records.values():
                    if record["status"] == "pending":
                        record.update(status="skipped", reason=termination_reason)
            pages = sorted(self._records["page"].values(), key=lambda row: row["url"])
            assets = sorted(self._records["asset"].values(), key=lambda row: row["url"])
            errors = [
                {"url": row["url"], "kind": kind, **error}
                for kind, rows in (("page", pages), ("asset", assets))
                for row in rows
                for error in row["errors"]
            ]
            payload = {
                "schema_version": 1,
                "generator": {"name": "website-downloader", "version": __version__},
                "start_url": self.start_urls[0],
                "start_urls": self.start_urls,
                "started_at": self.started_at,
                "finished_at": utc_now(),
                "completed": completed,
                "termination_reason": termination_reason,
                "stats": asdict(stats),
                "pages": pages,
                "assets": assets,
                "errors": errors,
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent, suffix=".tmp", delete=False
            ) as file:
                temp_path = Path(file.name)
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.write("\n")
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
