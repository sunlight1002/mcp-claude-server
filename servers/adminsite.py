"""Admin site MCP server tools.

Proxies florida_associate_admin Next.js /api routes and resolves properties
from the same Supabase DB the admin UI uses, so Claude can look up UUIDs and
call APIs with the same hydrated fields the UI sends.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from mcp.server.fastmcp import Context

from .shared import create_fastmcp, http_request_async

ADMINSITE_INSTRUCTIONS = """
You are connected to the Lee Associates South Florida admin portal.

This MCP server exposes the FULL admin portal — not properties only:
- People/contacts: search_contacts, search_customer_requests (leads with name/email/phone),
  search_team_members, search_users, search_career_inquiries
- Properties & Active Prospects: search_properties, get_property, comps, ratings, CoStar, proximity
- Triple Net / public listings: search_public_properties, get_public_property
- News, prospect tags/folders, dashboard stats
- Admin AI chat over property docs: ask_property
- Scraper result files via admin API: list_scraper_files, list_dade_files, get_admin_scraper_status
- Call sheet jobs: list_call_sheet_jobs

For people or contact questions, use search_contacts or search_customer_requests / search_team_members.
For external people enrichment (EnformionGO / ZoomInfo), use those separate MCP servers.
Do NOT say this connector only has property tools.
""".strip()

mcp = create_fastmcp("adminsite", instructions=ADMINSITE_INSTRUCTIONS)

API_URL = (
    os.getenv("ADMINSITE_API_URL")
    or "https://admin.lee-associates-southflorida.com"
).rstrip("/")
API_KEY = os.getenv("ADMINSITE_API_KEY", "")
REQUEST_TIMEOUT = float(os.getenv("ADMINSITE_TIMEOUT", "300"))

SUPABASE_URL = (
    os.getenv("ADMINSITE_SUPABASE_URL")
    or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    or ""
).rstrip("/")
SUPABASE_KEY = (
    os.getenv("ADMINSITE_SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or ""
)

PROPERTY_SELECT = (
    "id, name, address, property_type, building_size, year_built, "
    "sale_price, asking_price, lease_rate, property_status, is_active_prospect, "
    "is_offmarket, is_prospect_favorite, net_operating_income, investment_rating, "
    "investment_rating_summary, latitude, longitude, asset_class, tenancy, "
    "building_class, owner_occupied, number_of_tenants, zoning, number_of_docks, "
    "clear_height, construction_material, available_space, property_description, "
    "location_description, highlight, created_at, updated_at"
)

_supabase = None


def _get_supabase():
    global _supabase
    if _supabase is not None:
        return _supabase
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    from supabase import create_client

    _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


def _headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"
    return headers


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        cleaned[key] = value
    return cleaned


def _unwrap_api_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Surface admin API error messages instead of opaque HTTP envelopes."""
    if not isinstance(payload, dict):
        return {"error": "Unexpected response", "details": payload}

    if payload.get("error") and payload.get("details") is not None and not payload.get("success"):
        details = payload.get("details")
        if isinstance(details, dict):
            message = (
                details.get("error")
                or details.get("message")
                or details.get("raw")
            )
            if message:
                return {
                    "error": str(message),
                    "http_error": payload.get("error"),
                    "details": details,
                }
        return payload

    return payload


