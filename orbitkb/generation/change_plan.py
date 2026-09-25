"""Deterministic decisions that gate an evidence-backed change plan."""
from __future__ import annotations

from orbitkb.generation.runtime_configuration import ordered_kubernetes_workloads


def derive_decision_points(contracts_at_risk: list[dict], primary_services: set[str]) -> list[dict]:
    """Require an explicit compatibility choice for affected primary event contracts.

    A service-level task match alone is not enough to require a decision. The
    contract must be published by a primary candidate and have at least one indexed
    consumer, so choosing a breaking evolution would materially expand the work.
    """
    decisions: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for contract in contracts_at_risk:
        producer = contract.get("producer")
        name = contract.get("contract")
        consumers = sorted(set(contract.get("consumers", [])))
        if not isinstance(producer, str) or not isinstance(name, str) or producer not in primary_services or not consumers:
            continue
        key = producer, name
        if key in seen:
            continue
        seen.add(key)
        consumer_list = ", ".join(consumers)
        plural = "consume" if len(consumers) > 1 else "consumes"
        decisions.append({
            "id": f"event-compatibility:{producer}:{name}",
            "question": f"Will the {name} event payload or compatibility change?",
            "why_blocking": (
                f"{consumer_list} {plural} this event; compatibility determines whether it must change."
            ),
            "options": [
                "preserve backward compatibility",
                "version the event contract and update consumers",
            ],
            "recommended_default": "preserve backward compatibility unless a versioned rollout is approved",
            "owner": producer,
            "contract": name,
            "consumers": consumers,
            "evidence": contract.get("evidence", []),
        })
    return decisions


def validate_decision_selections(
    decision_points: list[dict], selections: object,
) -> tuple[list[dict] | None, str | None]:
    """Validate one explicit, supported selection for every pending decision."""
    if not isinstance(selections, list):
        return None, "decisions must be a list"
    expected = {decision["id"]: decision for decision in decision_points}
    chosen: dict[str, str] = {}
    for selection in selections:
        if not isinstance(selection, dict) or set(selection) != {"id", "option"}:
            return None, "each decision selection must contain only id and option"
        decision_id = selection["id"]
        option = selection["option"]
        if not isinstance(decision_id, str) or not isinstance(option, str):
            return None, "decision id and option must be strings"
        if decision_id in chosen:
            return None, f"decision selected more than once: {decision_id}"
        decision = expected.get(decision_id)
        if decision is None:
            return None, f"unknown decision: {decision_id}"
        if option not in decision["options"]:
            return None, f"unsupported option for decision: {decision_id}"
        chosen[decision_id] = option
    missing = sorted(set(expected) - set(chosen))
    if missing:
        return None, f"missing decisions: {', '.join(missing)}"
    return [{"id": decision["id"], "option": chosen[decision["id"]]} for decision in decision_points], None


def derive_change_units(decision_points: list[dict], selections: list[dict]) -> list[dict]:
    """Create source-bounded event-contract work after compatibility is selected."""
    decisions = {decision["id"]: decision for decision in decision_points}
    units: list[dict] = []
    for selection in selections:
        decision = decisions[selection["id"]]
        contract = decision.get("contract")
        consumers = decision.get("consumers")
        if not isinstance(contract, str) or not isinstance(consumers, list):
            continue
        preserve = selection["option"] == "preserve backward compatibility"
        action = "validate" if preserve else "modify"
        action_text = "preserve backward compatibility" if preserve else "version the event contract"
        units.append({
            "id": f"event-contract:{decision['owner']}:{contract}",
            "service": decision["owner"],
            "target": {
                "role": "contract",
                "symbol": f"message.publish:{contract}",
                "evidence": decision["evidence"],
            },
            "action": action,
            "reason": f"{action_text} for {contract} before changing its producer.",
            "preconditions": [f"{decision['id']}={selection['option']}"],
            "related_contracts": [contract],
            "dependencies": consumers,
            "validation": [f"verify {contract} remains compatible with {consumer}" for consumer in consumers],
            "confidence": 1.0,
            "evidence": decision["evidence"],
        })
    return units


