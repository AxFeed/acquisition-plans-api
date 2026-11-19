"""Scrape public web pages (French Wikipedia) and save their introduction.

The script reads robots.txt first and stops if a page is not allowed, sends a
generic User-Agent, waits between requests and retries on network or server
errors. From each page it keeps the paragraphs of the introduction, the part before
the first section title, without the reference marks such as [1], and saves them
with their provenance (address, licence CC BY-SA 4.0, date) in ``donnees/web/``.

Run from the repository root::

    python -B -m acquisition_plans.collecte.scraping_web
"""

import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import logging
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

ROBOTS_URL = "https://fr.wikipedia.org/robots.txt"
PAGES = {
    "block-automatique-lumineux": (
        "https://fr.wikipedia.org/wiki/Block_automatique_lumineux"
    ),
    "circuit-de-voie": "https://fr.wikipedia.org/wiki/Circuit_de_voie",
}
USER_AGENT = "collecte-pedagogique/1.0"
DEFAULT_TARGET = (
    Path(__file__).resolve().parents[1] / "donnees" / "web" / "pages-wikipedia.json"
)
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "track",
    "wbr",
}

logger = logging.getLogger("collecte.scraping_web")


class ScrapingError(RuntimeError):
    """Raised when a page cannot be fetched, is not allowed or has no text."""


class _IntroductionParser(HTMLParser):
    """Keep the paragraphs written directly in the article, before the first h2.

    A paragraph counts if its parent is the article body (``mw-parser-output``) or
    its first section; paragraphs of banners, tables and captions are ignored, and
    so are the reference marks (``sup``), styles and edit links.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, dict]] = []
        self.title = ""
        self.paragraphs: list[str] = []
        self._in_title = False
        self._in_body = False
        self._finished = False
        self._in_paragraph = False
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID_TAGS:
            if tag == "br" and self._in_paragraph and not self._skip_depth:
                self._parts.append(" ")
            return
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        parent = self.stack[-1] if self.stack else ("", {})
        self.stack.append((tag, attributes))
        if tag == "title":
            self._in_title = True
        if tag == "div" and "mw-parser-output" in classes:
            self._in_body = True
        if not self._in_body or self._finished:
            return
        if self._skip_depth:
            self._skip_depth += 1
        elif tag == "h2":
            self._finished = True
        elif tag == "p" and self._is_article_level(parent):
            self._in_paragraph = True
            self._parts = []
        elif self._in_paragraph and (
            tag in {"sup", "style", "script"} or "mw-editsection" in classes
        ):
            self._skip_depth = 1

    @staticmethod
    def _is_article_level(parent: tuple[str, dict]) -> bool:
        tag, attributes = parent
        classes = (attributes.get("class") or "").split()
        if tag == "div" and "mw-parser-output" in classes:
            return True
        return tag == "section" and attributes.get("data-mw-section-id") == "0"

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS or not self.stack:
            return
        if tag == "title":
            self._in_title = False
        if self._skip_depth:
            self._skip_depth -= 1
        elif tag == "p" and self._in_paragraph:
            text = re.sub(r"\s+", " ", "".join(self._parts)).strip()
            if text:
                self.paragraphs.append(text)
            self._in_paragraph = False
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._in_paragraph and not self._skip_depth and not self._finished:
            self._parts.append(data)


def extract_introduction(html: str) -> tuple[str, list[str]]:
    """Return the page title and the paragraphs of its introduction."""
    parser = _IntroductionParser()
    parser.feed(html)
    title = re.sub(r"\s+[—-]\s+Wikipédia$", "", parser.title.strip())
    return title, parser.paragraphs


def fetch_text(url: str, opener=urlopen, timeout: float = 20.0, attempts: int = 3):
    """Download a page as text with the generic User-Agent; retry on 5xx or network."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with opener(request, timeout=timeout) as response:
                return response.read().decode("utf-8")
        except HTTPError as error:
            if error.code < 500:
                raise ScrapingError(f"réponse HTTP {error.code} pour {url}") from error
            last_error = error
        except (URLError, TimeoutError) as error:
            last_error = error
        logger.warning(
            "Tentative %s sur %s échouée : %s", attempt, attempts, last_error
        )
        if attempt < attempts:
            time.sleep(attempt)
    raise ScrapingError(f"page injoignable après {attempts} tentatives : {last_error}")


def allowed_by_robots(robots_text: str, url: str) -> bool:
    rules = RobotFileParser()
    rules.parse(robots_text.splitlines())
    return rules.can_fetch(USER_AGENT, url)


def scrape_pages(pages: dict[str, str], opener=urlopen, pause: float = 1.0) -> list:
    """Check robots.txt, then fetch each page and keep its introduction."""
    robots_text = fetch_text(ROBOTS_URL, opener=opener)
    results = []
    for name, url in pages.items():
        if not allowed_by_robots(robots_text, url):
            raise ScrapingError(f"page interdite par robots.txt : {url}")
        time.sleep(pause)
        title, paragraphs = extract_introduction(fetch_text(url, opener=opener))
        if not paragraphs:
            raise ScrapingError(f"aucun paragraphe d'introduction trouvé : {url}")
        results.append(
            {"nom": name, "url": url, "titre": title, "paragraphes": paragraphs}
        )
    return results


def save_snapshot(pages: list[dict], target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "source": "Wikipédia en français, pages web (scraping)",
        "licence": "CC BY-SA 4.0",
        "auteurs": "Contributeurs de Wikipédia",
        "robots_txt": ROBOTS_URL,
        "user_agent": USER_AGENT,
        "date_collecte": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages": pages,
    }
    target.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scraping de pages Wikipédia.")
    parser.add_argument("--sortie", type=Path, default=DEFAULT_TARGET)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, opener=urlopen, pause: float = 1.0) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    try:
        pages = scrape_pages(PAGES, opener=opener, pause=pause)
    except ScrapingError as error:
        logger.error("Collecte interrompue : %s", error)
        return 1
    path = save_snapshot(pages, args.sortie)
    for page in pages:
        logger.info("%s : %s paragraphe(s)", page["titre"], len(page["paragraphes"]))
    logger.info("%s pages écrites dans %s", len(pages), path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
