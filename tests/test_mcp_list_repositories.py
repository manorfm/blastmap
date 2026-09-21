"""Coverage for the list_repositories MCP tool: an agent working cumulatively across
several indexed repositories needs a way to see what's already indexed without
shelling out to the CLI (see README's "cumulative knowledge" framing)."""
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.db.repositories import apis as apis_repo
from orbitkb.analysis.models import AnalysisResult, EntryPoint, Evidence
from orbitkb.db.repositories import flows as flows_repo
from orbitkb.db.repositories import search as search_repo
from orbitkb.mcp import queries

from tests.mcp_test_helpers import content_json, server_params
from tests.test_change_surface import FakeBackend


def test_list_repositories_reports_name_root_path_and_service_count(tmp_path: Path):
    conn = open_db(tmp_path / "fixture.db")
    repo_id = repositories_repo.ensure_repository(conn, "mono-repo", "/tmp/mono-repo")
    repositories_repo.ensure_repository(conn, "empty-repo", "/tmp/empty-repo")
    services_repo.ensure_service(conn, "checkout-service", "/tmp/mono-repo/checkout", "python", repository_id=repo_id)

    result = queries.list_repositories(conn)

    by_name = {r["name"]: r for r in result["repositories"]}
    assert by_name["mono-repo"] == {"name": "mono-repo", "root_path": "/tmp/mono-repo", "service_count": 1}
    assert by_name["empty-repo"]["service_count"] == 0


@pytest.mark.anyio
async def test_list_repositories_over_stdio(tmp_path: Path):
    db_path = tmp_path / "fixture2.db"
    conn = open_db(db_path)
    repositories_repo.ensure_repository(conn, "mono-repo", "/tmp/mono-repo")
    conn.close()

    params = server_params(db_path)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = content_json(await session.call_tool("list_repositories", {}))
            assert result["repositories"][0]["name"] == "mono-repo"


def test_mcp_requires_repository_to_disambiguate_same_named_services(tmp_path: Path):
    conn = open_db(tmp_path / "fixture3.db")
    checkout_id = repositories_repo.ensure_repository(conn, "checkout-repo", "/tmp/checkout-repo")
    fulfillment_id = repositories_repo.ensure_repository(conn, "fulfillment-repo", "/tmp/fulfillment-repo")
    services_repo.ensure_service(conn, "orders", "/tmp/checkout-repo/orders", "go", repository_id=checkout_id)
    fulfillment_service_id = services_repo.ensure_service(
        conn, "orders", "/tmp/fulfillment-repo/orders", "jvm-spring", repository_id=fulfillment_id,
    )
    apis_repo.upsert_api(conn, fulfillment_service_id, "POST", "/orders", "creates orders", "d", [], [])
    flows_repo.replace_analysis(
        conn,
        fulfillment_service_id,
        AnalysisResult(entrypoints=[EntryPoint("http", "POST", "/orders", "Orders.create", Evidence("Orders.java", 1, 2))]),
    )

    listing = queries.list_services(conn)
    ambiguous = queries.describe_service(conn, "orders")
    selected = queries.describe_service(conn, "orders", repository="fulfillment-repo")

    assert {(item["name"], item["repository"]) for item in listing["services"]} == {
        ("orders", "checkout-repo"), ("orders", "fulfillment-repo"),
    }
    assert ambiguous == {
        "error": "ambiguous service: orders; specify repository",
        "repositories": ["checkout-repo", "fulfillment-repo"],
    }
    assert selected["repository"] == "fulfillment-repo"
    assert selected["stack"] == "jvm-spring"