def derive_error_mapping_review_units(findings: list[dict], primary_services: set[str]) -> list[dict]:
    """Turn only a proven local 4xx-to-5xx degradation into a review unit.

    Architecture findings use ``possible_`` because middleware and gateway behavior
    remain unknown. This function preserves that uncertainty by creating a review,
    never an automatic code change, and requires the detector's high-confidence,
    source-backed intra-service evidence.
    """
    units: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for finding in findings:
        if finding.get("kind") != "possible_error_semantics_lost":
            continue
        services = finding.get("services")
        detail = finding.get("detail")
        confidence = finding.get("confidence")
        if (
            not isinstance(services, list) or len(services) != 1 or services[0] not in primary_services
            or not isinstance(detail, dict) or not isinstance(confidence, (int, float)) or confidence < 0.8
        ):
            continue
        error_type = detail.get("error_type")
        mapping = detail.get("mapping")
        evidence = detail.get("evidence")
        if (
            not isinstance(error_type, str) or not isinstance(mapping, dict) or not isinstance(evidence, list)
            or not isinstance(mapping.get("symbol"), str) or not isinstance(mapping.get("code"), str)
            or not evidence
        ):
            continue
        service = services[0]
        mapping_symbol = mapping["symbol"]
        code = mapping["code"]
        key = service, error_type, mapping_symbol, code
        if key in seen:
            continue
        seen.add(key)
        units.append({
            "id": f"error-mapping:{service}:{error_type}:{mapping_symbol}:{code}",
            "service": service,
            "target": {"role": "error_mapping", "symbol": mapping_symbol, "evidence": evidence},
            "action": "review",
            "reason": finding.get("reason", "review the indexed error mapping"),
            "preconditions": [],
            "related_contracts": [f"error:{error_type}", f"HTTP {code}"],
            "dependencies": [],
            "validation": [
                f"verify {error_type} preserves a documented client-error response or document the HTTP {code} translation",
            ],
            "confidence": float(confidence),
            "evidence": evidence,
        })
    return units


def derive_persistence_migration_review_units(
    persistence_affected: list[dict], migration_facts_by_service: dict[str, list[dict]], primary_services: set[str],
) -> list[dict]:
    """Return bounded schema-review units only for exact persisted-table matches."""
    units: list[dict] = []
    for persisted in persistence_affected:
        service = persisted.get("service")
        table_name = persisted.get("entity")
        evidence = persisted.get("evidence")
        if (
            not isinstance(service, str) or service not in primary_services
            or not isinstance(table_name, str) or persisted.get("kind") != "sql_table"
            or not isinstance(evidence, list) or not evidence
        ):
            continue
        persistence_evidence = _unique_evidence(evidence)
        if not persistence_evidence:
            continue
        matching_facts = [
            fact
            for fact in migration_facts_by_service.get(service, [])
            if (
                isinstance(fact, dict)
                and isinstance(fact.get("table_name"), str)
                and fact["table_name"].casefold() == table_name.casefold()
                and isinstance(fact.get("file_path"), str)
                and isinstance(fact.get("start_line"), int)
                and isinstance(fact.get("end_line"), int)
            )
        ]
        if not matching_facts:
            continue
        migration_evidence = [{
            "file": fact["file_path"], "start_line": fact["start_line"], "end_line": fact["end_line"],
        } for fact in matching_facts]
        all_evidence = _unique_evidence([*persistence_evidence, *migration_evidence])
        if not all_evidence:
            continue
        count = len(matching_facts)
        operation_word = "operation" if count == 1 else "operations"
        validation = [
            f"review {table_name} schema and its {count} indexed migration {operation_word} before altering persistence",
        ]
        if any(bool(fact.get("destructive")) for fact in matching_facts):
            validation.append("verify deployment order, backup, and rollback for destructive migration operations")
        units.append({
            "id": f"persistence-migration:{service}:{table_name}",
            "service": service,
            "target": {"role": "persistence", "symbol": f"table:{table_name}", "evidence": all_evidence},
            "action": "review",
            "reason": (
                f"{table_name} is on the indexed change surface and has {count} source-proven migration "
                f"{operation_word}; review schema compatibility before altering it."
            ),
            "preconditions": [],
            "related_contracts": [f"database:{table_name}"],
            "dependencies": [],
            "validation": validation,
            "confidence": 1.0,
            "evidence": all_evidence,
        })
    return units


