"""Contract coverage for deterministic persistence and migration facts exposed to agents."""
from orbitkb.analysis.models import AnalysisResult, Evidence, MigrationFact
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import flows, services
from orbitkb.mcp import queries


def test_describe_persistence_returns_bounded_static_migration_facts(tmp_path):
    conn = open_db(tmp_path / "migrations.db")
    service_id = services.ensure_service(conn, "orders", "/repos/orders", "jvm-spring")
    flows.replace_analysis(conn, service_id, AnalysisResult(migration_facts=[
        MigrationFact("add_column", "orders", "external_id", False, Evidence("db/migration/V4__orders.sql", 3, 3)),
        MigrationFact("drop_column", "orders", "legacy_id", True, Evidence("db/migration/V4__orders.sql", 4, 4)),
    ]))

    result = queries.describe_persistence(conn, "orders", limit=1)

    assert result["migration_facts"] == [{
        "operation": "add_column", "table_name": "orders", "column_name": "external_id",
        "destructive": False,
        "evidence": {"file": "db/migration/V4__orders.sql", "start_line": 3, "end_line": 3},
    }]
    assert result["migration_pagination"] == {"total": 2, "truncated": True}
