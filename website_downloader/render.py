from __future__ import annotations

from types import TracebackType
from urllib.parse import urlparse

from .constants import DEFAULT_USER_AGENT


class PlaywrightRenderer:
    """Small optional wrapper for JavaScript-rendered HTML pages."""

    def __init__(
        self,
        *,
        start_url: str,
        cookies: dict[str, str] | None = None,
        timeout_ms: int = 30000,
        wait_until: str = "networkidle",
        user_agent: str | None = None,
    ) -> None:
        self.start_url = start_url
        self.cookies = cookies or {}
        self.timeout_ms = timeout_ms
        self.wait_until = wait_until
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self._playwright = None
        self._browser = None
        self._context = None

    def __enter__(self) -> PlaywrightRenderer:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "JavaScript rendering needs the optional Playwright extra. "
                "Install it with: pip install -e .[render] && playwright install chromium"
            ) from exc

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._context = self._browser.new_context(user_agent=self.user_agent)
        self._add_cookies()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._context is not None:
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def _add_cookies(self) -> None:
        if not self.cookies or self._context is None:
            return
        parsed = urlparse(self.start_url)
        cookie_url = f"{parsed.scheme}://{parsed.netloc}"
        self._context.add_cookies(
            [
                {"name": name, "value": value, "url": cookie_url}
                for name, value in self.cookies.items()
            ]
        )

    def fetch(self, url: str) -> str:
        if self._context is None:
            raise RuntimeError("Renderer is not started")

        page = self._context.new_page()
        try:
            page.goto(url, wait_until=self.wait_until, timeout=self.timeout_ms)
            return page.content()
        finally:
            page.close()