def derive_feature_flag_review_units(
    feature_flags_by_service: dict[str, list[dict]], primary_services: set[str],
) -> list[dict]:
    """Return one bounded review per source-proven flag read by a primary service.

    A static read proves only that code is guarded by a flag. It cannot prove the
    flag's runtime value, targeting, or rollout intent, so the resulting unit is
    always a review rather than a prescribed configuration change.
    """
    units: list[dict] = []
    for service in sorted(primary_services):
        grouped: dict[tuple[str, str], list[tuple[dict, object]]] = {}
        for flag in feature_flags_by_service.get(service, []):
            if not isinstance(flag, dict):
                continue
            provider = flag.get("provider")
            key = flag.get("key")
            if not isinstance(provider, str) or not provider or not isinstance(key, str) or not key:
                continue
            evidence = {
                "file": flag.get("file_path"),
                "start_line": flag.get("start_line"),
                "end_line": flag.get("end_line"),
            }
            if not _unique_evidence([evidence]):
                continue
            grouped.setdefault((provider, key), []).append(evidence)
        for (provider, key), evidence_items in sorted(grouped.items()):
            evidence = sorted(
                _unique_evidence(evidence_items), key=lambda item: (item["file"], item["start_line"], item["end_line"]),
            )
            location_count = len(evidence)
            location_word = "location" if location_count == 1 else "locations"
            units.append({
                "id": f"feature-flag:{service}:{provider}:{key}",
                "service": service,
                "target": {"role": "feature_flag", "symbol": f"{provider}:{key}", "evidence": evidence},
                "action": "review",
                "reason": (
                    f"{key} is read through {provider} at {location_count} indexed {location_word}; "
                    "review targeting, default behavior, and rollout if the guarded behavior changes."
                ),
                "preconditions": [],
                "related_contracts": [f"feature_flag:{provider}:{key}"],
                "dependencies": [],
                "validation": [
                    f"verify {key} targeting, default behavior, and rollout state match the requested change",
                    f"verify guarded code remains safe when {key} is disabled",
                ],
                "confidence": 1.0,
                "evidence": evidence,
            })
    return units


