from orbitkb.analysis.models import AnalysisResult, EntryPoint, Evidence, FlowEdge
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import flows, services
from orbitkb.mcp import queries


def test_entrypoint_tools_keep_transport_and_flow_context_separate(tmp_path):
    conn = open_db(tmp_path / "entrypoints.db")
    service_id = services.ensure_service(conn, "checkout", "/repos/checkout", "node-ts")
    evidence = Evidence("resolvers.ts", 8, 12)
    flows.replace_analysis(
        conn,
        service_id,
        AnalysisResult(
            entrypoints=[EntryPoint("graphql", "MUTATION", "createOrder", "Mutation.createOrder", evidence)],
            edges=[FlowEdge("Mutation.createOrder", "orders.create", "writes", evidence)],
        ),
    )

    listing = queries.list_entrypoints(conn, "checkout")
    detail = queries.describe_entrypoint(conn, "checkout", "graphql", "mutation", "createOrder")

    assert listing["entrypoints"] == [
        {
            "kind": "graphql", "method": "MUTATION", "name": "createOrder", "symbol": "Mutation.createOrder",
            "evidence": {"file": "resolvers.ts", "start_line": 8, "end_line": 12},
        }
    ]
    assert detail["flow"][0]["kind"] == "writes"
    assert detail["flow"][0]["origin"] == "static"
