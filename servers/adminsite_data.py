"""Additional admin-portal data tools: people, leads, team, NNN, news, scrapers, call sheet.

Imported by adminsite.py so tools register on the same FastMCP instance.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .adminsite import (
    _clean,
    _get_supabase,
    _proxy,
    _resolve_property,
    mcp,
)
from .shared import http_request_async

CALL_SHEET_API_URL = (
    os.getenv("CALL_SHEET_API_URL")
    or os.getenv("NEXT_PUBLIC_CALL_SHEET_API_URL")
    or "https://api.lee-associates-southflorida.com"
).rstrip("/")


def _supabase_required() -> Any:
    client = _get_supabase()
    if client is None:
        return None
    return client


def _table_search(
    table: str,
    *,
    select: str = "*",
    query: str | None = None,
    or_fields: list[str] | None = None,
    eq_filters: dict[str, Any] | None = None,
    order_by: str = "created_at",
    descending: bool = True,
    limit: int = 25,
) -> dict[str, Any]:
    client = _supabase_required()
    if client is None:
        return {
            "error": (
                "Missing Supabase credentials. Set ADMINSITE_SUPABASE_URL and "
                "ADMINSITE_SUPABASE_SERVICE_ROLE_KEY in the MCP server .env."
            )
        }
    try:
        builder = client.table(table).select(select)
        if query and query.strip() and or_fields:
            term = query.strip().replace(",", " ").replace("%", "")
            clauses = ",".join(f"{field}.ilike.%{term}%" for field in or_fields)
            builder = builder.or_(clauses)
        if eq_filters:
            for key, value in eq_filters.items():
                if value is not None and value != "":
                    builder = builder.eq(key, value)
        response = (
            builder.order(order_by, desc=descending)
            .limit(max(1, min(limit, 100)))
            .execute()
        )
        rows = response.data or []
        return {"count": len(rows), "rows": rows}
    except Exception as exc:
        return {"error": f"{table} search failed: {exc}"}


# --- People / contacts -------------------------------------------------------

@mcp.tool()
def search_contacts(
    query: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Search people/contacts across the admin portal database.

    Searches customer leads (name/email/phone), team members, portal users, and
    career applicants. Use this for people or contact lookups in the admin portal.
    """
    if not query.strip():
        return {"error": "query is required"}

    leads = _table_search(
        "customer_requests",
        select="id, name, email, phone, status, property_id, message, created_at",
        query=query,
        or_fields=["name", "email", "phone"],
        limit=limit,
    )
    team = _table_search(
        "team_members",
        select="id, display_name, email, phone, role, specialization, linkedin, sort_order",
        query=query,
        or_fields=["display_name", "email", "phone", "role", "specialization"],
        order_by="sort_order",
        descending=False,
        limit=limit,
    )
    users = _table_search(
        "users",
        select="id, email, first_name, last_name, phone_number, is_active, is_admin, created_at",
        query=query,
        or_fields=["email", "first_name", "last_name", "phone_number"],
        limit=limit,
    )
    careers = _table_search(
        "career_inquiries",
        select="id, full_name, email, phone, has_real_estate_license, created_at",
        query=query,
        or_fields=["full_name", "email", "phone"],
        limit=limit,
    )

    contacts: list[dict[str, Any]] = []
    for row in leads.get("rows") or []:
        contacts.append(
            {
                "source": "customer_request",
                "id": row.get("id"),
                "name": row.get("name"),
                "email": row.get("email"),
                "phone": row.get("phone"),
                "status": row.get("status"),
                "property_id": row.get("property_id"),
                "message": row.get("message"),
            }
        )
    for row in team.get("rows") or []:
        contacts.append(
            {
                "source": "team_member",
                "id": row.get("id"),
                "name": row.get("display_name"),
                "email": row.get("email"),
                "phone": row.get("phone"),
                "role": row.get("role"),
                "specialization": row.get("specialization"),
                "linkedin": row.get("linkedin"),
            }
        )
    for row in users.get("rows") or []:
        name = " ".join(
            part for part in [row.get("first_name"), row.get("last_name")] if part
        ).strip()
        contacts.append(
            {
                "source": "user",
                "id": row.get("id"),
                "name": name or None,
                "email": row.get("email"),
                "phone": row.get("phone_number"),
                "is_admin": row.get("is_admin"),
                "is_active": row.get("is_active"),
            }
        )
    for row in careers.get("rows") or []:
        contacts.append(
            {
                "source": "career_inquiry",
                "id": row.get("id"),
                "name": row.get("full_name"),
                "email": row.get("email"),
                "phone": row.get("phone"),
            }
        )

    return {
        "query": query,
        "count": len(contacts),
        "contacts": contacts[: max(1, min(limit * 2, 100))],
        "breakdown": {
            "customer_requests": leads.get("count", 0) if "error" not in leads else 0,
            "team_members": team.get("count", 0) if "error" not in team else 0,
            "users": users.get("count", 0) if "error" not in users else 0,
            "career_inquiries": careers.get("count", 0) if "error" not in careers else 0,
        },
    }


