"""Florida Sunbiz MCP server.

Read-only search of the public Division of Corporations site at
https://search.sunbiz.org/. The site needs no API credential. The MCP mount
uses SUNBIZ_MCP_KEY like the other servers.

The site sits behind a Cloudflare check that rejects plain HTTP clients
from this host, so lookups run through a headed Chromium (Xvfb when there
is no display).
"""

from __future__ import annotations

import asyncio
import atexit
import os
import re
import shutil
import subprocess
import time
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from .shared import create_fastmcp

SUNBIZ_INSTRUCTIONS = """
Florida Department of State, Division of Corporations (Sunbiz) public search.
Read-only. Never file, amend, reinstate, or resign anything.

search_sunbiz returns the alphabetical neighborhood of a term (about 20 rows),
not a ranked exact-match list. Follow detail_url or document_number with
get_sunbiz_record for status, addresses, registered agent, and officers.
Pass next_page_url or previous_page_url back as page_url to page through results.
""".strip()

mcp = create_fastmcp("sunbiz", instructions=SUNBIZ_INSTRUCTIONS)

BASE_URL = "https://search.sunbiz.org"
SEARCH_URL = f"{BASE_URL}/Inquiry/CorporationSearch/SearchResults"
DOCUMENT_URL = f"{BASE_URL}/Inquiry/CorporationSearch/ByDocumentNumber"
MAX_TERM_LENGTH = 45
MAX_FILINGS = 20
PAGE_TIMEOUT_MS = int(float(os.getenv("SUNBIZ_TIMEOUT", "60")) * 1000)

InquiryType = Literal[
    "entity_name",
    "officer_or_registered_agent",
    "registered_agent",
    "fei_ein",
    "trademark",
    "trademark_owner",
    "street_address",
    "zip_code",
]

_INQUIRY_PARAM = {
    "entity_name": "EntityName",
    "officer_or_registered_agent": "OfficerRegisteredAgentName",
    "registered_agent": "RegisteredAgentName",
    "fei_ein": "FeiNumber",
    "trademark": "TrademarkName",
    "trademark_owner": "TrademarkOwnerName",
    "street_address": "Address",
    "zip_code": "ZipCode",
}

_HEADER_KEYS = {
    "corporate name": "entity_name",
    "entity name": "entity_name",
    "trademark name": "trademark_name",
    "officer/ra name": "officer_name",
    "registered agent name": "registered_agent_name",
    "owner name": "owner_name",
    "document number": "document_number",
    "entity number": "document_number",
    "fei/ein number": "fei_ein",
    "status": "status",
    "street address": "street_address",
    "zip": "zip",
}

_FILING_KEYS = {
    "document number": "document_number",
    "fei/ein number": "fei_ein",
    "date filed": "date_filed",
    "effective date": "effective_date",
    "state": "state",
    "status": "status",
    "last event": "last_event",
    "event date filed": "last_event_filed",
    "event effective date": "last_event_effective",
}

_EMPTY_VALUES = {"", "NONE", "None", "N/A", "NULL"}
_PAGING_TITLES = {"Next List": "next_page_url", "Previous List": "previous_page_url"}


def _clean_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _clean_text(value)
    if cleaned in _EMPTY_VALUES:
        return None
    return cleaned