def derive_runtime_configuration_review_units(
    configuration_bindings_by_service: dict[str, list[dict]],
    runtime_bindings_by_service: dict[str, list[dict]],
    primary_services: set[str],
) -> list[dict]:
    """Return reviews only for exact code-to-Kubernetes environment-key matches.

    A matching variable name proves the indexed code and workload use the same
    configuration boundary. It cannot prove deployed values or whether a task
    changes that boundary, so this remains a review rather than a config edit.
    """
    units: list[dict] = []
    for service in sorted(primary_services):
        code_evidence_by_key: dict[str, list[dict]] = {}
        for binding in configuration_bindings_by_service.get(service, []):
            if not isinstance(binding, dict) or binding.get("kind") != "environment":
                continue
            key = binding.get("key")
            evidence = {
                "file": binding.get("file_path"),
                "start_line": binding.get("start_line"),
                "end_line": binding.get("end_line"),
            }
            if isinstance(key, str) and key and _unique_evidence([evidence]):
                code_evidence_by_key.setdefault(key, []).append(evidence)

        runtime_by_key: dict[str, list[dict]] = {}
        for binding in runtime_bindings_by_service.get(service, []):
            if not isinstance(binding, dict):
                continue
            key = binding.get("environment_key")
            source_kind = binding.get("source_kind")
            source_name = binding.get("source_name")
            source_key = binding.get("source_key")
            evidence = {
                "file": binding.get("file_path"),
                "start_line": binding.get("start_line"),
                "end_line": binding.get("end_line"),
            }
            if (
                isinstance(key, str) and key
                and all(isinstance(value, str) and value for value in (source_kind, source_name, source_key))
                and _unique_evidence([evidence])
            ):
                runtime_by_key.setdefault(key, []).append(binding)

        for key in sorted(set(code_evidence_by_key) & set(runtime_by_key)):
            code_evidence = _sorted_evidence(code_evidence_by_key[key])
            runtime_bindings = runtime_by_key[key]
            runtime_evidence = _sorted_evidence([{
                "file": binding["file_path"], "start_line": binding["start_line"], "end_line": binding["end_line"],
            } for binding in runtime_bindings])
            evidence = _sorted_evidence([*code_evidence, *runtime_evidence])
            if not code_evidence or not runtime_evidence:
                continue
            local_count = len(code_evidence)
            workload_count = len(runtime_evidence)
            local_word = "location" if local_count == 1 else "locations"
            workload_word = "workload" if workload_count == 1 else "workloads"
            source_contracts = sorted({
                f"configuration:{binding['source_kind']}:{binding['source_name']}:{binding['source_key']}"
                for binding in runtime_bindings
            })
            units.append({
                "id": f"runtime-configuration:{service}:{key}",
                "service": service,
                "target": {"role": "configuration", "symbol": f"environment:{key}", "evidence": evidence},
                "action": "review",
                "reason": (
                    f"{key} is read at {local_count} local {local_word} and bound to {workload_count} indexed "
                    f"Kubernetes {workload_word}; review both layers if its behavior changes."
                ),
                "preconditions": [],
                "related_contracts": [f"configuration:environment:{key}", *source_contracts],
                "dependencies": [],
                "validation": [
                    f"verify {key} remains compatible with its indexed ConfigMap or Secret source",
                    "verify indexed Kubernetes workload references remain valid during rollout",
                ],
                "confidence": 1.0,
                "evidence": evidence,
            })
    return units


def derive_runtime_configuration_mismatch_review_units(
    mismatches_by_service: dict[str, list[dict]], primary_services: set[str],
) -> list[dict]:
    """Turn persisted, source-proven Kubernetes key conflicts into reviews."""
    units: list[dict] = []
    for service in sorted(primary_services):
        grouped: dict[tuple[str, str, str, str], list[dict]] = {}
        for mismatch in mismatches_by_service.get(service, []):
            if not isinstance(mismatch, dict):
                continue
            environment_key = mismatch.get("environment_key")
            source_kind = mismatch.get("source_kind")
            source_name = mismatch.get("source_name")
            source_key = mismatch.get("source_key")
            reference = {
                "file": mismatch.get("reference_file_path"), "start_line": mismatch.get("reference_start_line"),
                "end_line": mismatch.get("reference_end_line"),
            }
            declaration = {
                "file": mismatch.get("declaration_file_path"), "start_line": mismatch.get("declaration_start_line"),
                "end_line": mismatch.get("declaration_end_line"),
            }
            if (
                all(isinstance(value, str) and value for value in (environment_key, source_kind, source_name, source_key))
                and _unique_evidence([reference]) and _unique_evidence([declaration])
            ):
                grouped.setdefault((source_kind, source_name, source_key, environment_key), []).append({
                    "reference": reference, "declaration": declaration,
                })
        for (source_kind, source_name, source_key, environment_key), occurrences in sorted(grouped.items()):
            evidence = _sorted_evidence([
                evidence_item
                for occurrence in occurrences
                for evidence_item in (occurrence["reference"], occurrence["declaration"])
            ])
            source_label = "ConfigMap" if source_kind == "config_map" else "Secret"
            units.append({
                "id": (
                    f"runtime-configuration-mismatch:{service}:{source_kind}:{source_name}:{source_key}:"
                    f"{environment_key}"
                ),
                "service": service,
                "target": {
                    "role": "configuration", "symbol": f"kubernetes:{source_kind}:{source_name}:{source_key}",
                    "evidence": evidence,
                },
                "action": "review",
                "reason": (
                    f"{environment_key} references {source_label} {source_name} key {source_key}, but its single "
                    "indexed declaration does not list that key."
                ),
                "preconditions": [],
                "related_contracts": [
                    f"configuration:environment:{environment_key}",
                    f"configuration:{source_kind}:{source_name}:{source_key}",
                ],
                "dependencies": [],
                "validation": [
                    f"verify the {source_label} declaration or workload reference is corrected before rollout",
                    f"verify behavior remains safe when {environment_key} is unavailable",
                ],
                "confidence": 0.9,
                "evidence": evidence,
            })
    return units


