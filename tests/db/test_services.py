from pathlib import Path

from blastmap.db.connection import open_db
from blastmap.db.repositories import repositories as repositories_repo
from blastmap.db.repositories import services as services_repo


def test_ensure_service_and_overview(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    assert service_id > 0

    same_id = services_repo.ensure_service(conn, "orders-service", "/tmp/orders-moved", "python")
    assert same_id == service_id

    services_repo.update_service_overview(conn, service_id, "Handles orders.", "Longer description.")
    row = services_repo.get_service_by_name(conn, "orders-service")
    assert row["short_desc"] == "Handles orders."
    assert row["root_path"] == "/tmp/orders-moved"


def test_get_repository_by_name(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repositories_repo.ensure_repository(conn, "checkout-repo", "/tmp/checkout-repo")

    row = repositories_repo.get_repository_by_name(conn, "checkout-repo")
    assert row["root_path"] == "/tmp/checkout-repo"
    assert repositories_repo.get_repository_by_name(conn, "does-not-exist") is None


def test_ensure_repository_links_to_service(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repo_id = repositories_repo.ensure_repository(conn, "checkout-repo", "/tmp/checkout-repo")
    assert repo_id > 0

    same_repo_id = repositories_repo.ensure_repository(conn, "checkout-repo-renamed", "/tmp/checkout-repo")
    assert same_repo_id == repo_id  # keyed by root_path, name update is allowed

    services_repo.ensure_service(
        conn, "checkout-service", "/tmp/checkout-repo/checkout", "python", repository_id=repo_id
    )
    row = services_repo.get_service_by_name(conn, "checkout-service")
    assert row["repository_id"] == repo_id

    # Re-ensuring without a repository_id must not null out the existing link.
    services_repo.ensure_service(conn, "checkout-service", "/tmp/checkout-repo/checkout", "python")
    row = services_repo.get_service_by_name(conn, "checkout-service")
    assert row["repository_id"] == repo_id


def test_list_services_for_repository(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    repo_id = repositories_repo.ensure_repository(conn, "mono-repo", "/tmp/mono-repo")
    other_repo_id = repositories_repo.ensure_repository(conn, "other-repo", "/tmp/other-repo")
    services_repo.ensure_service(conn, "checkout-service", "/tmp/mono-repo/checkout", "python", repository_id=repo_id)
    services_repo.ensure_service(conn, "payments-service", "/tmp/mono-repo/payments", "node-ts", repository_id=repo_id)
    services_repo.ensure_service(conn, "unrelated-service", "/tmp/other-repo", "python", repository_id=other_repo_id)

    names = {r["name"] for r in services_repo.list_services_for_repository(conn, repo_id)}

    assert names == {"checkout-service", "payments-service"}