def _canonical(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _absolute(href: str) -> str:
    return urljoin(BASE_URL, href)


def _omit_empty(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        cleaned[key] = value
    return cleaned


def _header_key(header: str) -> str:
    normalized = _clean_text(header).lower()
    if normalized in _HEADER_KEYS:
        return _HEADER_KEYS[normalized]
    return re.sub(r"[^a-z0-9]+", "_", normalized).strip("_") or "column"


def _is_allowed_sunbiz_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname == "search.sunbiz.org"
        and parsed.path.startswith("/Inquiry/CorporationSearch/")
    )


def _normalize_sunbiz_url(url: str) -> str:
    candidate = url.strip()
    if candidate.startswith("/"):
        candidate = BASE_URL + candidate
    if not _is_allowed_sunbiz_url(candidate):
        raise ValueError("page_url must be a search.sunbiz.org corporation search URL")
    return candidate


class _PageParser(HTMLParser):
    """Parse a Sunbiz search-results or detail page in one pass."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.h2 = ""
        self.headers: list[str] = []
        self.rows: list[list[dict[str, str]]] = []
        self.sections: list[dict[str, Any]] = []
        self.paging: dict[str, str] = {}
        self.nav_links: dict[str, str] = {}
        self._in_results = False
        self._results_depth = 0
        self._in_h2 = False
        self._cell: str | None = None
        self._cell_parts: list[str] = []
        self._cell_href: str | None = None
        self._row: list[dict[str, str]] = []
        self._section: dict[str, Any] | None = None
        self._addr: list[str] | None = None
        self._link_href = ""
        self._link_title = ""
        self._link_parts: list[str] = []
        self._in_link = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if tag == "div" and attr.get("id") == "search-results":
            self._in_results = True
            self._results_depth = 1
            return
        if tag == "div" and self._in_results:
            self._results_depth += 1

        classes = attr.get("class", "").split()
        if tag == "div" and "detailSection" in classes:
            self._section = {"class": attr.get("class", ""), "events": []}
            self._addr = None
            return

        if tag == "a":
            self._in_link = True
            self._link_href = attr.get("href", "")
            self._link_title = attr.get("title", "")
            self._link_parts = []
            if self._cell == "td" and not self._cell_href and self._link_href:
                self._cell_href = self._link_href

        if self._in_results and tag == "h2":
            self._in_h2 = True
        if self._in_results and tag in {"th", "td"}:
            self._cell = tag
            self._cell_parts = []
            self._cell_href = None
        if self._in_results and tag == "br" and self._cell:
            self._cell_parts.append(" ")

        if self._section is None:
            return
        if tag == "div":
            classes = self._section.get("class", "")
            # Filing blocks use a wrapper div around labels. Address blocks use
            # a div for the street lines only.
            if "filingInformation" in classes or "corporationName" in classes:
                return
            self._addr = []
            self._section["events"].append(("address", self._addr))
            return
        if tag == "br":
            if self._addr is not None:
                self._addr.append("\n")
            else:
                self._section["events"].append(("br", ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_link:
            text = _clean_text("".join(self._link_parts))
            title = _clean_text(self._link_title)
            href = self._link_href
            if title in _PAGING_TITLES and href and _PAGING_TITLES[title] not in self.paging:
                self.paging[_PAGING_TITLES[title]] = _absolute(href)
            if title in {"View Events", "View Name History"} and href:
                self.nav_links[title] = _absolute(href)
            if (
                self._section is not None
                and text
                and href
                and "view image in pdf format" not in text.lower()
                and ("GetDocument" in href or "ConvertTiffToPDF" in href)
            ):
                self._section["events"].append(("filing", (href, text)))
            self._in_link = False
            self._link_href = ""
            self._link_title = ""
            self._link_parts = []

        if tag == "h2" and self._in_h2:
            self._in_h2 = False
        if tag in {"th", "td"} and self._cell == tag:
            text = _clean_text("".join(self._cell_parts))
            if tag == "th" and text:
                self.headers.append(text)
            elif tag == "td":
                cell = {"text": text}
                if self._cell_href:
                    cell["href"] = self._cell_href
                self._row.append(cell)
            self._cell = None
        if tag == "tr" and self._in_results and self._row:
            self.rows.append(self._row)
            self._row = []
        if tag == "div" and self._addr is not None:
            self._addr = None
            return
        if tag == "div" and self._section is not None and self._addr is None:
            # The address div close is handled above. A detailSection close
            # has no open address buffer. Nested address divs set _addr.
            self.sections.append(self._section)
            self._section = None
            return
        if tag == "div" and self._in_results:
            self._results_depth -= 1
            if self._results_depth <= 0:
                self._in_results = False

    def handle_data(self, data: str) -> None:
        if self._in_link:
            self._link_parts.append(data)
        if self._in_h2:
            self.h2 += data
        if self._cell:
            self._cell_parts.append(data)
        if self._section is None:
            return
        if self._addr is not None:
            self._addr.append(data)
            return
        text = _clean_text(data)
        if text:
            self._section["events"].append(("text", text))


def _join_address(parts: list[str]) -> str | None:
    lines = [_clean_text(line) for line in "".join(parts).splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return None
    return ", ".join(lines)


def _section_texts(section: dict[str, Any]) -> list[str]:
    return [value for kind, value in section["events"] if kind == "text" and isinstance(value, str)]


def _section_addresses(section: dict[str, Any]) -> list[str]:
    addresses: list[str] = []
    for kind, value in section["events"]:
        if kind == "address" and isinstance(value, list):
            joined = _join_address(value)
            if joined:
                addresses.append(joined)
    return addresses


def _changed_date(text: str, label: str) -> str | None:
    match = re.search(rf"{label}:\s*(.+)", text, flags=re.I)
    if not match:
        return None
    return _blank_to_none(match.group(1))


def _parse_people(section: dict[str, Any], group: str) -> list[dict[str, Any]]:
    people: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    name_parts: list[str] = []

    def finish() -> None:
        nonlocal current, name_parts
        if current is None:
            return
        name = _clean_text(" ".join(name_parts))
        if name:
            current["name"] = name
        people.append(_omit_empty(current))
        current = None
        name_parts = []

    for kind, value in section["events"]:
        if kind == "text" and isinstance(value, str) and value.lower().startswith("title"):
            finish()
            current = {"section": group, "title": _clean_text(value[5:])}
            continue
        if current is None:
            continue
        if kind == "text" and isinstance(value, str):
            if value.lower() in {"name & address", group.lower()}:
                continue
            name_parts.append(value)
        elif kind == "address" and isinstance(value, list):
            address = _join_address(value)
            if address:
                current["address"] = address
    finish()
    return people


def _parse_annual_reports(section: dict[str, Any]) -> list[dict[str, str]]:
    # Annual report rows are plain text pairs inside the section, but the
    # table cells are not tracked separately. Re-read from link-less text
    # is unreliable, so the caller passes table rows captured as text only
    # when the section heading is Annual Reports. Cell text is emitted in
    # order: header, header, year, date, year, date...
    texts = _section_texts(section)
    if texts and texts[0].lower() == "annual reports":
        texts = texts[1:]
    reports: list[dict[str, str]] = []
    index = 0
    if len(texts) >= 2 and "year" in texts[0].lower():
        index = 2
    while index + 1 < len(texts):
        year = texts[index]
        filed = texts[index + 1]
        if year.isdigit():
            reports.append({"year": year, "filed_date": filed})
        index += 2
    return reports


def _parse_filings(section: dict[str, Any]) -> list[dict[str, str]]:
    filings: list[dict[str, str]] = []
    for kind, value in section["events"]:
        if kind != "filing" or not isinstance(value, tuple):
            continue
        href, label = value
        label = _clean_text(label)
        if label and "no images are available" not in label.lower():
            filings.append({"label": label, "url": _absolute(href)})
    return filings


def _parse_address_block(section: dict[str, Any]) -> dict[str, Any]:
    texts = _section_texts(section)
    addresses = _section_addresses(section)
    heading = texts[0] if texts else ""
    payload: dict[str, Any] = {"heading": heading}
    if addresses:
        payload["address"] = addresses[0]
    for text in texts[1:]:
        lowered = text.lower()
        if lowered.startswith("changed:"):
            payload["changed"] = _changed_date(text, "Changed")
        elif lowered.startswith("address changed:"):
            payload["address_changed"] = _changed_date(text, "Address Changed")
        elif lowered.startswith("name changed:"):
            payload["name_changed"] = _changed_date(text, "Name Changed")
        elif lowered not in {"name & address"}:
            payload.setdefault("name", text)
    return payload


def parse_search_results(html: str, source_url: str, search_term: str) -> dict[str, Any]:
    parser = _PageParser()
    parser.feed(html)
    keys = [_header_key(header) for header in parser.headers]
    results: list[dict[str, Any]] = []
    query_canonical = _canonical(search_term)
    query_digits = _digits(search_term)

    for row in parser.rows:
        record: dict[str, Any] = {}
        detail_url = ""
        for index, cell in enumerate(row):
            key = keys[index] if index < len(keys) else f"column_{index + 1}"
            text = cell.get("text") or ""
            if text:
                record[key] = text
            href = cell.get("href") or ""
            if href and not detail_url:
                detail_url = _absolute(href)
        if detail_url:
            record["detail_url"] = detail_url
        fields = [
            str(record.get(field) or "")
            for field in (
                "entity_name",
                "trademark_name",
                "officer_name",
                "registered_agent_name",
                "owner_name",
                "fei_ein",
            )
        ]
        exact = any(value and _canonical(value) == query_canonical for value in fields)
        if not exact and len(query_digits) >= 9:
            exact = any(value and _digits(value) == query_digits for value in fields)
        record["exact_match"] = exact
        results.append(record)

    payload: dict[str, Any] = {
        "list_title": _clean_text(parser.h2) or None,
        "search_term": search_term or None,
        "result_count": len(results),
        "results": results,
        "source_url": source_url,
        "note": (
            "Sunbiz lists the alphabetical neighborhood of the search term, "
            "about 20 records per page. exact_match marks a normalized full match."
        ),
    }
    payload.update(parser.paging)
    return _omit_empty(payload)


def parse_detail(html: str, source_url: str) -> dict[str, Any]:
    if "Document Not Found" in html and "detailSection" not in html:
        return {"error": "Document not found", "source_url": source_url}

    parser = _PageParser()
    parser.feed(html)
    if not parser.sections:
        title = _clean_text(parser.h2)
        if "Just a moment" in html:
            return {"error": "Sunbiz Cloudflare challenge did not clear", "source_url": source_url}
        return {"error": "Sunbiz detail page did not contain a filing record", "source_url": source_url, "title": title or None}

    record: dict[str, Any] = {"detail_url": source_url}
    officers: list[dict[str, Any]] = []
    filings: list[dict[str, str]] = []
    other: list[dict[str, str]] = []

    for section in parser.sections:
        classes = section.get("class", "")
        texts = _section_texts(section)
        heading = texts[0] if texts else ""
        heading_key = heading.lower()

        if "corporationName" in classes:
            if len(texts) >= 1:
                record["filing_type"] = texts[0]
            if len(texts) >= 2:
                record["entity_name"] = texts[1]
            continue

        if "filingInformation" in classes:
            pairs = texts[1:] if heading_key == "filing information" else texts
            for index in range(0, len(pairs) - 1, 2):
                label = pairs[index].lower()
                field = _FILING_KEYS.get(label)
                if not field:
                    continue
                record[field] = _blank_to_none(pairs[index + 1])
            continue

        if heading_key in {"principal address", "mailing address"}:
            block = _parse_address_block(section)
            prefix = "principal" if heading_key.startswith("principal") else "mailing"
            record[f"{prefix}_address"] = block.get("address")
            record[f"{prefix}_address_changed"] = block.get("changed")
            continue

        if "registered agent" in heading_key:
            block = _parse_address_block(section)
            record["registered_agent"] = _omit_empty(
                {
                    "name": block.get("name"),
                    "address": block.get("address"),
                    "name_changed": block.get("name_changed"),
                    "address_changed": block.get("address_changed"),
                }
            )
            continue

        if any(kind == "text" and isinstance(value, str) and value.lower().startswith("title") for kind, value in section["events"]):
            officers.extend(_parse_people(section, heading or "Officers"))
            continue

        if heading_key == "annual reports":
            record["annual_reports"] = _parse_annual_reports(section)
            continue

        section_filings = _parse_filings(section)
        if section_filings:
            filings.extend(section_filings)
            continue

        plain = _clean_text(" ".join(texts))
        if plain and "no images are available" not in plain.lower():
            other.append({"heading": heading or "Section", "text": plain[:2000]})

    if officers:
        record["officers"] = officers
    if filings:
        record["filings"] = filings[:MAX_FILINGS]
        omitted = len(filings) - len(record["filings"])
        if omitted > 0:
            record["filings_omitted"] = omitted
    if other:
        record["other_sections"] = other
    if parser.nav_links.get("View Events"):
        record["events_url"] = parser.nav_links["View Events"]
    if parser.nav_links.get("View Name History"):
        record["name_history_url"] = parser.nav_links["View Name History"]
    return _omit_empty(record)


def parse_history(html: str) -> dict[str, Any]:
    """Parse a Sunbiz name-history or event-history page."""
    parser = _PageParser()
    parser.feed(html)
    # History pages are not detail sections. Fall back to a small table scan
    # implemented by reusing the search-results parser only when a results
    # table exists; otherwise read the raw tables below.
    return _parse_history_tables(html)


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.entity_name = ""
        self._in_bold_p = False
        self._bold_p = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "p" and "bold" in attr.get("class", "").split():
            self._in_bold_p = True
            self._bold_p = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(_clean_text("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None
        elif tag == "p" and self._in_bold_p:
            self._in_bold_p = False

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        elif self._in_bold_p:
            self.entity_name += data


def _parse_history_tables(html: str) -> dict[str, Any]:
    parser = _TableParser()
    parser.feed(html)
    payload: dict[str, Any] = {}
    name = _clean_text(parser.entity_name)
    if name:
        payload["entity_name"] = name
    events: list[dict[str, str]] = []
    for table in parser.tables:
        header = [cell.lower() for cell in table[0]]
        if header and "event type" in header[0]:
            for row in table[1:]:
                events.append(
                    _omit_empty(
                        {
                            "event_type": row[0] if len(row) > 0 else None,
                            "filed_date": row[1] if len(row) > 1 else None,
                            "effective_date": _blank_to_none(row[2] if len(row) > 2 else None),
                            "description": _blank_to_none(row[3] if len(row) > 3 else None),
                        }
                    )
                )
            continue
        if all(len(row) == 2 for row in table):
            for label, value in table:
                key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
                cleaned = _blank_to_none(value)
                if key and cleaned:
                    payload[key] = cleaned
    if events:
        payload["events"] = events
    return payload


class SunbizSession:
    """One headed Chromium tab, serialized so Cloudflare cookies stay warm."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._xvfb: subprocess.Popen[bytes] | None = None

    def _start_display(self) -> None:
        if os.environ.get("DISPLAY"):
            return
        socket_path = "/tmp/.X11-unix/X95"
        if os.path.exists(socket_path):
            os.environ["DISPLAY"] = ":95"
            return
        if not shutil.which("Xvfb"):
            raise RuntimeError("Xvfb is required to load search.sunbiz.org from this host")
        self._xvfb = subprocess.Popen(
            ["Xvfb", ":95", "-screen", "0", "1366x768x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        atexit.register(self._stop_display)
        for _ in range(25):
            if os.path.exists(socket_path):
                os.environ["DISPLAY"] = ":95"
                return
            if self._xvfb.poll() is not None:
                break
            time.sleep(0.1)
        raise RuntimeError("Xvfb failed to start")

    def _stop_display(self) -> None:
        proc = self._xvfb
        self._xvfb = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()

    async def _reset_locked(self) -> None:
        browser = self._browser
        playwright = self._playwright
        self._page = None
        self._browser = None
        self._playwright = None
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass

    async def _ensure_locked(self) -> None:
        if self._page is not None:
            return
        self._start_display()
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await self._browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self._page = await context.new_page()

    async def _page_snapshot(self) -> tuple[str, str, str]:
        """Return title, url, and html, retrying while navigation replaces the document."""
        page = self._page
        last_error: Exception | None = None
        for _ in range(8):
            try:
                title = await page.title()
                html = await page.content()
                return title, page.url, html
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.3)
        raise RuntimeError(f"Sunbiz page was still navigating: {last_error}")

    async def _wait_until(self, predicate, timeout_ms: int | None = None) -> tuple[str, str, str]:
        """Poll until predicate(title, url, html) is true. Survives Cloudflare reloads."""
        deadline = time.monotonic() + ((timeout_ms or PAGE_TIMEOUT_MS) / 1000)
        last_title = ""
        while time.monotonic() < deadline:
            try:
                title, url, html = await self._page_snapshot()
            except Exception:
                await asyncio.sleep(0.4)
                continue
            last_title = title
            if "Just a moment" not in title and predicate(title, url, html):
                return title, url, html
            await asyncio.sleep(0.4)
        if "Just a moment" in last_title:
            raise RuntimeError("Sunbiz Cloudflare challenge did not clear")
        raise RuntimeError("Timed out waiting for Sunbiz page")

    async def _wait_ready(self) -> str:
        _title, _url, html = await self._wait_until(
            lambda _title, _url, html: (
                'id="search-results"' in html
                or "detailSection" in html
                or 'id="SearchTerm"' in html
                or "Document Not Found" in html
                or "validation-summary-errors" in html
            )
        )
        return html

    async def _open(self, url: str) -> tuple[str, int | None, str]:
        page = self._page
        response = await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        html = await self._wait_ready()
        status = response.status if response is not None else None
        return html, status, page.url

    async def fetch(self, url: str) -> tuple[str, int | None, str]:
        if not _is_allowed_sunbiz_url(url):
            raise ValueError("Refusing non-Sunbiz URL")
        async with self._lock:
            last_error: Exception | None = None
            for attempt in (1, 2):
                try:
                    await self._ensure_locked()
                    return await self._open(url)
                except Exception as exc:
                    last_error = exc
                    await self._reset_locked()
                    if attempt == 2:
                        break
            raise RuntimeError(f"Sunbiz request failed: {last_error}")

    async def lookup_document(self, document_number: str) -> tuple[str, int | None, str]:
        async with self._lock:
            last_error: Exception | None = None
            for attempt in (1, 2):
                try:
                    await self._ensure_locked()
                    page = self._page
                    await page.goto(
                        DOCUMENT_URL,
                        wait_until="domcontentloaded",
                        timeout=PAGE_TIMEOUT_MS,
                    )
                    await self._wait_ready()
                    await page.fill("#SearchTerm", document_number)
                    await page.click('input[type="submit"]')
                    _title, final_url, html = await self._wait_until(
                        lambda _title, url, html: (
                            "detailSection" in html
                            or "Document Not Found" in html
                            or (
                                "SearchResultDetail" in url
                                and "Just a moment" not in html
                            )
                        )
                    )
                    return html, 200, final_url
                except Exception as exc:
                    last_error = exc
                    await self._reset_locked()
                    if attempt == 2:
                        break
            raise RuntimeError(f"Sunbiz document lookup failed: {last_error}")


_session = SunbizSession()


def _search_term_warning(original: str, used: str) -> str | None:
    if original != used:
        return f"Search term was truncated to {MAX_TERM_LENGTH} characters."
    return None


async def _load_history(url: str) -> dict[str, Any]:
    if not _is_allowed_sunbiz_url(url):
        return {"error": "Refusing non-Sunbiz history URL", "url": url}
    try:
        html, status, final_url = await _session.fetch(url)
    except Exception as exc:
        return {"error": str(exc), "url": url}
    if status and status >= 400:
        return {"error": f"HTTP {status}", "url": final_url}
    history = parse_history(html)
    history["url"] = final_url
    return history


@mcp.tool()
async def search_sunbiz(
    inquiry_type: InquiryType = "entity_name",
    search_term: str | None = None,
    page_url: str | None = None,
) -> dict[str, Any]:
    """Search Florida Sunbiz corporation, trademark, officer, FEI, address, or ZIP records.

    inquiry_type:
      entity_name, officer_or_registered_agent, registered_agent, fei_ein,
      trademark, trademark_owner, street_address, zip_code.
    Results are the next alphabetical page of about 20 records. Use
    next_page_url or previous_page_url as page_url to move through the index.
    Then call get_sunbiz_record with a result detail_url or document_number.
    """
    warning = None
    if page_url:
        try:
            url = _normalize_sunbiz_url(page_url)
        except ValueError as exc:
            return {"error": str(exc)}
        query = parse_qs(urlparse(url).query)
        if not search_term:
            search_term = (query.get("searchTerm") or query.get("SearchTerm") or [""])[0]
    else:
        term = _clean_text(search_term or "")
        if not term:
            return {"error": "search_term is required unless page_url is set"}
        used = term[:MAX_TERM_LENGTH]
        warning = _search_term_warning(term, used)
        search_term = used
        inquiry = _INQUIRY_PARAM.get(inquiry_type)
        if not inquiry:
            return {"error": f"Unknown inquiry_type: {inquiry_type}"}
        url = f"{SEARCH_URL}?{urlencode({'inquiryType': inquiry, 'searchTerm': used})}"

    try:
        html, status, final_url = await _session.fetch(url)
    except Exception as exc:
        return {"error": str(exc)}

    if status == 500:
        return {"error": "Sunbiz returned HTTP 500 for this search", "source_url": final_url}
    if "Just a moment" in html:
        return {"error": "Sunbiz Cloudflare challenge did not clear", "source_url": final_url}

    payload = parse_search_results(html, final_url, search_term or "")
    payload["inquiry_type"] = inquiry_type
    if warning:
        payload["warning"] = warning
    return payload


@mcp.tool()
async def get_sunbiz_record(
    document_number: str | None = None,
    detail_url: str | None = None,
    include_name_history: bool = True,
    include_events: bool = False,
) -> dict[str, Any]:
    """Get one Florida Sunbiz filing: status, addresses, agent, officers, and reports.

    Pass document_number (for example L02000021464) or a detail_url returned
    by search_sunbiz. This is read-only public record data.
    """
    number = _clean_text(document_number or "")
    url = _clean_text(detail_url or "")
    if not number and not url:
        return {"error": "document_number or detail_url is required"}

    try:
        if url:
            target = _normalize_sunbiz_url(url)
            html, status, final_url = await _session.fetch(target)
        else:
            html, status, final_url = await _session.lookup_document(number)
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": str(exc)}

    if status == 500:
        return {"error": "Sunbiz returned HTTP 500", "document_number": number or None, "source_url": final_url}

    record = parse_detail(html, final_url)
    if record.get("error"):
        if number:
            record["document_number"] = number
        return _omit_empty(record)

    if include_name_history and record.get("name_history_url"):
        record["name_history"] = await _load_history(record["name_history_url"])
    if include_events and record.get("events_url"):
        record["events"] = await _load_history(record["events_url"])
    return record
