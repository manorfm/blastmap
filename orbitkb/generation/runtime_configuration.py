from collections.abc import Iterable

_CONTAINER_ROLE_ORDER = {"initialization": 0, "application": 1}


def ordered_kubernetes_workloads(
    records: Iterable[tuple[object, object, object, object, object]],
) -> list[dict]:
    """Return unique, lifecycle-ordered workload scopes from proven import facts."""
    scopes: dict[tuple[str, str, str, str | None], set[str | None]] = {}
    for workload_kind, workload_name, container_name, container_role, prefix in records:
        if not all(isinstance(value, str) and value for value in (
            workload_kind, workload_name, container_name,
        )):
            continue
        role = (
            container_role
            if isinstance(container_role, str) and container_role in _CONTAINER_ROLE_ORDER
            else None
        )
        prefixes = scopes.setdefault((workload_kind, workload_name, container_name, role), set())
        if prefix is None or isinstance(prefix, str):
            prefixes.add(prefix)
    return [
        {
            "kind": workload_kind, "name": workload_name, "container": container_name,
            **({"container_role": container_role} if container_role is not None else {}),
            **({"prefixes": sorted(prefixes - {None})} if prefixes - {None} else {}),
            **({"includes_unprefixed_import": True} if None in prefixes else {}),
        }
        for (workload_kind, workload_name, container_name, container_role), prefixes in sorted(
            scopes.items(),
            key=lambda scope: (
                _CONTAINER_ROLE_ORDER.get(scope[0][3], len(_CONTAINER_ROLE_ORDER)),
                scope[0][0], scope[0][1], scope[0][2],
            ),
        )
    ]
