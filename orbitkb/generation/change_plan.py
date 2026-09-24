"""Deterministic decisions that gate an evidence-backed change plan."""
from __future__ import annotations


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
