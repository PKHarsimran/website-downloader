from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class CacheEntry:
    url: str
    path: str
    kind: str
    status_code: int
    etag: str | None = None
    last_modified: str | None = None


class CrawlCache:
    def __init__(self, entries: dict[str, CacheEntry] | None = None) -> None:
        self.entries = entries or {}

    @classmethod
    def load(cls, path: Path) -> CrawlCache:
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = {
            url: CacheEntry(**entry)
            for url, entry in raw.get("entries", {}).items()
            if isinstance(entry, dict)
        }
        return cls(entries)

    def get(self, url: str) -> CacheEntry | None:
        return self.entries.get(url)

    def conditional_headers(self, url: str) -> dict[str, str]:
        entry = self.get(url)
        if entry is None:
            return {}
        headers: dict[str, str] = {}
        if entry.etag:
            headers["If-None-Match"] = entry.etag
        if entry.last_modified:
            headers["If-Modified-Since"] = entry.last_modified
        return headers

    def update(
        self,
        *,
        url: str,
        path: Path,
        kind: str,
        status_code: int,
        response_headers: dict[str, Any],
    ) -> None:
        self.entries[url] = CacheEntry(
            url=url,
            path=path.as_posix(),
            kind=kind,
            status_code=status_code,
            etag=response_headers.get("ETag"),
            last_modified=response_headers.get("Last-Modified"),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "entries": {
                url: asdict(entry)
                for url, entry in sorted(self.entries.items(), key=lambda item: item[0])
            },
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