async def _proxy(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = await http_request_async(
        method,
        f"{API_URL}{path}",
        headers=_headers(),
        json_body=json_body,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    return _unwrap_api_result(result)


def _compact_property(row: dict[str, Any]) -> dict[str, Any]:
    return _clean(
        {
            "id": row.get("id"),
            "name": row.get("name"),
            "address": row.get("address"),
            "property_type": row.get("property_type"),
            "building_size": row.get("building_size"),
            "year_built": row.get("year_built"),
            "sale_price": row.get("sale_price"),
            "asking_price": row.get("asking_price"),
            "lease_rate": row.get("lease_rate"),
            "property_status": row.get("property_status"),
            "is_active_prospect": row.get("is_active_prospect"),
            "is_offmarket": row.get("is_offmarket"),
            "net_operating_income": row.get("net_operating_income"),
            "investment_rating": row.get("investment_rating"),
            "investment_rating_summary": row.get("investment_rating_summary"),
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "asset_class": row.get("asset_class"),
            "tenancy": row.get("tenancy"),
            "owner_occupied": row.get("owner_occupied"),
            "available_space": row.get("available_space"),
            "highlight": row.get("highlight"),
            "updated_at": row.get("updated_at"),
        }
    )


def _search_properties_db(
    *,
    query: str | None = None,
    property_id: str | None = None,
    is_active_prospect: bool | None = None,
    status: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    client = _get_supabase()
    if client is None:
        return {
            "error": (
                "Missing Supabase credentials. Set ADMINSITE_SUPABASE_URL and "
                "ADMINSITE_SUPABASE_SERVICE_ROLE_KEY (or NEXT_PUBLIC_SUPABASE_URL / "
                "SUPABASE_SERVICE_ROLE_KEY) in the MCP server .env."
            )
        }

    try:
        if property_id and property_id.strip():
            row = _fetch_property_by_id(property_id)
            if not row:
                return {"error": "Property not found", "property_id": property_id}
            return {"count": 1, "properties": [_compact_property(row)]}

        builder = client.table("properties").select(PROPERTY_SELECT)
        if query and query.strip():
            term = query.strip().replace(",", " ").replace("%", "")
            builder = builder.or_(
                f"name.ilike.%{term}%,address.ilike.%{term}%,property_type.ilike.%{term}%"
            )
        if is_active_prospect is not None:
            builder = builder.eq("is_active_prospect", is_active_prospect)
        if status and status.strip() and status.strip().lower() != "all":
            builder = builder.eq("property_status", status.strip())

        response = (
            builder.order("updated_at", desc=True)
            .limit(max(1, min(limit, 50)))
            .execute()
        )
        rows = response.data or []
        return {
            "count": len(rows),
            "properties": [_compact_property(row) for row in rows],
        }
    except Exception as exc:
        return {"error": f"Property search failed: {exc}"}


def _fetch_property_by_id(property_id: str) -> dict[str, Any] | None:
    client = _get_supabase()
    if client is None:
        return None
    try:
        response = (
            client.table("properties")
            .select(PROPERTY_SELECT)
            .eq("id", property_id.strip())
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def _resolve_property(
    *,
    property_id: str | None = None,
    address: str | None = None,
    name: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a property row from id, or best search match by address/name."""
    if property_id and property_id.strip():
        return _fetch_property_by_id(property_id)

    search = (address or name or "").strip()
    if not search:
        return None

    result = _search_properties_db(query=search, limit=5)
    properties = result.get("properties") or []
    if not properties:
        return None

    # Prefer exact/contains address match when possible.
    lowered = search.lower()
    for prop in properties:
        prop_address = str(prop.get("address") or "").lower()
        prop_name = str(prop.get("name") or "").lower()
        if lowered in prop_address or prop_address in lowered or lowered == prop_name:
            full = _fetch_property_by_id(str(prop["id"]))
            return full or prop
    full = _fetch_property_by_id(str(properties[0]["id"]))
    return full or properties[0]


def _api_body_from_property(prop: dict[str, Any]) -> dict[str, Any]:
    """Map DB property columns to the camelCase body the admin APIs expect."""
    sale_or_ask = prop.get("sale_price")
    if sale_or_ask is None:
        sale_or_ask = prop.get("asking_price")

    return _clean(
        {
            "propertyId": prop.get("id"),
            "address": prop.get("address"),
            "propertyType": prop.get("property_type"),
            "buildingSize": prop.get("building_size"),
            "yearBuilt": prop.get("year_built"),
            "salePrice": sale_or_ask,
            "leaseRate": prop.get("lease_rate"),
            "netOperatingIncome": prop.get("net_operating_income"),
            "propertyStatus": prop.get("property_status"),
            "assetClass": prop.get("asset_class"),
            "tenancy": prop.get("tenancy"),
            "buildingClass": prop.get("building_class"),
            "ownerOccupied": prop.get("owner_occupied"),
            "numberOfTenants": prop.get("number_of_tenants"),
            "zoning": prop.get("zoning"),
            "numberOfDocks": prop.get("number_of_docks"),
            "clearHeight": prop.get("clear_height"),
            "constructionMaterial": prop.get("construction_material"),
            "availableSpace": prop.get("available_space"),
            "propertyDescription": prop.get("property_description"),
            "locationDescription": prop.get("location_description"),
            "highlight": prop.get("highlight"),
            "ownerAskingPrice": prop.get("asking_price") or prop.get("sale_price"),
            "ownerRentPerSf": _parse_rent_number(prop.get("lease_rate")),
            "propertyName": prop.get("name"),
        }
    )


def _parse_rent_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    digits = "".join(ch for ch in value if ch.isdigit() or ch == ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def _merge_overrides(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if value is not None and value != "" and value != [] and value != {}:
            merged[key] = value
    return merged


# --- Property lookup tools ---------------------------------------------------

@mcp.tool()
def search_properties(
    query: Optional[str] = None,
    is_active_prospect: Optional[bool] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Search admin-site properties by name, address, or type.

    Call this first when the user mentions a property without a UUID. Returns
    property id values needed by proximity, comps, rating, and extract tools.
    """
    return _search_properties_db(
        query=query,
        is_active_prospect=is_active_prospect,
        status=status,
        limit=limit,
    )


@mcp.tool()
def get_property(property_id: str) -> dict[str, Any]:
    """Get one admin-site property by UUID, including pricing and listing fields."""
    if not property_id.strip():
        return {"error": "property_id is required"}
    row = _fetch_property_by_id(property_id)
    if not row:
        if _get_supabase() is None:
            return {
                "error": (
                    "Missing Supabase credentials. Set ADMINSITE_SUPABASE_URL and "
                    "ADMINSITE_SUPABASE_SERVICE_ROLE_KEY in the MCP server .env."
                )
            }
        return {"error": "Property not found", "property_id": property_id}
    return {"property": _compact_property(row), "raw": row}


# --- Admin API tools ---------------------------------------------------------

@mcp.tool()
async def property_proximity(
    property_id: Optional[str] = None,
    address: Optional[str] = None,
) -> dict[str, Any]:
    """Get team-map proximity analysis for an active prospect property.

    Provide property_id, or an address to resolve the property from the admin DB.
    """
    prop = _resolve_property(property_id=property_id, address=address)
    resolved_id = (prop or {}).get("id") or property_id
    if not resolved_id or not str(resolved_id).strip():
        return {
            "error": "property_id or a resolvable address is required",
            "hint": "Call search_properties first to find the UUID.",
        }
    result = await _proxy(
        "GET",
        "/api/active-prospect-proximity",
        params={"propertyId": str(resolved_id)},
    )
    if prop:
        result = {**result, "property": _compact_property(prop)}
    return result


@mcp.tool()
async def sales_comps(
    address: Optional[str] = None,
    property_id: Optional[str] = None,
    property_type: Optional[str] = None,
    building_size: Optional[float] = None,
    land_size: Optional[float] = None,
    year_built: Optional[int] = None,
    sale_price: Optional[float] = None,
) -> dict[str, Any]:
    """Generate AI sales comparables using the same /api/sales-comps as the admin UI.

    Prefer property_id so the API uses indexed property documents/embeddings.
    """
    prop = _resolve_property(property_id=property_id, address=address)
    body = _api_body_from_property(prop) if prop else {}
    body = _merge_overrides(
        body,
        {
            "address": address,
            "propertyId": property_id,
            "propertyType": property_type,
            "buildingSize": building_size,
            "landSize": land_size,
            "yearBuilt": year_built,
            "salePrice": sale_price,
        },
    )
    if not body.get("address"):
        return {"error": "address is required (or provide a resolvable property_id)"}
    return await _proxy("POST", "/api/sales-comps", json_body=_clean(body))


@mcp.tool()
async def lease_comps(
    address: Optional[str] = None,
    property_id: Optional[str] = None,
    property_type: Optional[str] = None,
    building_size: Optional[float] = None,
    land_size: Optional[float] = None,
    year_built: Optional[int] = None,
    lease_rate: Optional[float] = None,
) -> dict[str, Any]:
    """Generate AI lease comparables using /api/lease-comps (same as admin UI)."""
    prop = _resolve_property(property_id=property_id, address=address)
    body = _api_body_from_property(prop) if prop else {}
    body = _merge_overrides(
        body,
        {
            "address": address,
            "propertyId": property_id,
            "propertyType": property_type,
            "buildingSize": building_size,
            "landSize": land_size,
            "yearBuilt": year_built,
            "leaseRate": lease_rate,
        },
    )
    if not body.get("address"):
        return {"error": "address is required (or provide a resolvable property_id)"}
    return await _proxy("POST", "/api/lease-comps", json_body=_clean(body))


@mcp.tool()
async def investment_rating(
    address: Optional[str] = None,
    property_id: Optional[str] = None,
    property_type: Optional[str] = None,
    building_size: Optional[float] = None,
    sale_price: Optional[float] = None,
    lease_rate: Optional[float] = None,
    year_built: Optional[int] = None,
    net_operating_income: Optional[float] = None,
    include_comps: bool = True,
    ctx: Context = None,
) -> dict[str, Any]:
    """Generate a 0-5 investment rating using /api/investment-rating.

    Hydrates listing fields from the admin DB and, by default, fetches sales +
    lease comps first (same workflow as the admin UI) before scoring.
    """
    prop = _resolve_property(property_id=property_id, address=address)
    body = _api_body_from_property(prop) if prop else {}
    body = _merge_overrides(
        body,
        {
            "address": address,
            "propertyId": property_id,
            "propertyType": property_type,
            "buildingSize": building_size,
            "salePrice": sale_price,
            "leaseRate": lease_rate,
            "yearBuilt": year_built,
            "netOperatingIncome": net_operating_income,
        },
    )
    if not body.get("address") and not body.get("propertyId"):
        return {"error": "address or property_id is required"}

    sales = None
    leases = None
    if include_comps and body.get("address"):
        if ctx is not None:
            await ctx.report_progress(10, 100, "Fetching sales comps...")
        sales = await _proxy("POST", "/api/sales-comps", json_body=_clean(body))
        if ctx is not None:
            await ctx.report_progress(40, 100, "Fetching lease comps...")
        leases = await _proxy("POST", "/api/lease-comps", json_body=_clean(body))
        if isinstance(sales, dict) and sales.get("success"):
            body["salesComps"] = sales.get("comparables") or []
            body["salesCompsCount"] = len(body["salesComps"])
        if isinstance(leases, dict) and leases.get("success"):
            body["leaseComps"] = leases.get("comparables") or []
            body["leaseCompsCount"] = len(body["leaseComps"])

    if ctx is not None:
        await ctx.report_progress(70, 100, "Generating investment rating...")
    rating = await _proxy("POST", "/api/investment-rating", json_body=_clean(body))
    if isinstance(rating, dict):
        rating = {
            **rating,
            "property": _compact_property(prop) if prop else None,
            "sales_comps": (sales or {}).get("comparables") if isinstance(sales, dict) else None,
            "lease_comps": (leases or {}).get("comparables") if isinstance(leases, dict) else None,
        }
    return rating


@mcp.tool()
async def call_script(
    address: Optional[str] = None,
    property_id: Optional[str] = None,
    building_size: Optional[float] = None,
    owner_rent_per_sf: Optional[float] = None,
    owner_asking_price: Optional[float] = None,
    target_cap_rate: Optional[float] = None,
    call_notes: Optional[str] = None,
    include_comps: bool = True,
    ctx: Context = None,
) -> dict[str, Any]:
    """Generate an owner call script using /api/call-script (same as admin UI).

    Hydrates from the admin DB and optionally includes sales comps first.
    """
    prop = _resolve_property(property_id=property_id, address=address)
    body = _api_body_from_property(prop) if prop else {}
    body = _merge_overrides(
        body,
        {
            "address": address,
            "propertyId": property_id,
            "buildingSize": building_size,
            "ownerRentPerSf": owner_rent_per_sf,
            "ownerAskingPrice": owner_asking_price,
            "targetCapRate": target_cap_rate,
            "callNotes": call_notes,
        },
    )
    if not body.get("address") and not body.get("propertyId"):
        return {"error": "address or property_id is required"}

    sales = None
    if include_comps and body.get("address"):
        if ctx is not None:
            await ctx.report_progress(20, 100, "Fetching sales comps for call script...")
        sales = await _proxy("POST", "/api/sales-comps", json_body=_clean(body))
        if isinstance(sales, dict) and sales.get("success"):
            body["salesComps"] = sales.get("comparables") or []

    if ctx is not None:
        await ctx.report_progress(70, 100, "Generating call script...")
    result = await _proxy("POST", "/api/call-script", json_body=_clean(body))
    if isinstance(result, dict) and prop:
        result = {**result, "property": _compact_property(prop)}
    return result


@mcp.tool()
async def costar_changes(
    address: Optional[str] = None,
    property_id: Optional[str] = None,
    property_type: Optional[str] = None,
    building_size: Optional[float] = None,
) -> dict[str, Any]:
    """Extract CoStar tenant/rent-roll changes via /api/costar-changes.

    property_id is strongly recommended so the API can read indexed CoStar sheets.
    """
    prop = _resolve_property(property_id=property_id, address=address)
    body = _api_body_from_property(prop) if prop else {}
    body = _merge_overrides(
        body,
        {
            "address": address,
            "propertyId": property_id,
            "propertyType": property_type,
            "buildingSize": building_size,
        },
    )
    if not body.get("address") and not body.get("propertyId"):
        return {"error": "address or property_id is required"}
    if not body.get("address"):
        # API requires address string even when propertyId is set for some paths
        return {
            "error": "Could not resolve a property address. Pass address or a valid property_id.",
            "property_id": body.get("propertyId"),
        }
    return await _proxy("POST", "/api/costar-changes", json_body=_clean(body))


@mcp.tool()
async def extract_lease_rate(
    property_id: Optional[str] = None,
    address: Optional[str] = None,
) -> dict[str, Any]:
    """Extract asking rent/SF and building size from OM documents via /api/extract-lease-rate."""
    prop = _resolve_property(property_id=property_id, address=address)
    resolved_id = (prop or {}).get("id") or property_id
    if not resolved_id or not str(resolved_id).strip():
        return {
            "error": "property_id or a resolvable address is required",
            "hint": "Call search_properties first to find the UUID.",
        }
    result = await _proxy(
        "POST",
        "/api/extract-lease-rate",
        json_body={"propertyId": str(resolved_id)},
    )
    if prop and isinstance(result, dict):
        result = {**result, "property": _compact_property(prop)}
    return result


@mcp.tool()
async def active_prospect_web_scan(
    address: Optional[str] = None,
    property_id: Optional[str] = None,
    property_name: Optional[str] = None,
) -> dict[str, Any]:
    """Run a web diligence scan via /api/active-prospect-web-scan."""
    prop = _resolve_property(property_id=property_id, address=address, name=property_name)
    body = _api_body_from_property(prop) if prop else {}
    body = _merge_overrides(
        body,
        {
            "address": address,
            "propertyName": property_name,
        },
    )
    if not body.get("address"):
        return {"error": "address is required (or provide a resolvable property_id)"}
    return await _proxy(
        "POST",
        "/api/active-prospect-web-scan",
        json_body=_clean(
            {
                "address": body.get("address"),
                "propertyName": body.get("propertyName"),
            }
        ),
    )


@mcp.tool()
async def sync_property_knowledge(
    property_id: Optional[str] = None,
    address: Optional[str] = None,
) -> dict[str, Any]:
    """Sync a property's documents into the admin RAG index (/api/active-prospect-knowledge-sync).

    Run this before comps/chat-style analysis when documents were recently updated.
    """
    prop = _resolve_property(property_id=property_id, address=address)
    resolved_id = (prop or {}).get("id") or property_id
    if not resolved_id or not str(resolved_id).strip():
        return {
            "error": "property_id or a resolvable address is required",
            "hint": "Call search_properties first to find the UUID.",
        }
    return await _proxy(
        "POST",
        "/api/active-prospect-knowledge-sync",
        json_body={"propertyId": str(resolved_id)},
    )


# Register people/leads/NNN/news/scraper/call-sheet tools on this same MCP server.
from . import adminsite_data as _adminsite_data  # noqa: E402,F401

