"""Deterministically compare persisted change units with a bounded Git file diff."""
from __future__ import annotations

from pathlib import Path, PurePosixPath


def assess_change_units(
    repository_root: Path,
    changed_files: list[str],
    change_units: list[dict],
    service_roots: dict[str, Path],
    public_error_contracts: list[dict],
    current_public_error_contracts: list[dict],
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
        "public_error_contract_breaks": _public_error_contract_breaks(
            normalized_changes, public_error_contracts, current_public_error_contracts, relative_roots,
        ),
    }


def summarize_change_closure(assessment: dict, ci_validation: dict, max_unit_ids: int = 3) -> dict:
    """Summarize advisory closure state without asserting deployment approval."""
    omitted_units = assessment["omitted_change_units"]
    unassessable_units = assessment["unassessable_change_units"]
    risks = {
        "files_outside_planned_surface": len(assessment["files_outside_planned_surface"]),
        "public_error_contracts_at_risk": len(assessment["public_error_contracts_at_risk"]),
        "public_error_contract_breaks": len(assessment["public_error_contract_breaks"]),
    }
    validation_status = ci_validation["status"]
    needs_attention = bool(omitted_units or risks["public_error_contract_breaks"] or validation_status == "failed")
    needs_review = bool(
        unassessable_units
        or risks["files_outside_planned_surface"]
        or risks["public_error_contracts_at_risk"]
        or validation_status in {"pending", "no_indexed_commands"}
    )
    return {
        "status": "needs_attention" if needs_attention else "needs_review" if needs_review else "ready_for_manual_review",
        "coverage": {
            "planned_units": len(assessment["pending_validation"]),
            "covered_units": len(assessment["covered_change_units"]),
            "omitted_units": len(omitted_units),
            "unassessable_units": len(unassessable_units),
        },
        "risks": risks,
        "ci_validation": {
            "status": validation_status,
            "summary": ci_validation["summary"],
        },
        "outstanding_change_units": {
            "omitted": [unit["id"] for unit in omitted_units[:max_unit_ids]],
            "unassessable": [unit["id"] for unit in unassessable_units[:max_unit_ids]],
        },
    }


def _relative_service_root(repository_root: Path, service_root: Path) -> str | None:
    try:
        return service_root.resolve().relative_to(repository_root.resolve()).as_posix()
    except ValueError:
        return None


def changed_files_touch_service_roots(
    repository_root: Path, changed_files: list[str], service_roots: dict[str, Path],
) -> bool:
    """Return whether a Git diff reaches any indexed service root in a plan."""
    relative_roots = {
        root
        for service_root in service_roots.values()
        if (root := _relative_service_root(repository_root, service_root)) is not None
    }
    if "." in relative_roots:
        return bool(changed_files)
    return any(
        changed_file == root or changed_file.startswith(f"{root}/")
        for changed_file in changed_files
        for root in relative_roots
    )


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


def _public_error_contract_breaks(
    changed_files: list[str],
    previous_contracts: list[dict],
    current_contracts: list[dict],
    relative_roots: dict[str, str | None],
) -> list[dict]:
    """Compare source-proven public error contracts in changed service files.

    A group must contain one indexed and one current contract for the same source,
    role and protocol. Multiple mappings are deliberately left at risk rather than
    paired heuristically, and removed mappings remain at risk because static absence
    could be unsupported syntax rather than a deliberate contract removal.
    """
    previous_groups = _public_contract_groups(previous_contracts)
    current_groups = _public_contract_groups(current_contracts)
    changed = set(changed_files)
    breaks: list[dict] = []
    for key in sorted(set(previous_groups) & set(current_groups)):
        previous = previous_groups[key]
        current = current_groups[key]
        if len(previous) != 1 or len(current) != 1:
            continue
        previous_contract = previous[0]
        current_contract = current[0]
        changed_file = _changed_contract_file(previous_contract, changed, relative_roots)
        if changed_file is None or _changed_contract_file(current_contract, changed, relative_roots) is None:
            continue
        previous_values = _public_contract_values(previous_contract)
        current_values = _public_contract_values(current_contract)
        if previous_values == current_values:
            continue
        evidence = _contract_evidence(previous_contract) + _contract_evidence(current_contract)
        breaks.append({
            "service": previous_contract["service"],
            "symbol": previous_contract["source"],
            "protocol": previous_contract["protocol"],
            "previous": previous_values,
            "current": current_values,
            "changed_file": changed_file,
            "evidence": _distinct_evidence(evidence),
        })
    return breaks


def _public_contract_groups(contracts: list[dict]) -> dict[tuple[str, str, str, str], list[dict]]:
    groups: dict[tuple[str, str, str, str], list[dict]] = {}
    for contract in contracts:
        if not _is_public_contract(contract):
            continue
        key = contract["service"], contract["source"], contract["role"], contract["protocol"]
        groups.setdefault(key, []).append(contract)
    return groups


def _is_public_contract(contract: dict) -> bool:
    return (
        isinstance(contract.get("service"), str) and isinstance(contract.get("source"), str)
        and contract.get("protocol") in {"http", "grpc", "graphql"}
        and contract.get("role") in {"handles", "maps", "raises"}
        and (isinstance(contract.get("transport_code"), str) or isinstance(contract.get("public_code"), str))
        and isinstance(contract.get("file_path"), str)
    )


def _changed_contract_file(
    contract: dict, changed_files: set[str], relative_roots: dict[str, str | None],
) -> str | None:
    service = contract.get("service")
    file_path = contract.get("file_path")
    relative_root = relative_roots.get(service)
    if not isinstance(file_path, str) or relative_root is None:
        return None
    relative_file = PurePosixPath(file_path)
    if relative_file.is_absolute() or ".." in relative_file.parts:
        return None
    changed_file = (PurePosixPath(relative_root) / relative_file).as_posix()
    return changed_file if changed_file in changed_files else None


def _public_contract_values(contract: dict) -> dict:
    return {
        "transport_code": contract.get("transport_code"),
        "public_code": contract.get("public_code"),
    }


def _contract_evidence(contract: dict) -> list[dict]:
    file_path = contract.get("file_path")
    start_line = contract.get("start_line")
    end_line = contract.get("end_line")
    if not isinstance(file_path, str) or not isinstance(start_line, int) or not isinstance(end_line, int):
        return []
    return [{"file": file_path, "start_line": start_line, "end_line": end_line}]


def _distinct_evidence(evidence: list[dict]) -> list[dict]:
    seen: set[tuple[str, int, int]] = set()
    result: list[dict] = []
    for item in evidence:
        key = item["file"], item["start_line"], item["end_line"]
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
