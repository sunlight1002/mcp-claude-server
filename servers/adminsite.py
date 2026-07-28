"""Admin site MCP server tools.

Proxies the florida_associate_admin Next.js API routes.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .shared import create_fastmcp, http_request

mcp = create_fastmcp("adminsite")

API_URL = (
    os.getenv("ADMINSITE_API_URL")
    or "https://admin.lee-associates-southflorida.com"
).rstrip("/")
API_KEY = os.getenv("ADMINSITE_API_KEY", "")
REQUEST_TIMEOUT = float(os.getenv("ADMINSITE_TIMEOUT", "300"))


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
        headers=_headers(),
        json_body=json_body,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )


@mcp.tool()
def property_proximity(property_id: str) -> dict[str, Any]:
    """Get team-map proximity analysis for an active prospect property."""
    if not property_id.strip():
        return {"error": "property_id is required"}
    return _proxy(
        "GET",
        "/api/active-prospect-proximity",
        params={"propertyId": property_id},
    )


@mcp.tool()
def sales_comps(
    address: str,
    property_type: Optional[str] = None,
    building_size: Optional[float] = None,
    land_size: Optional[float] = None,
    year_built: Optional[int] = None,
    sale_price: Optional[float] = None,
    property_id: Optional[str] = None,
) -> dict[str, Any]:
    """Generate AI sales comparables for a property address."""
    if not address.strip():
        return {"error": "address is required"}
    return _proxy(
        "POST",
        "/api/sales-comps",
        json_body=_clean(
            {
                "address": address,
                "propertyType": property_type,
                "buildingSize": building_size,
                "landSize": land_size,
                "yearBuilt": year_built,
                "salePrice": sale_price,
                "propertyId": property_id,
            }
        ),
    )


@mcp.tool()
def lease_comps(
    address: str,
    property_type: Optional[str] = None,
    building_size: Optional[float] = None,
    land_size: Optional[float] = None,
    year_built: Optional[int] = None,
    lease_rate: Optional[float] = None,
    property_id: Optional[str] = None,
) -> dict[str, Any]:
    """Generate AI lease comparables for a property address."""
    if not address.strip():
        return {"error": "address is required"}
    return _proxy(
        "POST",
        "/api/lease-comps",
        json_body=_clean(
            {
                "address": address,
                "propertyType": property_type,
                "buildingSize": building_size,
                "landSize": land_size,
                "yearBuilt": year_built,
                "leaseRate": lease_rate,
                "propertyId": property_id,
            }
        ),
    )


@mcp.tool()
def investment_rating(
    address: Optional[str] = None,
    property_id: Optional[str] = None,
    property_type: Optional[str] = None,
    building_size: Optional[float] = None,
    sale_price: Optional[float] = None,
    lease_rate: Optional[float] = None,
    year_built: Optional[int] = None,
    net_operating_income: Optional[float] = None,
) -> dict[str, Any]:
    """Generate a 0-5 investment rating and summary for a property."""
    return _proxy(
        "POST",
        "/api/investment-rating",
        json_body=_clean(
            {
                "address": address,
                "propertyId": property_id,
                "propertyType": property_type,
                "buildingSize": building_size,
                "salePrice": sale_price,
                "leaseRate": lease_rate,
                "yearBuilt": year_built,
                "netOperatingIncome": net_operating_income,
            }
        ),
    )


@mcp.tool()
def call_script(
    address: Optional[str] = None,
    property_id: Optional[str] = None,
    building_size: Optional[float] = None,
    owner_rent_per_sf: Optional[float] = None,
    owner_asking_price: Optional[float] = None,
    target_cap_rate: Optional[float] = None,
    call_notes: Optional[str] = None,
) -> dict[str, Any]:
    """Generate an owner call script with cap-rate math and talking points."""
    return _proxy(
        "POST",
        "/api/call-script",
        json_body=_clean(
            {
                "address": address,
                "propertyId": property_id,
                "buildingSize": building_size,
                "ownerRentPerSf": owner_rent_per_sf,
                "ownerAskingPrice": owner_asking_price,
                "targetCapRate": target_cap_rate,
                "callNotes": call_notes,
            }
        ),
    )


@mcp.tool()
def costar_changes(
    address: str,
    property_type: Optional[str] = None,
    building_size: Optional[float] = None,
    property_id: Optional[str] = None,
) -> dict[str, Any]:
    """Extract CoStar tenant/rent roll changes for a property."""
    if not address.strip():
        return {"error": "address is required"}
    return _proxy(
        "POST",
        "/api/costar-changes",
        json_body=_clean(
            {
                "address": address,
                "propertyType": property_type,
                "buildingSize": building_size,
                "propertyId": property_id,
            }
        ),
    )


@mcp.tool()
def extract_lease_rate(property_id: str) -> dict[str, Any]:
    """Extract asking rent per SF and building size from property OM documents."""
    if not property_id.strip():
        return {"error": "property_id is required"}
    return _proxy(
        "POST",
        "/api/extract-lease-rate",
        json_body={"propertyId": property_id},
    )


@mcp.tool()
def active_prospect_web_scan(
    address: str,
    property_name: Optional[str] = None,
) -> dict[str, Any]:
    """Run a web diligence scan and return a summary with source links."""
    if not address.strip():
        return {"error": "address is required"}
    return _proxy(
        "POST",
        "/api/active-prospect-web-scan",
        json_body=_clean(
            {
                "address": address,
                "propertyName": property_name,
            }
        ),
    )
