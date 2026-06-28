from __future__ import annotations

import logging
from types import TracebackType

log = logging.getLogger(__name__)


class ProgressReporter:
    def __enter__(self) -> ProgressReporter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def page_seen(self, current: int, total: int, url: str) -> None:
        return None

    def page_saved(self, cached: bool = False) -> None:
        return None

    def asset_queued(self) -> None:
        return None

    def asset_saved(self, cached: bool = False) -> None:
        return None

    def error(self) -> None:
        return None


class RichProgressReporter(ProgressReporter):
    def __init__(self, max_pages: int) -> None:
        from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total} pages"),
            TextColumn("assets {task.fields[assets_saved]}/{task.fields[assets_queued]}"),
            TextColumn("cached {task.fields[cached]}"),
            TimeElapsedColumn(),
        )
        self.task_id = self.progress.add_task(
            "Mirroring",
            total=max_pages,
            assets_queued=0,
            assets_saved=0,
            cached=0,
        )
        self.assets_queued = 0
        self.assets_saved = 0
        self.pages_cached = 0

    def __enter__(self) -> RichProgressReporter:
        self.progress.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.progress.__exit__(exc_type, exc, tb)

    def page_seen(self, current: int, total: int, url: str) -> None:
        self.progress.update(
            self.task_id,
            completed=max(0, current - 1),
            description=f"Mirroring {url}",
            total=total,
        )

    def page_saved(self, cached: bool = False) -> None:
        if cached:
            self.pages_cached += 1
            self._refresh_fields()
        self.progress.advance(self.task_id)

    def asset_queued(self) -> None:
        self.assets_queued += 1
        self._refresh_fields()

    def asset_saved(self, cached: bool = False) -> None:
        self.assets_saved += 1
        if cached:
            self.pages_cached += 1
        self._refresh_fields()

    def _refresh_fields(self) -> None:
        self.progress.update(
            self.task_id,
            assets_queued=self.assets_queued,
            assets_saved=self.assets_saved,
            cached=self.pages_cached,
        )


def create_progress_reporter(enabled: bool, max_pages: int) -> ProgressReporter:
    if not enabled:
        return ProgressReporter()
    try:
        return RichProgressReporter(max_pages)
    except ImportError:
        log.warning("Install the ux extra to use --progress: pip install -e .[ux]")
        return ProgressReporter()
