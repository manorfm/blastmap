"""Usefulness-metric harness: operationalizes "how much relevant knowledge can an
agent get before opening source code" as concrete, CI-checked assertions instead of
a claim. Reuses the Pix-in-checkout fixture from test_change_surface.py rather than
building a second copy of the same fixture.
"""
import json
from pathlib import Path

from tests.test_change_surface import FakeBackend, _build_pix_fixture

from orbitkb.db.connection import open_db
from orbitkb.db.repositories import apis as apis_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.generation import change_surface
from orbitkb.mcp import queries

# Budget: find_change_surface's whole point is a small, progressive-disclosure
# response an agent can act on without pulling in a service's full documentation —
# this number is the harness's concrete stand-in for that claim.
MAX_RESPONSE_BYTES = 3000

# Same idea for describe_service's default (unpaginated-request) page: a real service
# can have far more endpoints than any fixture here, so the default page must stay
# bounded no matter how large the underlying service is.
MAX_DESCRIBE_SERVICE_DEFAULT_BYTES = 8000


def _context_efficiency(result: dict, total_services: int) -> dict:
    flagged = {f["service"] for f in result["primary"]} | {f["service"] for f in result["secondary"]}
    evidence_files = {
        e["file"]
        for bucket in ("primary", "secondary")
        for f in result[bucket]
        for e in f["evidence"]
    }
    return {
        "services_flagged": len(flagged),
        "services_total": total_services,
        "services_flagged_ratio": len(flagged) / total_services if total_services else 0.0,
        "distinct_evidence_files": len(evidence_files),
        "response_bytes": len(json.dumps(result)),
    }


def test_find_change_surface_flags_a_minority_of_services(tmp_path: Path):
    conn = _build_pix_fixture(tmp_path / "eff1.db")
    total_services = len(services_repo.list_services(conn))
    backend = FakeBackend({
        "primary": [
            {"service": "checkout-service", "reason": "owns checkout entry point", "confidence": 0.95},
            {"service": "payments-service", "reason": "owns payment method resolution", "confidence": 0.9},
        ],
        "secondary": [{"service": "order-service", "reason": "consumes payment confirmation", "confidence": 0.4}],
        "no_change": [{"service": "notification-service", "reason": "unrelated", "confidence": 0.8}],
    })

    result = change_surface.analyze_change_surface(conn, "Add support for Pix in checkout", backend)
    metrics = _context_efficiency(result, total_services)

    # The whole point: the agent gets pointed at a minority of the system, not asked
    # to describe_service every indexed service to figure out where to start.
    assert metrics["services_flagged"] < metrics["services_total"]
    assert metrics["services_flagged_ratio"] <= 0.75
    assert metrics["response_bytes"] < MAX_RESPONSE_BYTES


def test_evidence_points_at_a_small_number_of_files_not_the_whole_codebase(tmp_path: Path):
    conn = _build_pix_fixture(tmp_path / "eff2.db")
    backend = FakeBackend({
        "primary": [
            {"service": "checkout-service", "reason": "owns checkout entry point", "confidence": 0.95},
            {"service": "payments-service", "reason": "owns payment method resolution", "confidence": 0.9},
        ],
        "secondary": [],
        "no_change": [],
    })

    result = change_surface.analyze_change_surface(conn, "Add support for Pix in checkout", backend)
    metrics = _context_efficiency(result, len(services_repo.list_services(conn)))

    # An agent acting on this result knows exactly which few files to open — this is
    # the "read only what you need" promise made concrete and checkable.
    assert 0 < metrics["distinct_evidence_files"] <= 3


def test_no_match_short_circuit_is_tiny_and_free(tmp_path: Path):
    conn = _build_pix_fixture(tmp_path / "eff3.db")
    backend = FakeBackend({"primary": [], "secondary": [], "no_change": []})

    result = change_surface.analyze_change_surface(conn, "completely unrelated xyz task", backend)

    assert backend.calls == 0  # no LLM cost paid for a query that matches nothing
    assert len(json.dumps(result)) < 500


def test_describe_service_default_page_stays_bounded_for_a_huge_real_service(tmp_path: Path):
    conn = open_db(tmp_path / "huge.db")
    service_id = services_repo.ensure_service(conn, "huge-service", "/tmp/huge", "python")
    for i in range(200):
        apis_repo.upsert_api(conn, service_id, "GET", f"/resource/{i}", f"summary {i}" * 5, "d" * 20, [], [])

    result = queries.describe_service(conn, "huge-service")

    # The default page, not the full 200 endpoints, is what an agent actually pays
    # tokens for — pagination (see mcp/queries.py) is what keeps this bounded.
    assert len(result["apis"]) == queries.DEFAULT_LIST_LIMIT
    assert result["pagination"]["apis"]["total"] == 200
    assert result["pagination"]["apis"]["truncated"] is True
    assert len(json.dumps(result)) < MAX_DESCRIBE_SERVICE_DEFAULT_BYTES
