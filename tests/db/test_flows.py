from orbitkb.analysis.models import (
    AnalysisResult,
    EntryPoint,
    Evidence,
    FlowEdge,
    MessageContract,
)
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import flows, services


def test_flow_snapshot_is_replaced_per_service(tmp_path):
    conn = open_db(tmp_path / "flows.db")
    service_id = services.ensure_service(conn, "orders", "/repos/orders", "go")
    evidence = Evidence("main.go", 10, 12)
    analysis = AnalysisResult(
        entrypoints=[EntryPoint("http", "POST", "/orders", "Orders.Create", evidence)],
        edges=[FlowEdge("Orders.Create", "orders.UseCase.Execute", "invokes", evidence)],
    )

    flows.replace_analysis(conn, service_id, analysis)

    entrypoint = flows.get_entrypoint(conn, service_id, "http", "post", "/orders")
    assert entrypoint is not None
    assert [edge["to_symbol"] for edge in flows.list_entrypoint_edges(conn, entrypoint["id"])] == [
        "orders.UseCase.Execute"
    ]

    flows.replace_analysis(conn, service_id, AnalysisResult())
    assert flows.list_entrypoints(conn, service_id) == []


def test_static_message_contracts_are_replaced_with_the_flow_snapshot(tmp_path):
    conn = open_db(tmp_path / "contracts.db")
    service_id = services.ensure_service(conn, "orders", "/repos/orders", "node-ts")
    evidence = Evidence("resolvers.ts", 10, 10)

    flows.replace_analysis(
        conn, service_id, AnalysisResult(message_contracts=[
            MessageContract("publishes", "orders", "created", None, evidence),
        ]),
    )

    assert [dict(row) for row in flows.list_static_message_contracts(conn, service_id)] == [{
        "direction": "publishes", "channel": "orders", "routing_key": "created", "payload_type": None,
        "file_path": "resolvers.ts", "start_line": 10, "end_line": 10,
    }]
