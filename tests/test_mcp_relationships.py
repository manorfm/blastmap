"""Integration test for get_relationships: drives the real MCP server over stdio
against a deterministic fixture DB built directly through db.repository (no LLM call
involved), the same pattern used by find_change_surface's end-to-end test.
"""
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from context_insight.db import repository
from context_insight.db.connection import open_db

from tests.mcp_test_helpers import content_json, server_params


def _build_fixture_db(db_path: Path) -> None:
    conn = open_db(db_path)
    checkout_id = repository.ensure_service(conn, "checkout-service", "/tmp/checkout", "python")
    payments_id = repository.ensure_service(conn, "payments-service", "/tmp/payments", "node-ts")
    notif_id = repository.ensure_service(conn, "notification-service", "/tmp/notif", "python")

    api_id = repository.upsert_api(
        conn, checkout_id, "POST", "/checkout", "starts checkout", "desc", [],
        [{"file": "checkout.py", "start_line": 1, "end_line": 20}],
    )
    repository.replace_calls_for_api(
        conn, checkout_id, api_id,
        [{
            "to_service_name": "payments-service", "call_kind": "http",
            "reason": "authorize the payment for the order", "data_needed": ["amount"],
            "purpose_kind": "data_fetch", "confidence": 0.9,
        }],
        [{"file": "checkout.py", "start_line": 1, "end_line": 20}],
    )
    repository.reconcile_service_call_targets(conn)

    repository.replace_messages(
        conn, checkout_id,
        [{"direction": "publishes", "channel": "order_created", "shape_json": [], "description": "d"}],
        [{"file": "checkout.py", "start_line": 30, "end_line": 35}],
    )
    repository.replace_messages(
        conn, notif_id,
        [{"direction": "consumes", "channel": "order_created", "shape_json": [], "description": "d"}],
        [{"file": "notify.py", "start_line": 5, "end_line": 10}],
    )
    conn.close()


@pytest.mark.anyio
async def test_get_relationships_returns_outbound_inbound_and_message_links(tmp_path: Path):
    db_path = tmp_path / "fixture.db"
    _build_fixture_db(db_path)

    params = server_params(db_path)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            checkout_rels = content_json(
                await session.call_tool("get_relationships", {"service": "checkout-service"})
            )
            outbound = [r for r in checkout_rels["relationships"] if r["direction"] == "outbound" and r["type"] == "HTTP"]
            assert outbound[0]["target_service"] == "payments-service"
            assert outbound[0]["reason"] == "authorize the payment for the order"
            assert outbound[0]["confidence"] == 0.9
            assert outbound[0]["evidence"]

            message_link = [r for r in checkout_rels["relationships"] if r["type"] == "MESSAGE_LINK"][0]
            assert message_link["channel"] == "order_created"
            assert message_link["target_service"] == "notification-service"

            payments_rels = content_json(
                await session.call_tool("get_relationships", {"service": "payments-service", "direction": "inbound"})
            )
            inbound = payments_rels["relationships"]
            assert len(inbound) == 1
            assert inbound[0]["source_service"] == "checkout-service"

            unknown = content_json(
                await session.call_tool("get_relationships", {"service": "does-not-exist"})
            )
            assert "error" in unknown
