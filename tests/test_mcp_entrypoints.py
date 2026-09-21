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


def test_describe_entrypoint_returns_the_reachable_bounded_flow(tmp_path):
    conn = open_db(tmp_path / "reachable-flow.db")
    service_id = services.ensure_service(conn, "orders", "/repos/orders", "jvm-spring")
    evidence = Evidence("OrdersController.kt", 8, 12)
    flows.replace_analysis(
        conn,
        service_id,
        AnalysisResult(
            entrypoints=[EntryPoint("http", "POST", "/orders", "OrdersController.create", evidence)],
            edges=[
                FlowEdge("OrdersController.create", "CreateOrderUseCase.execute", "invokes", evidence),
                FlowEdge("CreateOrderUseCase.execute", "orderRepository.save", "writes", evidence),
            ],
        ),
    )

    detail = queries.describe_entrypoint(conn, "orders", "http", "post", "/orders")

    assert [(edge["from"], edge["to"]) for edge in detail["flow"]] == [
        ("OrdersController.create", "CreateOrderUseCase.execute"),
        ("CreateOrderUseCase.execute", "orderRepository.save"),
    ]


def test_describe_entrypoint_includes_a_deterministic_graphql_contract(tmp_path):
    conn = open_db(tmp_path / "graphql-contract.db")
    service_id = services.ensure_service(conn, "checkout", "/repos/checkout", "node-ts")
    evidence = Evidence("schema.graphql", 1, 1)
    flows.replace_analysis(
        conn,
        service_id,
        AnalysisResult(
            entrypoints=[EntryPoint("graphql", "MUTATION", "checkout", "Mutation.checkout", evidence)],
            contracts={"Mutation.checkout": {"arguments": [], "returns": {"type": "Receipt", "required": True}}},
        ),
    )

    detail = queries.describe_entrypoint(conn, "checkout", "graphql", "mutation", "checkout")

    assert detail["contract"] == {"arguments": [], "returns": {"type": "Receipt", "required": True}}


def test_describe_entrypoint_bounds_flow_context_and_reports_truncation(tmp_path):
    conn = open_db(tmp_path / "flow-budget.db")
    service_id = services.ensure_service(conn, "orders", "/repos/orders", "jvm-spring")
    evidence = Evidence("OrdersController.kt", 8, 12)
    flows.replace_analysis(
        conn,
        service_id,
        AnalysisResult(
            entrypoints=[EntryPoint("http", "POST", "/orders", "OrdersController.create", evidence)],
            edges=[
                FlowEdge("OrdersController.create", "UseCase.execute", "invokes", evidence),
                FlowEdge("UseCase.execute", "Repository.save", "writes", evidence),
            ],
        ),
    )

    detail = queries.describe_entrypoint(conn, "orders", "http", "post", "/orders", max_edges=1)

    assert len(detail["flow"]) == 1
    assert detail["flow_pagination"] == {"max_edges": 1, "truncated": True}
