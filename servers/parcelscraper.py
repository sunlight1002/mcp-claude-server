"""Parcel scraper MCP server tools.

Proxies the prospecting automation HTTP API.
"""

from __future__ import annotations

import os
from typing import Any

from .shared import create_fastmcp, http_request

mcp = create_fastmcp("parcelscraper")

API_URL = (
    os.getenv("PARCELSCRAPER_API_URL")
    or "https://automation.lee-associates-southflorida.com"
).rstrip("/")
REQUEST_TIMEOUT = float(os.getenv("PARCELSCRAPER_TIMEOUT", "600"))


def _proxy(method: str, path: str, *, json_body: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return http_request(
        method,
        f"{API_URL}{path}",
        json_body=json_body,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )


@mcp.tool()
def start_parcel_scrape(parcel_ids: list[str]) -> dict[str, Any]:
    """Start an async parcel scrape job for one or more parcel/folio IDs.

    Supports Miami-Dade, Broward, and Palm Beach parcel ID formats.
    Returns a jobId; poll with get_scrape_status until complete.
    """
    if not parcel_ids:
        return {"error": "parcel_ids must be a non-empty list"}
    return _proxy("POST", "/scraper", json_body={"parcelIds": parcel_ids})


@mcp.tool()
def start_dade_scrape(
    date_from: str,
    date_to: str,
    document_type: str,
) -> dict[str, Any]:
    """Start an async Miami-Dade Clerk records scrape job.

    Args:
        date_from: Start date, e.g. "01/01/2025".
        date_to: End date, e.g. "01/31/2025".
        document_type: Document type, e.g. "DISSOLUTION OF MARRIAGE - DOM".
    """
    return _proxy(
        "POST",
        "/scraper-dade",
        json_body={
            "dateFromRaw": date_from,
            "dateToRaw": date_to,
            "documentType": document_type,
        },
    )


@mcp.tool()
def get_scrape_status(job_id: str) -> dict[str, Any]:
    """Poll the status of a parcel or Dade scrape job by jobId."""
    if not job_id.strip():
        return {"error": "job_id is required"}
    return _proxy("GET", f"/scraper/status/{job_id}")


@mcp.tool()
def get_lee_associates_link(address: str) -> dict[str, Any]:
    """Look up Lee Associates South Florida listing and PDF links for an address."""
    if not address.strip():
        return {"error": "address is required"}
    return _proxy("GET", "/get-less-associates", params={"address": address})
