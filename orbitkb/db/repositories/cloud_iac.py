"""The `cloud_iac_resources` table: structurally-parsed IaC declarations for a
whole repository — see orbitkb/iac/scanner.py, which produces the
`IacResource` records this replaces wholesale on every `orbitkb index`."""
from __future__ import annotations

import json
import sqlite3

from orbitkb.db.repositories import services as services_repo
from orbitkb.iac.models import IacResource

from ._util import now


def replace_iac_resources(
    conn: sqlite3.Connection, repository_id: int, resources: list[IacResource],
) -> None:
    conn.execute("DELETE FROM cloud_iac_resources WHERE repository_id = ?", (repository_id,))
    timestamp = now()
    rows = []
    for resource in resources:
        service_id = None
        if resource.matched_service_name is not None:
            service = services_repo.get_service_by_name(
                conn, resource.matched_service_name, repository_id=repository_id,
            )
            service_id = service["id"] if service is not None else None
        rows.append((
            repository_id, service_id, resource.provider, resource.resource_type,
            resource.iac_resource_type, resource.logical_name, resource.physical_name,
            resource.source_format, resource.confidence, resource.file_path,
            resource.start_line, resource.end_line, timestamp, json.dumps(resource.attributes),
        ))
    conn.executemany(
        """INSERT INTO cloud_iac_resources
           (repository_id, service_id, provider, resource_type, iac_resource_type, logical_name,
            physical_name, source_format, confidence, file_path, start_line, end_line, updated_at,
            attributes_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()


def list_iac_resources_for_repository(conn: sqlite3.Connection, repository_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM cloud_iac_resources WHERE repository_id = ? ORDER BY file_path, start_line",
        (repository_id,),
    ).fetchall()


def list_iac_resources_for_service(conn: sqlite3.Connection, service_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM cloud_iac_resources WHERE service_id = ? ORDER BY file_path, start_line",
        (service_id,),
    ).fetchall()
