"""Tests of the web scraping, with a fake Wikipedia (no network)."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from acquisition_plans.collecte import scraping_web

PAGE = """<!DOCTYPE html><html lang="fr"><head>
<title>Circuit de voie — Wikipédia</title><meta charset="utf-8"></head><body>
<div class="vector-toc"><h2>Sommaire</h2></div>
<div id="mw-content-text"><div class="mw-content-ltr mw-parser-output">
<section data-mw-section-id="0">
<div class="bandeau-container homonymie">
<p>Pour les articles homonymes, voir CDV.</p></div>
<table class="infobox"><tr><td><p>Texte d'un tableau.</p></td></tr></table>
<p>Un <b>circuit de voie</b> détecte la présence d'un train<sup class="reference">
<a href="#n1">[1]</a></sup>.<br>Il commande les signaux.</p>
<p>   </p>
<p>Il sert au <a href="/wiki/BAL">block automatique</a>.</p>
</section>
<section data-mw-section-id="1"><div class="mw-heading mw-heading2"><h2>Principe</h2>
<span class="mw-editsection">[modifier]</span></div>
<p>Texte de la section Principe, hors introduction.</p></section>
</div></div></body></html>"""
ROBOTS_ALLOW = "User-agent: *\nDisallow: /w/\n"
ROBOTS_DENY = "User-agent: *\nDisallow: /wiki/\n"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


def site(robots: str = ROBOTS_ALLOW, page: str = PAGE, failures=()):
    """Fake ``urlopen``: robots.txt, then pages; the first answers can fail."""
    pending = list(failures)
    requests = []

    def opener(request, timeout):
        del timeout
        requests.append(request)
        if pending:
            raise pending.pop(0)
        body = robots if request.full_url.endswith("/robots.txt") else page
        return FakeResponse(body.encode("utf-8"))

    opener.requests = requests
    return opener


class ScrapingTests(unittest.TestCase):
    def test_only_the_article_introduction_is_kept(self) -> None:
        title, paragraphs = scraping_web.extract_introduction(PAGE)
        self.assertEqual(title, "Circuit de voie")
        self.assertEqual(
            paragraphs,
            [
                "Un circuit de voie détecte la présence d'un train. "
                "Il commande les signaux.",
                "Il sert au block automatique.",
            ],
        )

    def test_legacy_html_without_sections_is_read_too(self) -> None:
        legacy = (
            '<div class="mw-parser-output"><p>Intro.</p>'
            "<h2>Suite</h2><p>Hors introduction.</p></div>"
        )
        self.assertEqual(scraping_web.extract_introduction(legacy), ("", ["Intro."]))

    def test_main_checks_robots_and_saves_the_pages(self) -> None:
        opener = site()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "web" / "pages.json"
            with contextlib.redirect_stderr(io.StringIO()):
                code = scraping_web.main(
                    ["--sortie", str(target)], opener=opener, pause=0
                )
            snapshot = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertTrue(opener.requests[0].full_url.endswith("/robots.txt"))
        self.assertEqual(
            opener.requests[1].get_header("User-agent"), scraping_web.USER_AGENT
        )
        self.assertEqual(snapshot["licence"], "CC BY-SA 4.0")
        self.assertEqual(len(snapshot["pages"]), len(scraping_web.PAGES))
        self.assertEqual(snapshot["pages"][0]["titre"], "Circuit de voie")

    def test_refusals_and_errors_stop_the_collection(self) -> None:
        cases = [
            ("robots.txt", site(robots=ROBOTS_DENY)),
            ("aucun paragraphe", site(page="<html><body></body></html>")),
            ("HTTP 404", site(failures=[HTTPError("u", 404, "Not Found", {}, None)])),
        ]
        for reason, opener in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(scraping_web.ScrapingError) as raised:
                    scraping_web.scrape_pages(scraping_web.PAGES, opener, pause=0)
                self.assertIn(reason.split()[-1], str(raised.exception))

    def test_network_errors_are_retried(self) -> None:
        flaky = site(failures=[URLError("coupure"), TimeoutError("lent")])
        with (
            patch.object(scraping_web.time, "sleep"),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            pages = scraping_web.scrape_pages(scraping_web.PAGES, flaky, pause=0)
            self.assertEqual(len(pages), len(scraping_web.PAGES))
            offline = site(failures=[URLError("coupure")] * 3)
            code = scraping_web.main(["--sortie", "inutile.json"], offline, pause=0)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
