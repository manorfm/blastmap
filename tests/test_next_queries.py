"""TDD coverage for NextQueryRecommender: turns the pieces a change surface already
computed into a small, ranked list of MCP tool calls worth making next — purely from
already-indexed data, no extra LLM call and no extra SQL beyond what's already read."""
from pathlib import Path

from blastmap.db.connection import open_db
from blastmap.db.repositories import apis as apis_repo
from blastmap.db.repositories import messages as messages_repo
from blastmap.db.repositories import services as services_repo
from blastmap.generation.next_queries import NextQueryRecommender


def _seed_service_with_api(conn, name: str) -> int:
    service_id = services_repo.ensure_service(conn, name, f"/tmp/{name}", "python")
    apis_repo.upsert_api(conn, service_id, "POST", f"/{name}", "does the thing", "d", [], [])
    return service_id


def test_recommends_describe_api_for_a_primary_service_with_apis(tmp_path: Path):
    conn = open_db(tmp_path / "t.db")
    _seed_service_with_api(conn, "checkout-service")
    recommender = NextQueryRecommender()

    recs = recommender.recommend(conn, primary=["checkout-service"], secondary=[], unmapped_internal_hint=[])

    describe_api_recs = [r for r in recs if r["tool"] == "describe_api"]
    assert describe_api_recs
    assert describe_api_recs[0]["arguments"] == {"service": "checkout-service", "method": "POST", "path": "/checkout-service"}
    assert "reason" in describe_api_recs[0]


def test_recommends_describe_messages_when_service_has_messages(tmp_path: Path):
    conn = open_db(tmp_path / "t.db")
    service_id = services_repo.ensure_service(conn, "payments-service", "/tmp/payments-service", "node-ts")
    messages_repo.replace_messages(
        conn, service_id, [{"direction": "publishes", "channel": "payment_authorized", "shape_json": [], "description": "d"}], [],
    )
    recommender = NextQueryRecommender()

    recs = recommender.recommend(conn, primary=["payments-service"], secondary=[], unmapped_internal_hint=[])

    describe_messages_recs = [r for r in recs if r["tool"] == "describe_messages"]
    assert describe_messages_recs
    assert describe_messages_recs[0]["arguments"] == {"service": "payments-service"}


def test_recommends_indexing_for_unmapped_internal_services():
    recommender = NextQueryRecommender()

    recs = recommender.recommend(
        conn=None, primary=[], secondary=[],
        unmapped_internal_hint=[{"service": "fraud-service", "via_service": "payments-service"}],
    )

    index_recs = [r for r in recs if r["tool"] == "index"]
    assert index_recs
    assert index_recs[0]["arguments"] == {"service": "fraud-service"}
    assert "fraud-service" in index_recs[0]["reason"]


def test_primary_services_are_recommended_before_secondary(tmp_path: Path):
    conn = open_db(tmp_path / "t.db")
    _seed_service_with_api(conn, "secondary-svc")
    _seed_service_with_api(conn, "primary-svc")
    recommender = NextQueryRecommender()

    recs = recommender.recommend(conn, primary=["primary-svc"], secondary=["secondary-svc"], unmapped_internal_hint=[])

    services_in_order = [r["arguments"]["service"] for r in recs if r["tool"] == "describe_api"]
    assert services_in_order.index("primary-svc") < services_in_order.index("secondary-svc")


def test_recommendations_are_capped(tmp_path: Path):
    conn = open_db(tmp_path / "t.db")
    names = [f"svc-{i}" for i in range(10)]
    for name in names:
        _seed_service_with_api(conn, name)
    recommender = NextQueryRecommender(max_recommendations=5)

    recs = recommender.recommend(conn, primary=names, secondary=[], unmapped_internal_hint=[])

    assert len(recs) <= 5
