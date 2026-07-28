"""ZoomInfo MCP server tools.

Docs: https://api-docs.zoominfo.com/
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx

from .shared import create_fastmcp, http_request

mcp = create_fastmcp("zoominfo")

API_URL = (os.getenv("ZOOMINFO_API_URL") or "https://api.zoominfo.com").rstrip("/")
USERNAME = os.getenv("ZOOMINFO_USERNAME", "")
PASSWORD = os.getenv("ZOOMINFO_PASSWORD", "")
CLIENT_ID = os.getenv("ZOOMINFO_CLIENT_ID", "")
PRIVATE_KEY = os.getenv("ZOOMINFO_PRIVATE_KEY", "")
REQUEST_TIMEOUT = float(os.getenv("ZOOMINFO_TIMEOUT", "60"))

_token: str | None = None
_token_expires_at: float = 0.0


def _auth_payload() -> dict[str, str]:
    if CLIENT_ID and PRIVATE_KEY:
        return {
            "clientId": CLIENT_ID,
            "privateKey": PRIVATE_KEY,
        }
    return {
        "username": USERNAME,
        "password": PASSWORD,
    }


def _get_token() -> str | None:
    """Authenticate with ZoomInfo and return a cached JWT token."""
    global _token, _token_expires_at

    if _token and time.time() < _token_expires_at:
        return _token

    payload = _auth_payload()
    if not payload.get("username") and not payload.get("clientId"):
        return None

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.post(f"{API_URL}/authenticate", json=payload)
    except httpx.RequestError:
        return None

    if not response.is_success:
        return None

    try:
        data = response.json()
    except ValueError:
        return None

    token = data.get("jwt") or data.get("access_token") or data.get("token")
    if not token:
        return None

    _token = token
    _token_expires_at = time.time() + 55 * 60
    return _token


def _auth_headers() -> dict[str, str] | None:
    token = _get_token()
    if not token:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _zoominfo_call(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = _auth_headers()
    if not headers:
        return {
            "error": "Missing ZoomInfo credentials. Set ZOOMINFO_USERNAME and "
            "ZOOMINFO_PASSWORD, or ZOOMINFO_CLIENT_ID and ZOOMINFO_PRIVATE_KEY."
        }

    return http_request(
        method,
        f"{API_URL}{path}",
        headers=headers,
        json_body=payload,
        timeout=REQUEST_TIMEOUT,
    )


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        cleaned[key] = value
    return cleaned


@mcp.tool()
def enrich_contact(
    email_address: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    company_name: Optional[str] = None,
    phone: Optional[str] = None,
    person_id: Optional[int] = None,
) -> dict[str, Any]:
    """Enrich a contact using ZoomInfo.

    Provide at least one identifier such as email, name + company, phone, or person_id.
    """
    payload = _clean(
        {
            "matchPersonInput": [
                _clean(
                    {
                        "emailAddress": email_address,
                        "firstName": first_name,
                        "lastName": last_name,
                        "companyName": company_name,
                        "phone": phone,
                        "personId": person_id,
                    }
                )
            ]
        }
    )
    return _zoominfo_call("POST", "/enrich/contact", payload)


@mcp.tool()
def enrich_company(
    company_name: Optional[str] = None,
    company_website: Optional[str] = None,
    company_id: Optional[int] = None,
) -> dict[str, Any]:
    """Enrich a company using ZoomInfo.

    Provide company_name, company_website, or company_id.
    """
    payload = _clean(
        {
            "matchCompanyInput": [
                _clean(
                    {
                        "companyName": company_name,
                        "companyWebsite": company_website,
                        "companyId": company_id,
                    }
                )
            ]
        }
    )
    return _zoominfo_call("POST", "/enrich/company", payload)


@mcp.tool()
def search_contact(
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    company_name: Optional[str] = None,
    email_address: Optional[str] = None,
    job_title: Optional[str] = None,
    page: int = 1,
    rpp: int = 25,
) -> dict[str, Any]:
    """Search ZoomInfo contacts by name, company, email, or job title."""
    payload = _clean(
        {
            "firstName": first_name,
            "lastName": last_name,
            "companyName": company_name,
            "emailAddress": email_address,
            "jobTitle": job_title,
            "page": page,
            "rpp": rpp,
        }
    )
    return _zoominfo_call("POST", "/search/contact", payload)


@mcp.tool()
def search_company(
    company_name: Optional[str] = None,
    company_website: Optional[str] = None,
    state: Optional[str] = None,
    country: Optional[str] = None,
    page: int = 1,
    rpp: int = 25,
) -> dict[str, Any]:
    """Search ZoomInfo companies by name, website, state, or country."""
    payload = _clean(
        {
            "companyName": company_name,
            "companyWebsite": company_website,
            "state": state,
            "country": country,
            "page": page,
            "rpp": rpp,
        }
    )
    return _zoominfo_call("POST", "/search/company", payload)
