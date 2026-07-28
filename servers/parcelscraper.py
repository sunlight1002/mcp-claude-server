"""Parcel scraper MCP server tools.

Proxies the prospecting automation HTTP API. Blocking tools wait for jobs,
download Supabase result files when needed, and return parsed parcel records.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Literal, Optional

from mcp.server.fastmcp import Context

from .shared import create_fastmcp, fetch_json_text, http_request, http_request_async

mcp = create_fastmcp("parcelscraper")

API_URL = (
    os.getenv("PARCELSCRAPER_API_URL")
    or "https://automation.lee-associates-southflorida.com"
).rstrip("/")
REQUEST_TIMEOUT = float(os.getenv("PARCELSCRAPER_TIMEOUT", "120"))
POLL_INTERVAL = float(os.getenv("PARCELSCRAPER_POLL_INTERVAL", "5"))
MAX_WAIT = float(os.getenv("PARCELSCRAPER_MAX_WAIT", "1800"))
MAX_RESULT_BYTES = int(os.getenv("PARCELSCRAPER_MAX_RESULT_BYTES", "120000"))

DetailLevel = Literal["auto", "full", "summary"]


def _is_error_envelope(payload: dict[str, Any]) -> bool:
    """True for MCP/HTTP error envelopes, not automation API bodies with error: null."""
    return bool(payload.get("error")) and not payload.get("success")


def _proxy(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return http_request(
        method,
        f"{API_URL}{path}",
        json_body=json_body,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )


async def _proxy_async(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await http_request_async(
        method,
        f"{API_URL}{path}",
        json_body=json_body,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )


def _extract_results_list(payload: Any) -> list[Any] | None:
    """Return a non-empty list of scrape records from various payload shapes."""
    if isinstance(payload, list) and payload:
        return payload
    if not isinstance(payload, dict):
        return None

    for key in ("results", "data"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            return value
    return None


def _summarize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Compact one parcel scrape record for Claude context limits."""
    property_info = record.get("propertyInfo") or {}
    if not isinstance(property_info, dict):
        property_info = {}

    owner_infos = property_info.get("owner_infos") or []
    if not isinstance(owner_infos, list):
        owner_infos = []

    name_search = record.get("nameSearchResult")
    owner_contacts: list[dict[str, Any]] = []
    if isinstance(name_search, dict):
        owner_contacts.append(
            {
                "full_name": name_search.get("full_name"),
                "age": name_search.get("age"),
                "phone_numbers": name_search.get("phone_numbers"),
                "email": name_search.get("email"),
            }
        )
    elif isinstance(name_search, list):
        for item in name_search:
            if isinstance(item, dict):
                owner_contacts.append(
                    {
                        "full_name": item.get("full_name"),
                        "age": item.get("age"),
                        "phone_numbers": item.get("phone_numbers"),
                        "email": item.get("email"),
                    }
                )

    company_owner = record.get("company_owner_name") or {}
    if not isinstance(company_owner, dict):
        company_owner = {}

    businesses = record.get("businessSearchResult") or []
    if not isinstance(businesses, list):
        businesses = []

    dade = record.get("dade")
    summary: dict[str, Any] = {
        "parcel_id": property_info.get("parcelID"),
        "physical_address": property_info.get("physical_address"),
        "mailing_address": property_info.get("mailing_address"),
        "year_built": property_info.get("year_built"),
        "lot_size": property_info.get("lot_size"),
        "adjusted_area": property_info.get("adjusted_area"),
        "last_sale_date": property_info.get("last_sale_date"),
        "last_sale_price": property_info.get("last_sale_price"),
        "owner_names": owner_infos,
        "original_owner_name": property_info.get("original_owner_name"),
        "owner_contacts": owner_contacts,
        "owner_occupied": record.get("owner_occupied"),
        "businesses_at_property": businesses,
        "sunbiz_link": company_owner.get("sunbiz_link"),
        "company_officers": company_owner.get("owners_information"),
    }

    if isinstance(dade, dict):
        summary["dade_record"] = dade

    return {key: value for key, value in summary.items() if value not in (None, "", [], {})}


