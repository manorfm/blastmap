from collections.abc import Iterable

_CONTAINER_ROLE_ORDER = {"initialization": 0, "application": 1}


def ordered_kubernetes_workloads(
    records: Iterable[tuple[object, object, object, object, object, object, object, object]],
    max_evidence: int | None = None,
) -> list[dict]:
    """Return unique workload scopes, optionally bounding evidence per scope."""
    scopes: dict[tuple[str, str, str, str | None], tuple[set[str | None], set[tuple[str, int, int]]]] = {}
    for workload_kind, workload_name, container_name, container_role, prefix, file_path, start_line, end_line in records:
        if not all(isinstance(value, str) and value for value in (
            workload_kind, workload_name, container_name,
        )):
            continue
        role = (
            container_role
            if isinstance(container_role, str) and container_role in _CONTAINER_ROLE_ORDER
            else None
        )
        prefixes, evidence = scopes.setdefault(
            (workload_kind, workload_name, container_name, role), (set(), set()),
        )
        if prefix is None or isinstance(prefix, str):
            prefixes.add(prefix)
        if isinstance(file_path, str) and isinstance(start_line, int) and isinstance(end_line, int):
            evidence.add((file_path, start_line, end_line))
    evidence_limit = max_evidence if isinstance(max_evidence, int) and max_evidence > 0 else None
    return [
        {
            "kind": workload_kind, "name": workload_name, "container": container_name,
            **({"container_role": container_role} if container_role is not None else {}),
            **({"prefixes": sorted(prefixes - {None})} if prefixes - {None} else {}),
            **({"includes_unprefixed_import": True} if None in prefixes else {}),
            **({
                "evidence": [
                    {"file": file_path, "start_line": start_line, "end_line": end_line}
                    for file_path, start_line, end_line in sorted(evidence)[:evidence_limit]
                ],
            } if evidence else {}),
            **({"evidence_truncated": True} if evidence_limit is not None and len(evidence) > evidence_limit else {}),
            **({"evidence_total": len(evidence)} if evidence_limit is not None and len(evidence) > evidence_limit else {}),
        }
        for (workload_kind, workload_name, container_name, container_role), (prefixes, evidence) in sorted(
            scopes.items(),
            key=lambda scope: (
                _CONTAINER_ROLE_ORDER.get(scope[0][3], len(_CONTAINER_ROLE_ORDER)),
                scope[0][0], scope[0][1], scope[0][2],
            ),
        )
    ]
