from collections.abc import Iterable

_CONTAINER_ROLE_ORDER = {"initialization": 0, "application": 1}


def ordered_kubernetes_workloads(
    records: Iterable[tuple[object, object, object, object]],
) -> list[dict]:
    """Return unique, lifecycle-ordered workload scopes from proven import facts."""
    scopes: set[tuple[str, str, str, str | None]] = set()
    for workload_kind, workload_name, container_name, container_role in records:
        if not all(isinstance(value, str) and value for value in (
            workload_kind, workload_name, container_name,
        )):
            continue
        role = (
            container_role
            if isinstance(container_role, str) and container_role in _CONTAINER_ROLE_ORDER
            else None
        )
        scopes.add((workload_kind, workload_name, container_name, role))
    return [
        {
            "kind": workload_kind, "name": workload_name, "container": container_name,
            **({"container_role": container_role} if container_role is not None else {}),
        }
        for workload_kind, workload_name, container_name, container_role in sorted(
            scopes,
            key=lambda scope: (
                _CONTAINER_ROLE_ORDER.get(scope[3], len(_CONTAINER_ROLE_ORDER)),
                scope[0], scope[1], scope[2],
            ),
        )
    ]