@mcp.tool()
def search_customer_requests(
    query: Optional[str] = None,
    status: Optional[str] = None,
    property_id: Optional[str] = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Search customer/lead requests (name, email, phone, message, NDA status).

    These are people who requested property info from the public site.
    """
    result = _table_search(
        "customer_requests",
        select="id, name, email, phone, status, property_id, message, nda_pdf_url, created_at",
        query=query,
        or_fields=["name", "email", "phone", "message"],
        eq_filters=_clean({"status": status, "property_id": property_id}),
        limit=limit,
    )
    if "error" in result:
        return result
    return {"count": result["count"], "customer_requests": result["rows"]}


@mcp.tool()
def get_customer_request(request_id: int) -> dict[str, Any]:
    """Get one customer/lead request by id."""
    client = _supabase_required()
    if client is None:
        return {"error": "Missing Supabase credentials"}
    try:
        response = (
            client.table("customer_requests")
            .select("*")
            .eq("id", request_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return {"error": "Customer request not found", "id": request_id}
        return {"customer_request": rows[0]}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def search_team_members(
    query: Optional[str] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Search broker/team member contacts (name, email, phone, role, LinkedIn)."""
    result = _table_search(
        "team_members",
        select=(
            "id, display_name, email, phone, role, specialization, linkedin, "
            "photo, biography, sort_order, user_id, address1, address2"
        ),
        query=query,
        or_fields=["display_name", "email", "phone", "role", "specialization"],
        order_by="sort_order",
        descending=False,
        limit=limit,
    )
    if "error" in result:
        return result
    return {"count": result["count"], "team_members": result["rows"]}


@mcp.tool()
def get_team_member(team_member_id: int) -> dict[str, Any]:
    """Get one team member profile by id, including bio and transactions."""
    client = _supabase_required()
    if client is None:
        return {"error": "Missing Supabase credentials"}
    try:
        response = (
            client.table("team_members")
            .select("*")
            .eq("id", team_member_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return {"error": "Team member not found", "id": team_member_id}
        return {"team_member": rows[0]}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def search_users(
    query: Optional[str] = None,
    is_admin: Optional[bool] = None,
    is_active: Optional[bool] = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Search admin portal user accounts (email, name, phone, admin/active flags)."""
    result = _table_search(
        "users",
        select=(
            "id, email, first_name, last_name, phone_number, is_active, is_admin, "
            "avatar, created_at, last_ip, ip_location"
        ),
        query=query,
        or_fields=["email", "first_name", "last_name", "phone_number"],
        eq_filters=_clean({"is_admin": is_admin, "is_active": is_active}),
        limit=limit,
    )
    if "error" in result:
        return result
    return {"count": result["count"], "users": result["rows"]}


@mcp.tool()
def get_user(user_id: str) -> dict[str, Any]:
    """Get one portal user by UUID."""
    if not user_id.strip():
        return {"error": "user_id is required"}
    client = _supabase_required()
    if client is None:
        return {"error": "Missing Supabase credentials"}
    try:
        response = (
            client.table("users")
            .select("*")
            .eq("id", user_id.strip())
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return {"error": "User not found", "id": user_id}
        return {"user": rows[0]}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def search_career_inquiries(
    query: Optional[str] = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Search career applicants (full name, email, phone, licenses)."""
    result = _table_search(
        "career_inquiries",
        select=(
            "id, full_name, email, phone, has_real_estate_license, "
            "has_drivers_license, sales_experience_years, comfortable_onsite, "
            "cv_storage_path, created_at"
        ),
        query=query,
        or_fields=["full_name", "email", "phone"],
        limit=limit,
    )
    if "error" in result:
        return result
    return {"count": result["count"], "career_inquiries": result["rows"]}


# --- Triple net / news / tags / dashboard ------------------------------------

@mcp.tool()
def search_public_properties(
    query: Optional[str] = None,
    lease_type: Optional[str] = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Search public/triple-net lease listings from the admin portal."""
    result = _table_search(
        "public_properties",
        select=(
            "id, name, address, tenant, lease_type, lease_term_remaining, "
            "asking_cap_rate, annual_rental_income, monthly_rental_income, "
            "price, detail_link, image_link, created_at"
        ),
        query=query,
        or_fields=["name", "address", "tenant", "lease_type"],
        eq_filters=_clean({"lease_type": lease_type}),
        limit=limit,
    )
    if "error" in result:
        return result
    return {"count": result["count"], "public_properties": result["rows"]}


@mcp.tool()
def get_public_property(public_property_id: int) -> dict[str, Any]:
    """Get one public/triple-net listing by id."""
    client = _supabase_required()
    if client is None:
        return {"error": "Missing Supabase credentials"}
    try:
        response = (
            client.table("public_properties")
            .select("*")
            .eq("id", public_property_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return {"error": "Public property not found", "id": public_property_id}
        return {"public_property": rows[0]}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def search_news(
    query: Optional[str] = None,
    is_active: Optional[bool] = True,
    limit: int = 25,
) -> dict[str, Any]:
    """Search admin news/press items."""
    result = _table_search(
        "news",
        select="id, title, type, short_summary, content, news_photo, team_member_id, is_active, created_at",
        query=query,
        or_fields=["title", "type", "short_summary", "content"],
        eq_filters=_clean({"is_active": is_active}),
        limit=limit,
    )
    if "error" in result:
        return result
    return {"count": result["count"], "news": result["rows"]}


@mcp.tool()
def list_prospect_tags() -> dict[str, Any]:
    """List Active Prospect tags (Contact Made, No Interest, etc.)."""
    client = _supabase_required()
    if client is None:
        return {"error": "Missing Supabase credentials"}
    try:
        response = (
            client.table("prospect_tags")
            .select("*")
            .order("sort_order")
            .execute()
        )
        rows = response.data or []
        return {"count": len(rows), "prospect_tags": rows}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def list_prospect_folders() -> dict[str, Any]:
    """List Active Prospect folders."""
    client = _supabase_required()
    if client is None:
        return {"error": "Missing Supabase credentials"}
    try:
        response = (
            client.table("prospect_folders")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        rows = response.data or []
        return {"count": len(rows), "prospect_folders": rows}
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def get_dashboard_stats() -> dict[str, Any]:
    """Get high-level admin dashboard counts from the portal database."""
    client = _supabase_required()
    if client is None:
        return {"error": "Missing Supabase credentials"}
    try:
        def _count(table: str, **eq: Any) -> int:
            builder = client.table(table).select("id", count="exact")
            for key, value in eq.items():
                builder = builder.eq(key, value)
            response = builder.limit(1).execute()
            return int(response.count or 0)

        return {
            "properties_total": _count("properties"),
            "properties_active_prospect": _count("properties", is_active_prospect=True),
            "properties_offmarket": _count("properties", is_offmarket=True),
            "customer_requests_total": _count("customer_requests"),
            "customer_requests_pending": _count("customer_requests", status="pending"),
            "users_total": _count("users"),
            "team_members_total": _count("team_members"),
            "public_properties_total": _count("public_properties"),
            "news_total": _count("news"),
            "career_inquiries_total": _count("career_inquiries"),
        }
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool()
def get_scraper_settings() -> dict[str, Any]:
    """Get parcel scraper scheduler settings from the admin database."""
    client = _supabase_required()
    if client is None:
        return {"error": "Missing Supabase credentials"}
    try:
        response = client.table("scraper_settings").select("*").limit(5).execute()
        return {"scraper_settings": response.data or []}
    except Exception as exc:
        return {"error": str(exc)}


# --- Admin API: chat + scraper file listing ----------------------------------

@mcp.tool()
async def ask_property(
    question: str,
    property_id: Optional[str] = None,
    address: Optional[str] = None,
) -> dict[str, Any]:
    """Ask a question about a property using the admin Ask AI endpoint.

    Uses /api/active-prospect-chat over that property's indexed documents.
    """
    if not question.strip():
        return {"error": "question is required"}
    prop = _resolve_property(property_id=property_id, address=address)
    resolved_id = (prop or {}).get("id") or property_id
    if not resolved_id:
        return {
            "error": "property_id or a resolvable address is required",
            "hint": "Call search_properties first.",
        }
    result = await _proxy(
        "POST",
        "/api/active-prospect-chat",
        json_body={
            "propertyId": str(resolved_id),
            "messages": [{"role": "user", "content": question}],
        },
    )
    if prop and isinstance(result, dict):
        result = {
            **result,
            "property_id": str(resolved_id),
            "property_address": prop.get("address"),
        }
    return result


@mcp.tool()
async def list_scraper_files(page: int = 1, limit: int = 10) -> dict[str, Any]:
    """List parcel scraper result files from the admin portal storage API."""
    return await _proxy(
        "GET",
        "/api/scraper/list-files",
        params={"page": page, "limit": limit},
    )


@mcp.tool()
async def list_dade_files(page: int = 1, limit: int = 10) -> dict[str, Any]:
    """List Miami-Dade scraper result files from the admin portal storage API."""
    return await _proxy(
        "GET",
        "/api/scraper/list-dade-files",
        params={"page": page, "limit": limit},
    )


@mcp.tool()
async def get_admin_scraper_status(job_id: str) -> dict[str, Any]:
    """Poll a parcel scrape job via the admin portal /api/scraper/status proxy."""
    if not job_id.strip():
        return {"error": "job_id is required"}
    return await _proxy("POST", "/api/scraper/status", json_body={"jobId": job_id})


@mcp.tool()
async def get_admin_dade_status(job_id: str) -> dict[str, Any]:
    """Poll a Miami-Dade scrape job via the admin portal /api/scraper/dade-status proxy."""
    if not job_id.strip():
        return {"error": "job_id is required"}
    return await _proxy("POST", "/api/scraper/dade-status", json_body={"jobId": job_id})


@mcp.tool()
async def start_admin_parcel_scrape(parcel_ids: list[str]) -> dict[str, Any]:
    """Start a parcel scrape through the admin portal /api/scraper/process-parcels proxy."""
    if not parcel_ids:
        return {"error": "parcel_ids must be a non-empty list"}
    return await _proxy(
        "POST",
        "/api/scraper/process-parcels",
        json_body={"parcelIds": parcel_ids},
    )


@mcp.tool()
async def start_admin_dade_scrape(
    date_from: str,
    date_to: str,
    document_type: str,
) -> dict[str, Any]:
    """Start a Miami-Dade scrape through the admin portal /api/scraper/process-dade proxy."""
    return await _proxy(
        "POST",
        "/api/scraper/process-dade",
        json_body={
            "documentType": document_type,
            "dateFromRaw": date_from,
            "dateToRaw": date_to,
        },
    )


# --- Call sheet API ----------------------------------------------------------

@mcp.tool()
async def list_call_sheet_jobs() -> dict[str, Any]:
    """List call-sheet converter jobs from the call sheet API used by the admin portal."""
    return await http_request_async(
        "GET",
        f"{CALL_SHEET_API_URL}/api/jobs",
        timeout=60.0,
    )


@mcp.tool()
async def get_call_sheet_download(filename: str) -> dict[str, Any]:
    """Get call-sheet download metadata/URL for a finished job filename."""
    if not filename.strip():
        return {"error": "filename is required"}
    return await http_request_async(
        "GET",
        f"{CALL_SHEET_API_URL}/api/download/{filename.strip()}",
        timeout=60.0,
    )
