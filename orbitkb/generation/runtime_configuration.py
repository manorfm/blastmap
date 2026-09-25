from collections.abc import Iterable

_CONTAINER_ROLE_ORDER = {"initialization": 0, "application": 1}


def ordered_kubernetes_workloads(
    records: Iterable[tuple[object, object, object, object, object, object, object, object]],
) -> list[dict]:
    """Return unique, lifecycle-ordered workload scopes from proven import facts."""
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
    return [
        {
            "kind": workload_kind, "name": workload_name, "container": container_name,
            **({"container_role": container_role} if container_role is not None else {}),
            **({"prefixes": sorted(prefixes - {None})} if prefixes - {None} else {}),
            **({"includes_unprefixed_import": True} if None in prefixes else {}),
            **({
                "evidence": [
                    {"file": file_path, "start_line": start_line, "end_line": end_line}
                    for file_path, start_line, end_line in sorted(evidence)
                ],
            } if evidence else {}),
        }
        for (workload_kind, workload_name, container_name, container_role), (prefixes, evidence) in sorted(
            scopes.items(),
            key=lambda scope: (
                _CONTAINER_ROLE_ORDER.get(scope[0][3], len(_CONTAINER_ROLE_ORDER)),
                scope[0][0], scope[0][1], scope[0][2],
            ),
        )
    ]
