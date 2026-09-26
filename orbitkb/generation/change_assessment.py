"""Deterministically compare persisted change units with a bounded Git file diff."""
from __future__ import annotations

from pathlib import Path, PurePosixPath


def assess_change_units(
    repository_root: Path,
    changed_files: list[str],
    change_units: list[dict],
    service_roots: dict[str, Path],
    public_error_contracts: list[dict],
) -> dict:
    """Classify unit coverage only from source evidence; never infer semantic coverage."""
    normalized_changes = sorted(set(changed_files))
    relative_roots = {
        name: _relative_service_root(repository_root, service_root)
        for name, service_root in service_roots.items()
    }
    planned_services = {
        service
        for unit in change_units
        for service in [unit.get("service"), *unit.get("dependencies", [])]
        if isinstance(service, str)
    }
    planned_roots = [
        relative_roots[service]
        for service in planned_services
        if relative_roots.get(service) is not None
    ]

    covered_change_units: list[dict] = []
    omitted_change_units: list[dict] = []
    unassessable_change_units: list[dict] = []
    pending_validation: list[dict] = []
    for unit in change_units:
        unit_id = unit["id"]
        evidence_files = _unit_evidence_files(unit, relative_roots.get(unit.get("service")))
        if evidence_files:
            covered_files = sorted(set(evidence_files) & set(normalized_changes))
            if covered_files:
                covered_change_units.append({"id": unit_id, "changed_files": covered_files})
            else:
                omitted_change_units.append({"id": unit_id, "evidence_files": evidence_files})
        else:
            reason = (
                "the planned service is not indexed in this repository"
                if unit.get("service") not in relative_roots
                else "the change unit has no source evidence file"
            )
            unassessable_change_units.append({"id": unit_id, "reason": reason})
        pending_validation.append({
            "change_unit_id": unit_id,
            "contracts": list(unit.get("related_contracts", [])),
            "dependencies": list(unit.get("dependencies", [])),
            "checks": list(unit.get("validation", [])),
        })

    return {
        "changed_files": normalized_changes,
        "covered_change_units": covered_change_units,
        "omitted_change_units": omitted_change_units,
        "unassessable_change_units": unassessable_change_units,
        "files_outside_planned_surface": [
            path for path in normalized_changes if not any(_is_within(path, root) for root in planned_roots)
        ],
        "pending_validation": pending_validation,
        "public_error_contracts_at_risk": _public_error_contracts_at_risk(
            normalized_changes, public_error_contracts, relative_roots,
        ),
    }


def _relative_service_root(repository_root: Path, service_root: Path) -> str | None:
    try:
        return service_root.resolve().relative_to(repository_root.resolve()).as_posix()
    except ValueError:
        return None


def _unit_evidence_files(unit: dict, relative_service_root: str | None) -> list[str]:
    if relative_service_root is None:
        return []
    files: set[str] = set()
    for evidence in unit.get("evidence", []):
        file_path = evidence.get("file") if isinstance(evidence, dict) else None
        if not isinstance(file_path, str):
            continue
        relative_file = PurePosixPath(file_path)
        if relative_file.is_absolute() or ".." in relative_file.parts:
            continue
        files.add((PurePosixPath(relative_service_root) / relative_file).as_posix())
    return sorted(files)


def _is_within(path: str, root: str) -> bool:
    return path == root or path.startswith(f"{root}/")


def _public_error_contracts_at_risk(
    changed_files: list[str], public_error_contracts: list[dict], relative_roots: dict[str, str | None],
) -> list[dict]:
    """Return indexed public contracts whose source file changed in the Git diff.

    File-level Git evidence cannot establish whether a status or public code itself
    changed. These are intentionally advisory candidates for reindexing and
    compatibility validation, not asserted contract breaks.
    """
    changed = set(changed_files)
    at_risk: list[dict] = []
    seen: set[tuple[str, str, str, str | None, str | None, str]] = set()
    for contract in public_error_contracts:
        service = contract.get("service")
        source = contract.get("source")
        protocol = contract.get("protocol")
        role = contract.get("role")
        transport_code = contract.get("transport_code")
        public_code = contract.get("public_code")
        file_path = contract.get("file_path")
        relative_root = relative_roots.get(service)
        if (
            not isinstance(service, str) or not isinstance(source, str)
            or protocol not in {"http", "grpc", "graphql"} or role not in {"handles", "maps", "raises"}
            or not isinstance(file_path, str) or relative_root is None
            or not isinstance(transport_code, str) and not isinstance(public_code, str)
        ):
            continue
        relative_file = PurePosixPath(file_path)
        if relative_file.is_absolute() or ".." in relative_file.parts:
            continue
        changed_file = (PurePosixPath(relative_root) / relative_file).as_posix()
        if changed_file not in changed:
            continue
        key = service, source, protocol, transport_code, public_code, changed_file
        if key in seen:
            continue
        seen.add(key)
        at_risk.append({
            "service": service,
            "symbol": source,
            "protocol": protocol,
            "transport_code": transport_code,
            "public_code": public_code,
            "changed_file": changed_file,
            "evidence": [{
                "file": file_path,
                "start_line": contract.get("start_line"),
                "end_line": contract.get("end_line"),
            }],
        })
    return sorted(
        at_risk,
        key=lambda item: (
            item["service"], item["symbol"], item["protocol"], item["transport_code"] or "",
            item["public_code"] or "", item["changed_file"],
        ),
    )