def test_all_service_scoped_queries_require_a_repository_for_duplicate_names(tmp_path: Path):
    conn = open_db(tmp_path / "fixture4.db")
    checkout_id = repositories_repo.ensure_repository(conn, "checkout-repo", "/tmp/checkout-repo")
    fulfillment_id = repositories_repo.ensure_repository(conn, "fulfillment-repo", "/tmp/fulfillment-repo")
    services_repo.ensure_service(conn, "orders", "/tmp/checkout-repo/orders", "go", repository_id=checkout_id)
    service_id = services_repo.ensure_service(conn, "orders", "/tmp/fulfillment-repo/orders", "jvm-spring", repository_id=fulfillment_id)
    apis_repo.upsert_api(conn, service_id, "POST", "/orders", "creates orders", "d", [], [])
    flows_repo.replace_analysis(
        conn,
        service_id,
        AnalysisResult(entrypoints=[EntryPoint("http", "POST", "/orders", "Orders.create", Evidence("Orders.java", 1, 2))]),
    )

    ambiguous = [
        queries.list_apis(conn, "orders"),
        queries.describe_api(conn, "orders", "POST", "/orders"),
        queries.list_entrypoints(conn, "orders"),
        queries.describe_entrypoint(conn, "orders", "http", "POST", "/orders"),
        queries.list_security_findings(conn, "orders"),
        queries.describe_persistence(conn, "orders"),
        queries.describe_messages(conn, "orders"),
        queries.get_relationships(conn, "orders"),
        queries.trace_flow(conn, "orders", "orders"),
    ]
    selected = [
        queries.list_apis(conn, "orders", repository="fulfillment-repo"),
        queries.describe_api(conn, "orders", "POST", "/orders", repository="fulfillment-repo"),
        queries.list_entrypoints(conn, "orders", repository="fulfillment-repo"),
        queries.describe_entrypoint(conn, "orders", "http", "POST", "/orders", repository="fulfillment-repo"),
        queries.list_security_findings(conn, "orders", repository="fulfillment-repo"),
        queries.describe_persistence(conn, "orders", repository="fulfillment-repo"),
        queries.describe_messages(conn, "orders", repository="fulfillment-repo"),
        queries.get_relationships(conn, "orders", repository="fulfillment-repo"),
        queries.trace_flow(
            conn, "orders", "orders", from_repository="fulfillment-repo", to_repository="fulfillment-repo",
        ),
    ]

    assert all(result["error"] == "ambiguous service: orders; specify repository" for result in ambiguous)
    assert all(result.get("repository") == "fulfillment-repo" for result in selected[:-1])
    assert selected[-1]["reachable"] is True


def test_change_surface_requires_or_applies_repository_scope_for_duplicate_names(tmp_path: Path):
    conn = open_db(tmp_path / "fixture5.db")
    checkout_id = repositories_repo.ensure_repository(conn, "checkout-repo", "/tmp/checkout-repo")
    fulfillment_id = repositories_repo.ensure_repository(conn, "fulfillment-repo", "/tmp/fulfillment-repo")
    services_repo.ensure_service(conn, "orders", "/tmp/checkout-repo/orders", "go", repository_id=checkout_id)
    services_repo.ensure_service(conn, "orders", "/tmp/fulfillment-repo/orders", "jvm-spring", repository_id=fulfillment_id)
    backend = FakeBackend({
        "primary": [{"service": "orders", "reason": "explicit scope", "confidence": 0.9}],
        "secondary": [], "no_change": [],
    })

    ambiguous = queries.find_change_surface(conn, backend, "orders change")
    scoped = queries.find_change_surface(
        conn, backend, "orders change", hint_services=["orders"], repository="fulfillment-repo",
    )

    assert ambiguous == {
        "error": "ambiguous service identities; specify repository",
        "duplicate_services": ["orders"],
    }
    assert scoped["scope"] == {"repository": "fulfillment-repo"}
    assert scoped["primary"][0]["service"] == "orders"


def test_search_reports_and_filters_the_owning_repository(tmp_path: Path):
    conn = open_db(tmp_path / "fixture6.db")
    checkout_id = repositories_repo.ensure_repository(conn, "checkout-repo", "/tmp/checkout-repo")
    fulfillment_id = repositories_repo.ensure_repository(conn, "fulfillment-repo", "/tmp/fulfillment-repo")
    checkout_service_id = services_repo.ensure_service(
        conn, "orders", "/tmp/checkout-repo/orders", "go", repository_id=checkout_id,
    )
    fulfillment_service_id = services_repo.ensure_service(
        conn, "orders", "/tmp/fulfillment-repo/orders", "jvm-spring", repository_id=fulfillment_id,
    )
    services_repo.update_service_overview(conn, checkout_service_id, "Checkout order workflow", "d")
    services_repo.update_service_overview(conn, fulfillment_service_id, "Fulfillment order workflow", "d")
    search_repo.rebuild_search_index(conn)

    all_results = queries.search(conn, "order")["results"]
    scoped_results = queries.search(conn, "order", repository="fulfillment-repo")["results"]

    assert {result["repository"] for result in all_results} == {"checkout-repo", "fulfillment-repo"}
    assert {result["repository"] for result in scoped_results} == {"fulfillment-repo"}
