from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


RenderPage = Callable[[str], str]


@dataclass(frozen=True)
class RenderedHtml:
    url: str
    html: str
    browser: str
    selector: str


class RenderedHtmlFetcher:
    """Fetch fully rendered page DOM through Playwright or an injected renderer."""

    def __init__(
        self,
        *,
        render_page: RenderPage | None = None,
        browser: str = "chromium",
        wait_until: str = "networkidle",
    ) -> None:
        self._render_page = render_page
        self.browser = browser
        self.wait_until = wait_until

    def __call__(self, url: str) -> str:
        return self.render(url).html

    def render(self, url: str) -> RenderedHtml:
        html = self._render_page(url) if self._render_page else _render_with_playwright(
            url, self.browser, self.wait_until
        )
        return RenderedHtml(
            url=url,
            html=html,
            browser=self.browser,
            selector="document.documentElement",
        )


def rendered_loader(
    entry_url: str,
    document_id: str,
    *,
    storage_dir: Path | None = None,
    rendered_storage_dir: Path | None = None,
    metadata_path: Path | None = None,
    render_page: RenderPage | None = None,
):
    """Build the regular HTML loader with rendered DOM retrieval."""
    from app.ingestion.html_loader import HtmlLoader

    fetcher = RenderedHtmlFetcher(render_page=render_page)
    return HtmlLoader(
        entry_url,
        document_id,
        fetch_html=fetcher,
        storage_dir=storage_dir,
        rendered_storage_dir=rendered_storage_dir,
        metadata_path=metadata_path,
    )


def _render_with_playwright(url: str, browser_name: str, wait_until: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError(
            "Playwright is required for rendered HTML retrieval. Install it and run "
            "'playwright install chromium'."
        ) from error

    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name, None)
        if browser_type is None:
            raise ValueError(f"unsupported browser: {browser_name}")
        try:
            browser = browser_type.launch(headless=True)
        except Exception as error:
            raise RuntimeError(
                "Playwright browser could not start. Run 'playwright install chromium'."
            ) from error
        try:
            page = browser.new_page()
            page.goto(url, wait_until=wait_until)
            return page.locator("html").evaluate("element => element.outerHTML")
        finally:
            browser.close()


def store_rendered_html(root: Path, document_id: str, rendered: RenderedHtml) -> Path:
    import hashlib

    digest = hashlib.sha256(rendered.html.encode("utf-8")).hexdigest()
    output_dir = root / document_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{digest}.html"
    if not output_path.exists():
        output_path.write_text(rendered.html, encoding="utf-8")
    return output_path
