"""EnformionGO MCP server tools.

Docs: https://enformiongo.readme.io/reference/overview
"""

from __future__ import annotations

import os
from typing import Any, Optional

import requests

from .shared import create_fastmcp

mcp = create_fastmcp("enformion")


def _api_url() -> str:
    return (os.getenv("ENFORMIONGO_API_URL") or "https://devapi.enformion.com").rstrip("/")


def _credentials() -> tuple[str | None, str | None]:
    name = (
        os.getenv("ENFORMIONGO_ACCESS_PROFILE_NAME")
        or os.getenv("ENFORMIONGO_KEY_NAME")
    )
    password = (
        os.getenv("ENFORMIONGO_ACCESS_PROFILE_PASSWORD")
        or os.getenv("ENFORMIONGO_KEY_PASSWORD")
    )
    return name, password


def _timeout() -> int:
    return int(os.getenv("ENFORMIONGO_TIMEOUT", "30"))


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None, an empty string, or an empty container."""
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, dict):
            nested = _clean(value)
            if nested:
                cleaned[key] = nested
        else:
            cleaned[key] = value
    return cleaned


def _extract_api_error(data: dict[str, Any]) -> str | None:
    """Return a human-readable error from an EnformionGO JSON body, if present."""
    if not isinstance(data, dict):
        return None

    for key in ("error", "Error", "message", "Message"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = _extract_api_error(value)
            if nested:
                return nested

    for key in ("isError", "IsError"):
        if data.get(key) is True:
            for detail_key in ("error", "Error", "message", "Message"):
                detail = data.get(detail_key)
                if isinstance(detail, str) and detail.strip():
                    return detail.strip()
            return "EnformionGO reported an error in the response body."

    return None


def _call(search_type: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST a request to an EnformionGO endpoint and return the parsed JSON."""
    ap_name, ap_password = _credentials()
    if not ap_name or not ap_password:
        return {
            "error": "Missing credentials. Set ENFORMIONGO_ACCESS_PROFILE_NAME "
            "(or ENFORMIONGO_KEY_NAME) and ENFORMIONGO_ACCESS_PROFILE_PASSWORD "
            "(or ENFORMIONGO_KEY_PASSWORD) in your environment / .env file."
        }

    url = f"{_api_url()}{path}"
    headers = {
        "galaxy-ap-name": ap_name,
        "galaxy-ap-password": ap_password,
        "galaxy-search-type": search_type,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        response = requests.post(
            url,
            json=_clean(payload),
            headers=headers,
            timeout=_timeout(),
        )
    except requests.RequestException as exc:
        return {"error": f"Request to EnformionGO failed: {exc}"}

    try:
        data = response.json()
    except ValueError:
        data = {"raw": response.text}

    if not response.ok:
        api_error = _extract_api_error(data) if isinstance(data, dict) else None
        return {
            "error": api_error or f"EnformionGO returned HTTP {response.status_code}",
            "details": data,
        }

    if isinstance(data, dict):
        api_error = _extract_api_error(data)
        if api_error:
            return {"error": api_error, "details": data}

    return data


@mcp.tool()
def contact_enrichment(
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    middle_name: Optional[str] = None,
    dob: Optional[str] = None,
    age: Optional[int] = None,
    address_line1: Optional[str] = None,
    address_line2: Optional[str] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None,
) -> dict[str, Any]:
    """Enrich a single contact and return the best matching person.

    Provide at least TWO identifying fields for a reliable match,
    e.g. name + phone, name + address, or email + location.
    """
    payload = {
        "FirstName": first_name,
        "MiddleName": middle_name,
        "LastName": last_name,
        "Dob": dob,
        "Age": age,
        "Address": {
            "addressLine1": address_line1,
            "addressLine2": address_line2,
        },
        "Phone": phone,
        "Email": email,
    }
    return _call("DevAPIContactEnrich", "/Contact/Enrich", payload)


@mcp.tool()
def person_search(
    first_name: Optional[str] = None,
    middle_name: Optional[str] = None,
    last_name: Optional[str] = None,
    dob: Optional[str] = None,
    age: Optional[int] = None,
    age_range: Optional[str] = None,
    address_line1: Optional[str] = None,
    address_line2: Optional[str] = None,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    includes: Optional[list[str]] = None,
    search_type: str = "Person",
    page: int = 1,
    results_per_page: int = 10,
) -> dict[str, Any]:
    """Search for people matching the given criteria and return a list of matches."""
    payload: dict[str, Any] = {
        "FirstName": first_name,
        "MiddleName": middle_name,
        "LastName": last_name,
        "Dob": dob,
        "Age": age,
        "AgeRange": age_range,
        "Email": email,
        "Phone": phone,
        "Page": page,
        "ResultsPerPage": results_per_page,
    }
    if address_line1 or address_line2:
        payload["Addresses"] = [
            {"AddressLine1": address_line1, "AddressLine2": address_line2}
        ]
    if includes:
        payload["Includes"] = includes
    return _call(search_type, "/PersonSearch", payload)


@mcp.tool()
def caller_id(phone: str) -> dict[str, Any]:
    """Look up caller identity information for a phone number."""
    return _call("DevAPICallerID", "/Phone/Caller", {"Phone": phone})


@mcp.tool()
def reverse_phone_search(
    phone: str,
    page: int = 1,
    results_per_page: int = 10,
) -> dict[str, Any]:
    """Reverse phone search: find people associated with a phone number."""
    payload = {
        "Phone": phone,
        "Page": page,
        "ResultsPerPage": results_per_page,
    }
    return _call("ReversePhone", "/Phone/Enrich", payload)


@mcp.tool()
def address_id(address_line1: str, address_line2: str) -> dict[str, Any]:
    """Identify and validate an address, returning associated address details."""
    payload = {
        "addressLine1": address_line1,
        "addressLine2": address_line2,
    }
    return _call("DevAPIAddressID", "/Address/Id", payload)
