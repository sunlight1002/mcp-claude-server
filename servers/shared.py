"""Shared helpers for MCP server modules."""

from __future__ import annotations

import os
from typing import Any

import httpx


def get_transport_security():
    """Return DNS-rebinding transport security settings when MCP_DOMAIN is set."""
    domain = os.getenv("MCP_DOMAIN", "").strip()
    if not domain:
        return None

    from mcp.server.transport_security import TransportSecuritySettings

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            domain,
            f"{domain}:*",
            "localhost",
            "localhost:*",
            "127.0.0.1",
            "127.0.0.1:*",
        ],
        allowed_origins=[
            f"https://{domain}",
            f"http://{domain}",
            "http://127.0.0.1:*",
            "http://localhost:*",
            "https://claude.ai",
            "https://*.claude.ai",
            "https://www.claude.ai",
        ],
    )


def create_fastmcp(name: str):
    """Create a FastMCP instance with shared transport security settings."""
    from mcp.server.fastmcp import FastMCP

    return FastMCP(
        name,
        transport_security=get_transport_security(),
    )


def http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Perform an HTTP request and return parsed JSON or an error envelope."""
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
            )
    except httpx.RequestError as exc:
        return {"error": f"Request failed: {exc}"}

    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = response.json()
        except ValueError:
            data = {"raw": response.text}
    else:
        data = {"raw": response.text, "content_type": content_type}

    if not response.is_success:
        return {
            "error": f"HTTP {response.status_code}",
            "details": data,
        }

    return data if isinstance(data, dict) else {"data": data}
