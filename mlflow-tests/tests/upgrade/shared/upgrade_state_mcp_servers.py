"""Shared MLflow upgrade scenario state for the MCP Server Registry suite."""

from __future__ import annotations

MCP_SERVER_STATE = {
    "name": "io.opendatahub.upgrade-tests/upgrade-mcp-server-1",
    "version": "1.0.0",
    "description": "Static upgrade-test MCP server",
    "tags": {
        "upgrade-tag-key": "upgrade-tag-value",
    },
    "access_endpoint": {
        "url": "https://example.invalid/upgrade-mcp",
        "transport_type": "streamable-http",
    },
}