def _shape(results: list[Any], detail: DetailLevel) -> tuple[list[Any], str, bool]:
    """Return shaped results, the detail level used, and whether output was truncated."""
    if detail == "summary":
        return [_summarize_record(item) for item in results if isinstance(item, dict)], "summary", False

    if detail == "full":
        return results, "full", False

    serialized = json.dumps(results, default=str)
    if len(serialized.encode("utf-8")) <= MAX_RESULT_BYTES:
        return results, "full", False

    summarized = [_summarize_record(item) for item in results if isinstance(item, dict)]
    return summarized, "summary", True


async def _resolve_results(
    status: dict[str, Any],
    cached_data: list[Any] | None,
) -> tuple[list[Any] | None, str | None]:
    """Resolve scrape records from status.data, Supabase file, or cached partial data."""
    data = _extract_results_list(status.get("data"))
    if data:
        return data, "status.data"

    file_url = status.get("fileUrl") or status.get("file_url")
    if isinstance(file_url, str) and file_url.strip():
        file_payload = await fetch_json_text(file_url.strip(), timeout=REQUEST_TIMEOUT)
        if _is_error_envelope(file_payload):
            if cached_data:
                return cached_data, "cached_partial_after_file_error"
            return None, file_payload.get("error")

        file_results = _extract_results_list(file_payload)
        if file_results:
            return file_results, "supabase_file"

    if cached_data:
        return cached_data, "cached_partial"

    return None, None


def _build_response(
    *,
    job_id: str | None,
    status: str,
    results: list[Any] | None,
    detail_used: str,
    truncated: bool,
    message: str | None = None,
    file_url: str | None = None,
    file_name: str | None = None,
    progress: int | None = None,
    resolution_source: str | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "job_id": job_id,
        "status": status,
        "parcel_count": len(results) if results else 0,
        "results": results or [],
        "detail": detail_used,
        "truncated": truncated,
    }
    if message:
        response["message"] = message
    if file_url:
        response["file_url"] = file_url
    if file_name:
        response["file_name"] = file_name
    if progress is not None:
        response["progress"] = progress
    if resolution_source:
        response["resolution_source"] = resolution_source
    if error:
        response["error"] = error
    if extra:
        response.update(extra)
    return response


async def _wait_for_job(
    job_id: str,
    ctx: Context | None,
    *,
    max_wait_seconds: float | None = None,
) -> dict[str, Any]:
    """Poll job status until complete, error, or timeout."""
    deadline = time.monotonic() + (max_wait_seconds or MAX_WAIT)
    cached_data: list[Any] | None = None
    last_status: dict[str, Any] = {}

    while time.monotonic() < deadline:
        status_payload = await _proxy_async("GET", f"/scraper/status/{job_id}")
        if _is_error_envelope(status_payload):
            return status_payload

        last_status = status_payload
        progress = int(status_payload.get("progress") or 0)
        message = status_payload.get("message") or "Processing scrape job..."
        if ctx is not None:
            await ctx.report_progress(progress, 100, message)

        partial = _extract_results_list(status_payload.get("data"))
        if partial:
            cached_data = partial

        if status_payload.get("isError"):
            return _build_response(
                job_id=job_id,
                status="error",
                results=cached_data,
                detail_used="full",
                truncated=False,
                message=message,
                progress=progress,
                error=status_payload.get("error") or "Scrape job failed",
                file_url=status_payload.get("fileUrl"),
                file_name=status_payload.get("fileName"),
            )

        if status_payload.get("isComplete"):
            return {
                "completed": True,
                "status_payload": status_payload,
                "cached_data": cached_data,
            }

        await asyncio.sleep(POLL_INTERVAL)

    return _build_response(
        job_id=job_id,
        status="timeout",
        results=cached_data,
        detail_used="full",
        truncated=False,
        message=(
            "Scrape job did not finish within the wait window. "
            "Call get_scrape_results with this job_id to retry resolving results."
        ),
        progress=int(last_status.get("progress") or 0),
        file_url=last_status.get("fileUrl"),
        file_name=last_status.get("fileName"),
    )


