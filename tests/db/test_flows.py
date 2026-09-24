from orbitkb.analysis.models import (
    AnalysisResult,
    CloudFact,
    EntryPoint,
    Evidence,
    FlowEdge,
    MessageContract,
    MigrationFact,
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


def test_grpc_entrypoint_contract_is_persisted_with_the_flow_snapshot(tmp_path):
    conn = open_db(tmp_path / "grpc.db")
    service_id = services.ensure_service(conn, "inventory", "/repos/inventory", "go")
    evidence = Evidence("inventory.proto", 7, 7)

    flows.replace_analysis(
        conn,
        service_id,
        AnalysisResult(entrypoints=[
            EntryPoint(
                "grpc", "RPC", "inventory.v1.Inventory.Reserve",
                "proto.inventory.v1.Inventory.Reserve", evidence,
            ),
        ], contracts={
            "proto.inventory.v1.Inventory.Reserve": {"formal_contract": {"format": "protobuf"}},
        }),
    )

    entrypoint = flows.get_entrypoint(
        conn, service_id, "grpc", "rpc", "inventory.v1.Inventory.Reserve",
    )

    assert entrypoint is not None
    assert flows.get_entrypoint_contract(conn, entrypoint["id"]) == {
        "formal_contract": {"format": "protobuf"},
    }


def test_static_message_contracts_are_replaced_with_the_flow_snapshot(tmp_path):
    conn = open_db(tmp_path / "contracts.db")
    service_id = services.ensure_service(conn, "orders", "/repos/orders", "node-ts")
    evidence = Evidence("resolvers.ts", 10, 10)

    flows.replace_analysis(
        conn, service_id, AnalysisResult(message_contracts=[
            MessageContract("publishes", "orders", "created", None, evidence, "1"),
        ]),
    )

    assert [dict(row) for row in flows.list_static_message_contracts(conn, service_id)] == [{
        "direction": "publishes", "channel": "orders", "routing_key": "created", "payload_type": None,
        "message_version": "1",
        "file_path": "resolvers.ts", "start_line": 10, "end_line": 10,
    }]


def test_static_cloud_facts_are_replaced_with_the_flow_snapshot(tmp_path):
    conn = open_db(tmp_path / "cloud.db")
    service_id = services.ensure_service(conn, "orders", "/repos/orders", "node-ts")
    evidence = Evidence("publisher.ts", 4, 4)

    flows.replace_analysis(
        conn, service_id, AnalysisResult(cloud_facts=[
            CloudFact("aws", "queue", "sqs", "SendMessage", "publish", "aws-sdk-js-v3", None, evidence),
        ]),
    )

    facts = flows.list_static_cloud_facts(conn, service_id)
    assert [dict(row) for row in facts] == [{
        "provider": "aws", "resource_type": "queue", "service_name": "sqs",
        "operation": "SendMessage", "operation_kind": "publish", "sdk": "aws-sdk-js-v3",
        "target_name": None, "file_path": "publisher.ts", "start_line": 4, "end_line": 4,
    }]

    flows.replace_analysis(conn, service_id, AnalysisResult())
    assert flows.list_static_cloud_facts(conn, service_id) == []


def test_static_migration_facts_are_replaced_with_the_flow_snapshot(tmp_path):
    conn = open_db(tmp_path / "migrations.db")
    service_id = services.ensure_service(conn, "orders", "/repos/orders", "jvm-spring")
    evidence = Evidence("db/migration/V4__orders.sql", 3, 3)

    flows.replace_analysis(conn, service_id, AnalysisResult(migration_facts=[
        MigrationFact("add_column", "orders", "external_id", False, evidence),
        MigrationFact("drop_column", "orders", "legacy_id", True, Evidence(evidence.file_path, 4, 4)),
    ]))

    assert [dict(row) for row in flows.list_static_migration_facts(conn, service_id)] == [
        {
            "operation": "add_column", "table_name": "orders", "column_name": "external_id",
            "destructive": 0, "file_path": "db/migration/V4__orders.sql", "start_line": 3, "end_line": 3,
        },
        {
            "operation": "drop_column", "table_name": "orders", "column_name": "legacy_id",
            "destructive": 1, "file_path": "db/migration/V4__orders.sql", "start_line": 4, "end_line": 4,
        },
    ]

    flows.replace_analysis(conn, service_id, AnalysisResult())
    assert flows.list_static_migration_facts(conn, service_id) == []
