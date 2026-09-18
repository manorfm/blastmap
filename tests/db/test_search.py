from pathlib import Path

from blastmap.db.connection import open_db
from blastmap.db.repositories import apis as apis_repo
from blastmap.db.repositories import search as search_repo
from blastmap.db.repositories import service_calls as service_calls_repo
from blastmap.db.repositories import services as services_repo


def test_search_finds_service_api_and_relationship(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    checkout_id = services_repo.ensure_service(conn, "checkout-service", "/tmp/checkout", "python")
    payments_id = services_repo.ensure_service(conn, "payments-service", "/tmp/payments", "node-ts")
    services_repo.update_service_overview(conn, payments_id, "Charges customer cards.", "Longer.")
    services_repo.update_service_overview(conn, checkout_id, "Checkout flow.", "Longer.")
    api_id = apis_repo.upsert_api(conn, payments_id, "POST", "/charge", "charges a card", "desc", [], [])
    apis_repo.upsert_api(conn, checkout_id, "POST", "/checkout", "starts checkout", "desc", [], [])
    service_calls_repo.replace_calls_for_api(
        conn, checkout_id, api_id,
        [{
            "to_service_name": "payments-service", "call_kind": "http",
            "reason": "authorize the pix payment", "data_needed": [], "purpose_kind": "data_fetch",
        }],
        [],
    )
    search_repo.rebuild_search_index(conn)

    by_service = search_repo.search(conn, "charge")
    assert "service" in {r["kind"] for r in by_service}
    assert "api" in {r["kind"] for r in by_service}

    by_reason = search_repo.search(conn, "pix")
    assert any(r["kind"] == "relationship" and r["service"] == "checkout-service" for r in by_reason)


def test_search_ranks_stronger_matches_first(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    strong_id = services_repo.ensure_service(conn, "discount-service", "/tmp/discount", "python")
    services_repo.update_service_overview(
        conn, strong_id, "Applies discount codes to orders.",
        "discount discount discount — heavily about discounts specifically.",
    )
    weak_id = services_repo.ensure_service(conn, "catalog-service", "/tmp/catalog", "python")
    services_repo.update_service_overview(
        conn, weak_id, "Manages the product catalog.",
        "Mentions discount only once, in passing, as an unrelated aside.",
    )
    search_repo.rebuild_search_index(conn)

    results = search_repo.search(conn, "discount")

    names_in_order = [r["service"] for r in results if r["kind"] == "service"]
    assert names_in_order.index("discount-service") < names_in_order.index("catalog-service")


def test_search_handles_a_query_that_looks_like_an_fts5_operator(tmp_path: Path):
    # "OR"/"AND"/"NOT" are FTS5 boolean operators; a query whose tokens happen to
    # spell one out as a bare word (e.g. "' OR '1'='1", a classic SQLi probe) must
    # not reach FTS5's parser unquoted, or it raises sqlite3.OperationalError
    # instead of just returning a normal (possibly non-empty, via prefix match)
    # result list (found via DAST, tests/test_dast_adversarial_inputs.py).
    conn = open_db(tmp_path / "test.db")
    services_repo.ensure_service(conn, "catalog-service", "/tmp/catalog", "python")
    search_repo.rebuild_search_index(conn)

    assert search_repo.search(conn, "' OR '1'='1") == []
    assert search_repo.search(conn, "AND NOT") == []


def test_search_index_is_rebuilt_per_service_without_duplicating_others(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    a_id = services_repo.ensure_service(conn, "service-a", "/tmp/a", "python")
    b_id = services_repo.ensure_service(conn, "service-b", "/tmp/b", "python")
    services_repo.update_service_overview(conn, a_id, "Handles alpha things.", "L")
    services_repo.update_service_overview(conn, b_id, "Handles beta things.", "L")
    search_repo.rebuild_search_index(conn)
    search_repo.rebuild_search_index_for_service(conn, a_id)  # re-run for just one service

    results = search_repo.search(conn, "things")
    services = [r["service"] for r in results if r["kind"] == "service"]
    assert services.count("service-a") == 1  # not duplicated by the second rebuild
    assert "service-b" in services  # untouched service is still there