def derive_runtime_configuration_source_unknown_review_units(
    unknowns_by_service: dict[str, list[dict]], primary_services: set[str],
) -> list[dict]:
    """Turn possible external Kubernetes configuration dependencies into reviews.

    A missing local declaration does not prove a deployment problem: the source may
    belong to another repository or delivery boundary. The review keeps that human
    ownership decision explicit without prescribing a manifest change.
    """
    units: list[dict] = []
    for service in sorted(primary_services):
        grouped: dict[tuple[str, str, str, str], list[dict]] = {}
        for unknown in unknowns_by_service.get(service, []):
            if not isinstance(unknown, dict):
                continue
            environment_key = unknown.get("environment_key")
            source_kind = unknown.get("source_kind")
            source_name = unknown.get("source_name")
            source_key = unknown.get("source_key")
            reference = {
                "file": unknown.get("reference_file_path"), "start_line": unknown.get("reference_start_line"),
                "end_line": unknown.get("reference_end_line"),
            }
            if (
                all(isinstance(value, str) and value for value in (environment_key, source_kind, source_name, source_key))
                and _unique_evidence([reference])
            ):
                grouped.setdefault((source_kind, source_name, source_key, environment_key), []).append(reference)
        for (source_kind, source_name, source_key, environment_key), references in sorted(grouped.items()):
            evidence = _sorted_evidence(references)
            source_label = "ConfigMap" if source_kind == "config_map" else "Secret"
            units.append({
                "id": (
                    f"runtime-configuration-source-unknown:{service}:{source_kind}:{source_name}:{source_key}:"
                    f"{environment_key}"
                ),
                "service": service,
                "target": {
                    "role": "configuration", "symbol": f"kubernetes:{source_kind}:{source_name}:{source_key}",
                    "evidence": evidence,
                },
                "action": "review",
                "reason": (
                    f"{environment_key} references {source_label} {source_name} key {source_key}, but no matching "
                    "local declaration was indexed."
                ),
                "preconditions": [],
                "related_contracts": [
                    f"configuration:environment:{environment_key}",
                    f"configuration:{source_kind}:{source_name}:{source_key}",
                ],
                "dependencies": [],
                "validation": [
                    f"confirm the owning repository, chart, controller, or deployment process for {source_label} {source_name}",
                    f"verify {environment_key} remains available and compatible throughout rollout",
                ],
                "confidence": 0.4,
                "evidence": evidence,
            })
    return units


