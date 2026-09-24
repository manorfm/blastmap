"""Persist source-proven Kubernetes configuration references per repository."""
from __future__ import annotations

import sqlite3

from orbitkb.db.repositories import services as services_repo
from orbitkb.iac.models import KubernetesConfigurationBinding

from ._util import now


def replace_kubernetes_configuration_bindings(
    conn: sqlite3.Connection, repository_id: int, bindings: list[KubernetesConfigurationBinding],
) -> None:
    """Replace one repository snapshot without retaining stale manifest facts."""
    conn.execute("DELETE FROM kubernetes_configuration_bindings WHERE repository_id = ?", (repository_id,))
    timestamp = now()
    rows = []
    for binding in bindings:
        service_id = None
        if binding.matched_service_name is not None:
            service = services_repo.get_service_by_name(
                conn, binding.matched_service_name, repository_id=repository_id,
            )
            service_id = service["id"] if service is not None else None
        rows.append((
            repository_id, service_id, binding.environment_key, binding.source_kind, binding.source_name,
            binding.source_key, binding.workload_kind, binding.workload_name, binding.container_name,
            binding.file_path, binding.start_line, binding.end_line, timestamp,
        ))
    conn.executemany(
        """INSERT INTO kubernetes_configuration_bindings
           (repository_id, service_id, environment_key, source_kind, source_name, source_key,
            workload_kind, workload_name, container_name, file_path, start_line, end_line, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()


def list_kubernetes_configuration_bindings_for_service(
    conn: sqlite3.Connection, service_id: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT environment_key, source_kind, source_name, source_key, workload_kind, workload_name,
                  container_name, file_path, start_line, end_line
           FROM kubernetes_configuration_bindings WHERE service_id = ?
           ORDER BY environment_key, source_name, source_key, file_path, start_line""",
        (service_id,),
    ).fetchall()
