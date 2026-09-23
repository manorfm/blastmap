"""TDD coverage for the cloud-related architecture findings in
generation/architecture.py — deterministic, computed from static_cloud_facts
and cloud_iac_resources alone, no LLM."""
from pathlib import Path

from orbitkb.analysis.models import AnalysisResult, CloudFact, Evidence
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import cloud_iac as cloud_iac_repo
from orbitkb.db.repositories import flows as flows_repo
from orbitkb.db.repositories import repositories as repositories_repo
from orbitkb.db.repositories import services as services_repo
from orbitkb.generation.architecture import (
    find_cloud_code_without_iac,
    find_cloud_iac_unused_in_code,
    find_shared_cloud_resource,
)
from orbitkb.iac.models import IacResource

EVIDENCE = Evidence("publisher.ts", 4, 4)


def _cloud_fact(target_name: str | None = "orders-queue") -> CloudFact:
    return CloudFact("aws", "queue", "sqs", "SendMessage", "publish", "aws-sdk-js-v3", target_name, EVIDENCE)


def _iac_resource(physical_name: str | None = "orders-queue", matched_service_name: str | None = None) -> IacResource:
    return IacResource(
        provider="aws", resource_type="queue", iac_resource_type="aws_sqs_queue",
        logical_name="orders", physical_name=physical_name, source_format="terraform",
        confidence="high", file_path="infra/main.tf", start_line=1, end_line=3,
        matched_service_name=matched_service_name,
    )


def test_cloud_code_without_matching_iac_is_flagged(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    service_id = services_repo.ensure_service(
        conn, "orders-service", "/tmp/shop/orders", "node-ts", repository_id=repository_id,
    )
    flows_repo.replace_analysis(conn, service_id, AnalysisResult(cloud_facts=[_cloud_fact()]))

    findings = find_cloud_code_without_iac(conn)

    assert len(findings) == 1
    assert findings[0]["kind"] == "cloud_dependency_without_iac"
    assert findings[0]["severity"] == "warning"
    assert findings[0]["services"] == ["orders-service"]
    assert findings[0]["detail"]["target_name"] == "orders-queue"


def test_cloud_code_with_matching_iac_is_not_flagged(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    service_id = services_repo.ensure_service(
        conn, "orders-service", "/tmp/shop/orders", "node-ts", repository_id=repository_id,
    )
    flows_repo.replace_analysis(conn, service_id, AnalysisResult(cloud_facts=[_cloud_fact()]))
    cloud_iac_repo.replace_iac_resources(conn, repository_id, [_iac_resource()])

    assert find_cloud_code_without_iac(conn) == []


def test_cloud_code_without_a_resolved_target_name_is_not_flagged(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    service_id = services_repo.ensure_service(
        conn, "orders-service", "/tmp/shop/orders", "node-ts", repository_id=repository_id,
    )
    flows_repo.replace_analysis(conn, service_id, AnalysisResult(cloud_facts=[_cloud_fact(target_name=None)]))

    assert find_cloud_code_without_iac(conn) == []


def test_iac_resource_unused_in_code_is_flagged(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    services_repo.ensure_service(conn, "orders-service", "/tmp/shop/orders", "node-ts", repository_id=repository_id)
    cloud_iac_repo.replace_iac_resources(
        conn, repository_id, [_iac_resource(matched_service_name="orders-service")],
    )

    findings = find_cloud_iac_unused_in_code(conn)

    assert len(findings) == 1
    assert findings[0]["kind"] == "cloud_iac_resource_unused"
    assert findings[0]["severity"] == "info"
    assert findings[0]["services"] == ["orders-service"]


def test_iac_resource_used_in_code_is_not_flagged(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    service_id = services_repo.ensure_service(
        conn, "orders-service", "/tmp/shop/orders", "node-ts", repository_id=repository_id,
    )
    flows_repo.replace_analysis(conn, service_id, AnalysisResult(cloud_facts=[_cloud_fact()]))
    cloud_iac_repo.replace_iac_resources(
        conn, repository_id, [_iac_resource(matched_service_name="orders-service")],
    )

    assert find_cloud_iac_unused_in_code(conn) == []


def test_iac_resource_unattributed_to_a_service_is_not_flagged(tmp_path: Path):
    """A repository-scoped resource (matched_service_name=None) is skipped by this
    detector rather than reported with an empty `services` list: architecture
    findings are deduplicated across runs by (kind, services, entrypoint) identity,
    and an always-empty services tuple would collapse unrelated resources across
    different repositories into the same identity — a correctness bug, not a
    display nicety. See plan.md's WP7 notes."""
    conn = open_db(tmp_path / "test.db")
    repository_id = repositories_repo.ensure_repository(conn, "shop", "/tmp/shop")
    cloud_iac_repo.replace_iac_resources(conn, repository_id, [_iac_resource(matched_service_name=None)])

    assert find_cloud_iac_unused_in_code(conn) == []


def test_two_services_on_the_same_named_queue_are_flagged_as_shared(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    a = services_repo.ensure_service(conn, "orders-service", "/tmp/orders", "node-ts")
    b = services_repo.ensure_service(conn, "billing-service", "/tmp/billing", "python")
    flows_repo.replace_analysis(conn, a, AnalysisResult(cloud_facts=[_cloud_fact()]))
    flows_repo.replace_analysis(conn, b, AnalysisResult(cloud_facts=[_cloud_fact()]))

    findings = find_shared_cloud_resource(conn)

    assert len(findings) == 1
    assert set(findings[0]["services"]) == {"orders-service", "billing-service"}
    assert findings[0]["kind"] == "shared_cloud_resource"
    assert findings[0]["detail"]["target_name"] == "orders-queue"


def test_one_service_on_a_queue_is_not_flagged_as_shared(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    a = services_repo.ensure_service(conn, "orders-service", "/tmp/orders", "node-ts")
    flows_repo.replace_analysis(conn, a, AnalysisResult(cloud_facts=[_cloud_fact()]))

    assert find_shared_cloud_resource(conn) == []