def derive_runtime_configuration_source_import_unknown_review_units(
    unknowns_by_service: dict[str, list[dict]], primary_services: set[str],
) -> list[dict]:
    """Turn unresolved ``envFrom`` sources into ownership reviews.

    The same source can be imported with distinct prefixes, but source ownership is
    one decision. Grouping by source preserves every reference as evidence while
    avoiding duplicate units and never inventing imported key names.
    """
    units: list[dict] = []
    for service in sorted(primary_services):
        grouped: dict[tuple[str, str], list[dict]] = {}
        for unknown in unknowns_by_service.get(service, []):
            if not isinstance(unknown, dict):
                continue
            source_kind = unknown.get("source_kind")
            source_name = unknown.get("source_name")
            reference = {
                "file": unknown.get("reference_file_path"), "start_line": unknown.get("reference_start_line"),
                "end_line": unknown.get("reference_end_line"),
            }
            if (
                all(isinstance(value, str) and value for value in (source_kind, source_name))
                and _unique_evidence([reference])
            ):
                grouped.setdefault((source_kind, source_name), []).append((
                    reference, unknown.get("optional"), unknown.get("container_role"), unknown.get("workload_kind"),
                    unknown.get("workload_name"), unknown.get("container_name"),
                ))
        for (source_kind, source_name), records in sorted(grouped.items()):
            evidence = _sorted_evidence([
                reference for reference, _optional, _container_role, _workload_kind, _workload_name, _container_name in records
            ])
            source_label = "ConfigMap" if source_kind == "config_map" else "Secret"
            availability_values = {
                None if optional is None else bool(optional)
                for _reference, optional, _container_role, _workload_kind, _workload_name, _container_name in records
            }
            container_roles = {
                container_role
                for _reference, _optional, container_role, _workload_kind, _workload_name, _container_name in records
                if container_role in {"application", "initialization"}
            }
            workloads = ordered_kubernetes_workloads(
                (workload_kind, workload_name, container_name, container_role)
                for _reference, _optional, container_role, workload_kind, workload_name, container_name in records
            )
            availability = (
                "optional" if availability_values == {True}
                else "required" if availability_values == {False}
                else "unknown" if availability_values == {None}
                else "mixed"
            )
            availability_validation = (
                f"verify requested behavior remains safe when the optional {source_label} {source_name} is unavailable"
                if availability == "optional"
                else "verify required configuration keys are supplied by the owning delivery boundary before rollout"
                if availability == "required"
                else "confirm source availability before relying on imported configuration during rollout"
            )
            validation = [
                f"confirm the owning repository, chart, controller, or deployment process for {source_label} {source_name}",
                availability_validation,
            ]
            if "initialization" in container_roles:
                validation.append("verify initialization completes before application containers start")
            target = {
                "role": "configuration", "symbol": f"kubernetes:{source_kind}:{source_name}", "evidence": evidence,
            }
            if workloads:
                target["workloads"] = workloads
            units.append({
                "id": f"runtime-configuration-source-import-unknown:{service}:{source_kind}:{source_name}",
                "service": service,
                "target": target,
                "action": "review",
                "reason": (
                    f"{service} imports keys from {source_label} {source_name} through envFrom, but no matching "
                    "local declaration was indexed."
                ),
                "preconditions": [],
                "related_contracts": [f"configuration:{source_kind}:{source_name}"],
                "dependencies": [],
                "validation": validation,
                "confidence": 0.4,
                "evidence": evidence,
            })
    return units


def _unique_evidence(items: list[dict]) -> list[dict]:
    seen: set[tuple[str, int, int]] = set()
    evidence: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        file_path = item.get("file")
        start_line = item.get("start_line")
        end_line = item.get("end_line")
        if not isinstance(file_path, str) or not isinstance(start_line, int) or not isinstance(end_line, int):
            continue
        key = file_path, start_line, end_line
        if key not in seen:
            seen.add(key)
            evidence.append({"file": file_path, "start_line": start_line, "end_line": end_line})
    return evidence


def _sorted_evidence(items: list[dict]) -> list[dict]:
    return sorted(
        _unique_evidence(items), key=lambda item: (item["file"], item["start_line"], item["end_line"]),
    )
