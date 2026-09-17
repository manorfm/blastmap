from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from context_insight.db import repository
from context_insight.db.connection import open_db
from context_insight.mcp import queries
from tests.mcp_test_helpers import content_json, server_params


def _chain_fixture(db_path: Path):
    """checkout-service -> payments-service -> ledger-service, no direct A->C edge.
    inventory-service is disconnected, to exercise the unreachable case."""
    conn = open_db(db_path)
    checkout_id = repository.ensure_service(conn, "checkout-service", "/tmp/checkout", "python")
    payments_id = repository.ensure_service(conn, "payments-service", "/tmp/payments", "node-ts")
    ledger_id = repository.ensure_service(conn, "ledger-service", "/tmp/ledger", "python")
    repository.ensure_service(conn, "inventory-service", "/tmp/inventory", "python")

    checkout_api = repository.upsert_api(conn, checkout_id, "POST", "/checkout", "s", "d", [], [])
    repository.replace_calls_for_api(
        conn, checkout_id, checkout_api,
        [{
            "to_service_name": "payments-service", "call_kind": "http", "reason": "authorize payment",
            "data_needed": [], "purpose_kind": "data_fetch", "confidence": 0.9,
        }],
        [{"file": "checkout.py", "start_line": 1, "end_line": 10}],
    )
    repository.reconcile_service_call_targets(conn)

    payments_api = repository.upsert_api(conn, payments_id, "POST", "/charge", "s", "d", [], [])
    repository.replace_calls_for_api(
        conn, payments_id, payments_api,
        [{
            "to_service_name": "ledger-service", "call_kind": "queue_publish", "reason": "record the transaction",
            "data_needed": [], "purpose_kind": "other", "confidence": 0.85,
        }],
        [],
    )
    repository.reconcile_service_call_targets(conn)
    return conn


def test_trace_flow_finds_multi_hop_path(tmp_path: Path):
    conn = _chain_fixture(tmp_path / "chain.db")

    result = queries.trace_flow(conn, "checkout-service", "ledger-service")

    assert result["reachable"] is True
    assert result["hops"] == 2
    path = result["path"]
    assert path[0]["from"] == "checkout-service"
    assert path[0]["to"] == "payments-service"
    assert path[0]["reason"] == "authorize payment"
    assert path[1]["from"] == "payments-service"
    assert path[1]["to"] == "ledger-service"


def test_trace_flow_direct_edge_is_one_hop(tmp_path: Path):
    conn = _chain_fixture(tmp_path / "chain2.db")

    result = queries.trace_flow(conn, "checkout-service", "payments-service")

    assert result["reachable"] is True
    assert result["hops"] == 1


def test_trace_flow_reports_unreachable(tmp_path: Path):
    conn = _chain_fixture(tmp_path / "chain3.db")

    result = queries.trace_flow(conn, "checkout-service", "inventory-service")

    assert result["reachable"] is False
    assert result["path"] == []


def test_trace_flow_respects_max_hops(tmp_path: Path):
    conn = _chain_fixture(tmp_path / "chain4.db")

    result = queries.trace_flow(conn, "checkout-service", "ledger-service", max_hops=1)

    assert result["reachable"] is False


def test_trace_flow_same_service(tmp_path: Path):
    conn = _chain_fixture(tmp_path / "chain5.db")

    result = queries.trace_flow(conn, "checkout-service", "checkout-service")

    assert result["path"] == []
    assert result["reachable"] is True


def test_trace_flow_unknown_service_errors(tmp_path: Path):
    conn = _chain_fixture(tmp_path / "chain6.db")

    assert "error" in queries.trace_flow(conn, "checkout-service", "does-not-exist")
    assert "error" in queries.trace_flow(conn, "does-not-exist", "checkout-service")


@pytest.mark.anyio
async def test_trace_flow_is_reachable_over_stdio(tmp_path: Path):
    db_path = tmp_path / "chain_stdio.db"
    _chain_fixture(db_path).close()

    params = server_params(db_path)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = content_json(
                await session.call_tool("trace_flow", {"from_service": "checkout-service", "to_service": "ledger-service"})
            )
            assert result["reachable"] is True
            assert result["hops"] == 2