async def _finalize_job_response(
    wait_result: dict[str, Any],
    *,
    detail: DetailLevel,
) -> dict[str, Any]:
    """Turn a completed wait result into a shaped response for Claude."""
    if wait_result.get("completed"):
        status_payload = wait_result["status_payload"]
        cached_data = wait_result.get("cached_data")
        results, source = await _resolve_results(status_payload, cached_data)
        if not results:
            return _build_response(
                job_id=status_payload.get("jobId"),
                status="completed",
                results=[],
                detail_used="full",
                truncated=False,
                message=status_payload.get("message"),
                file_url=status_payload.get("fileUrl"),
                file_name=status_payload.get("fileName"),
                progress=int(status_payload.get("progress") or 100),
                error=source or "No scrape results could be resolved",
            )

        shaped, detail_used, truncated = _shape(results, detail)
        return _build_response(
            job_id=status_payload.get("jobId"),
            status="completed",
            results=shaped,
            detail_used=detail_used,
            truncated=truncated,
            message=status_payload.get("message"),
            file_url=status_payload.get("fileUrl"),
            file_name=status_payload.get("fileName"),
            progress=int(status_payload.get("progress") or 100),
            resolution_source=source,
        )

    return wait_result


@mcp.tool()
async def scrape_parcels(
    parcel_ids: list[str],
    detail: DetailLevel = "auto",
    max_wait_seconds: Optional[float] = None,
    ctx: Context = None,
) -> dict[str, Any]:
    """Scrape one or more parcel/folio IDs and return the finished parcel records.

    Preferred tool for Claude. Starts the scrape, waits with progress updates,
    downloads the Supabase result file when needed, and returns parsed records.
    Present the records directly to the user; only mention file_url if they ask
    for the uploaded file.
    """
    if not parcel_ids:
        return {"error": "parcel_ids must be a non-empty list"}

    start_payload = await _proxy_async(
        "POST",
        "/scraper",
        json_body={"parcelIds": parcel_ids},
    )
    if _is_error_envelope(start_payload):
        return start_payload

    job_id = start_payload.get("jobId")
    if not job_id:
        return {"error": "Scrape job did not return a jobId", "details": start_payload}

    wait_result = await _wait_for_job(job_id, ctx, max_wait_seconds=max_wait_seconds)
    return await _finalize_job_response(wait_result, detail=detail)


@mcp.tool()
async def scrape_dade_records(
    date_from: str,
    date_to: str,
    document_type: str,
    detail: DetailLevel = "auto",
    max_wait_seconds: Optional[float] = None,
    ctx: Context = None,
) -> dict[str, Any]:
    """Scrape Miami-Dade Clerk records and return finished enrichment records.

    Preferred tool for Claude. Waits for the job, resolves Supabase file results
    when needed, and returns parsed records instead of a file link.
    """
    start_payload = await _proxy_async(
        "POST",
        "/scraper-dade",
        json_body={
            "dateFromRaw": date_from,
            "dateToRaw": date_to,
            "documentType": document_type,
        },
    )
    if _is_error_envelope(start_payload):
        return start_payload

    job_id = start_payload.get("jobId")
    if not job_id:
        return {"error": "Dade scrape job did not return a jobId", "details": start_payload}

    wait_result = await _wait_for_job(job_id, ctx, max_wait_seconds=max_wait_seconds)
    return await _finalize_job_response(wait_result, detail=detail)


