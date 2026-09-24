"""Persist source-proven Kubernetes configuration references per repository."""
from __future__ import annotations

import sqlite3

from orbitkb.db.repositories import services as services_repo
from orbitkb.iac.models import (
    KubernetesConfigurationBinding,
    KubernetesConfigurationKeyMismatch,
    KubernetesConfigurationSourceImport,
    KubernetesConfigurationSourceUnknown,
)

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


def list_kubernetes_configuration_bindings_for_environment_keys(
    conn: sqlite3.Connection, service_id: int, environment_keys: set[str],
) -> list[sqlite3.Row]:
    """Return only runtime references matching a page of code-read environment keys."""
    if not environment_keys:
        return []
    placeholders = ", ".join("?" for _ in environment_keys)
    return conn.execute(
        f"""SELECT environment_key, source_kind, source_name, source_key, workload_kind, workload_name,
                    container_name, file_path, start_line, end_line
             FROM kubernetes_configuration_bindings
             WHERE service_id = ? AND environment_key IN ({placeholders})
             ORDER BY environment_key, source_name, source_key, file_path, start_line""",  # nosec B608 - placeholders are generated from a set length.
        (service_id, *sorted(environment_keys)),
    ).fetchall()


def replace_kubernetes_configuration_source_imports(
    conn: sqlite3.Connection, repository_id: int, imports: list[KubernetesConfigurationSourceImport],
) -> None:
    """Replace ``envFrom`` sources without turning their runtime keys into facts."""
    conn.execute("DELETE FROM kubernetes_configuration_source_imports WHERE repository_id = ?", (repository_id,))
    timestamp = now()
    rows = []
    for source_import in imports:
        service_id = None
        if source_import.matched_service_name is not None:
            service = services_repo.get_service_by_name(
                conn, source_import.matched_service_name, repository_id=repository_id,
            )
            service_id = service["id"] if service is not None else None
        rows.append((
            repository_id, service_id, source_import.source_kind, source_import.source_name, source_import.prefix,
            source_import.workload_kind, source_import.workload_name, source_import.container_name,
            source_import.file_path, source_import.start_line, source_import.end_line, timestamp,
        ))
    conn.executemany(
        """INSERT INTO kubernetes_configuration_source_imports
           (repository_id, service_id, source_kind, source_name, prefix, workload_kind, workload_name,
            container_name, file_path, start_line, end_line, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()


def list_kubernetes_configuration_source_imports_for_service(
    conn: sqlite3.Connection, service_id: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT source_kind, source_name, prefix, workload_kind, workload_name, container_name,
                  file_path, start_line, end_line
           FROM kubernetes_configuration_source_imports WHERE service_id = ?
           ORDER BY source_kind, source_name, prefix, file_path, start_line""",
        (service_id,),
    ).fetchall()


def replace_kubernetes_configuration_key_mismatches(
    conn: sqlite3.Connection, repository_id: int, mismatches: list[KubernetesConfigurationKeyMismatch],
) -> None:
    """Replace only source-proven local declaration conflicts for a repository."""
    conn.execute("DELETE FROM kubernetes_configuration_key_mismatches WHERE repository_id = ?", (repository_id,))
    timestamp = now()
    rows = []
    for mismatch in mismatches:
        service_id = None
        if mismatch.matched_service_name is not None:
            service = services_repo.get_service_by_name(
                conn, mismatch.matched_service_name, repository_id=repository_id,
            )
            service_id = service["id"] if service is not None else None
        rows.append((
            repository_id, service_id, mismatch.environment_key, mismatch.source_kind, mismatch.source_name,
            mismatch.source_key, mismatch.reference_file_path, mismatch.reference_start_line,
            mismatch.reference_end_line, mismatch.declaration_file_path, mismatch.declaration_start_line,
            mismatch.declaration_end_line, timestamp,
        ))
    conn.executemany(
        """INSERT INTO kubernetes_configuration_key_mismatches
           (repository_id, service_id, environment_key, source_kind, source_name, source_key,
            reference_file_path, reference_start_line, reference_end_line, declaration_file_path,
            declaration_start_line, declaration_end_line, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()


def list_kubernetes_configuration_key_mismatches_for_service(
    conn: sqlite3.Connection, service_id: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT environment_key, source_kind, source_name, source_key, reference_file_path,
                  reference_start_line, reference_end_line, declaration_file_path,
                  declaration_start_line, declaration_end_line
           FROM kubernetes_configuration_key_mismatches WHERE service_id = ?
           ORDER BY environment_key, source_name, source_key, reference_file_path, reference_start_line""",
        (service_id,),
    ).fetchall()


def replace_kubernetes_configuration_source_unknowns(
    conn: sqlite3.Connection, repository_id: int, unknowns: list[KubernetesConfigurationSourceUnknown],
) -> None:
    """Replace possible external configuration dependencies for a repository."""
    conn.execute("DELETE FROM kubernetes_configuration_source_unknowns WHERE repository_id = ?", (repository_id,))
    timestamp = now()
    rows = []
    for unknown in unknowns:
        service_id = None
        if unknown.matched_service_name is not None:
            service = services_repo.get_service_by_name(
                conn, unknown.matched_service_name, repository_id=repository_id,
            )
            service_id = service["id"] if service is not None else None
        rows.append((
            repository_id, service_id, unknown.environment_key, unknown.source_kind, unknown.source_name,
            unknown.source_key, unknown.reference_file_path, unknown.reference_start_line,
            unknown.reference_end_line, timestamp,
        ))
    conn.executemany(
        """INSERT INTO kubernetes_configuration_source_unknowns
           (repository_id, service_id, environment_key, source_kind, source_name, source_key,
            reference_file_path, reference_start_line, reference_end_line, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()


def list_kubernetes_configuration_source_unknowns_for_service(
    conn: sqlite3.Connection, service_id: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT environment_key, source_kind, source_name, source_key, reference_file_path,
                  reference_start_line, reference_end_line
           FROM kubernetes_configuration_source_unknowns WHERE service_id = ?
           ORDER BY environment_key, source_name, source_key, reference_file_path, reference_start_line""",
        (service_id,),
    ).fetchall()
