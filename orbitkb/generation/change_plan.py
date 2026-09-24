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