@mcp.tool()
async def get_scrape_results(
    job_id: Optional[str] = None,
    file_url: Optional[str] = None,
    detail: DetailLevel = "auto",
) -> dict[str, Any]:
    """Resolve scrape records for a finished job or directly from a Supabase file URL."""
    if file_url and file_url.strip():
        file_payload = await fetch_json_text(file_url.strip(), timeout=REQUEST_TIMEOUT)
        if _is_error_envelope(file_payload):
            return file_payload
        results = _extract_results_list(file_payload)
        if not results:
            return {"error": "No records found in file", "details": file_payload}
        shaped, detail_used, truncated = _shape(results, detail)
        return _build_response(
            job_id=job_id,
            status="completed",
            results=shaped,
            detail_used=detail_used,
            truncated=truncated,
            file_url=file_url.strip(),
            resolution_source="supabase_file",
        )

    if not job_id or not job_id.strip():
        return {"error": "Provide job_id and/or file_url"}

    status_payload = await _proxy_async("GET", f"/scraper/status/{job_id.strip()}")
    if _is_error_envelope(status_payload):
        return status_payload

    results, source = await _resolve_results(status_payload, None)
    if not results:
        return _build_response(
            job_id=status_payload.get("jobId") or job_id.strip(),
            status=str(status_payload.get("status") or "unknown"),
            results=[],
            detail_used="full",
            truncated=False,
            message=status_payload.get("message"),
            file_url=status_payload.get("fileUrl"),
            file_name=status_payload.get("fileName"),
            progress=int(status_payload.get("progress") or 0),
            error=source or "No results available yet",
        )

    shaped, detail_used, truncated = _shape(results, detail)
    return _build_response(
        job_id=status_payload.get("jobId") or job_id.strip(),
        status=str(status_payload.get("status") or "completed"),
        results=shaped,
        detail_used=detail_used,
        truncated=truncated,
        message=status_payload.get("message"),
        file_url=status_payload.get("fileUrl"),
        file_name=status_payload.get("fileName"),
        progress=int(status_payload.get("progress") or 0),
        resolution_source=source,
    )


@mcp.tool()
async def get_scrape_status(
    job_id: str,
    detail: DetailLevel = "auto",
) -> dict[str, Any]:
    """Get scrape job status. When complete, returns parsed records instead of only a file link."""
    if not job_id.strip():
        return {"error": "job_id is required"}

    status_payload = await _proxy_async("GET", f"/scraper/status/{job_id.strip()}")
    if _is_error_envelope(status_payload):
        return status_payload

    if not status_payload.get("isComplete"):
        return {
            "job_id": status_payload.get("jobId") or job_id.strip(),
            "status": status_payload.get("status"),
            "is_complete": False,
            "is_error": bool(status_payload.get("isError")),
            "progress": status_payload.get("progress"),
            "message": status_payload.get("message"),
            "error": status_payload.get("error"),
            "partial_results": status_payload.get("data") or [],
        }

    results, source = await _resolve_results(status_payload, None)
    if not results:
        return _build_response(
            job_id=status_payload.get("jobId") or job_id.strip(),
            status="completed",
            results=[],
            detail_used="full",
            truncated=False,
            message=status_payload.get("message"),
            file_url=status_payload.get("fileUrl"),
            file_name=status_payload.get("fileName"),
            progress=int(status_payload.get("progress") or 100),
            error=source or "No results could be resolved",
        )

    shaped, detail_used, truncated = _shape(results, detail)
    return _build_response(
        job_id=status_payload.get("jobId") or job_id.strip(),
        status="completed",
        results=shaped,
        detail_used=detail_used,
        truncated=truncated,
        message=status_payload.get("message"),
        file_url=status_payload.get("fileUrl"),
        file_name=status_payload.get("fileName"),
        progress=int(status_payload.get("progress") or 100),
        resolution_source=source,
    )


@mcp.tool()
def start_parcel_scrape(parcel_ids: list[str]) -> dict[str, Any]:
    """Start an async parcel scrape job without waiting.

    Prefer scrape_parcels for Claude so results are returned automatically.
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
    """Start an async Miami-Dade Clerk scrape job without waiting.

    Prefer scrape_dade_records for Claude so results are returned automatically.
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
def get_lee_associates_link(address: str) -> dict[str, Any]:
    """Look up Lee Associates South Florida listing and PDF links for an address."""
    if not address.strip():
        return {"error": "address is required"}
    return _proxy("GET", "/get-less-associates", params={"address": address})
