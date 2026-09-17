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
