"""Small, evidence-led architectural smells for individual entrypoint flows."""
from __future__ import annotations

from collections.abc import Mapping, Sequence


def find_entrypoint_smells(entrypoint: Mapping, edges: Sequence[Mapping]) -> list[dict]:
    """Return hypotheses, never a categorical BFF classification.

    A GraphQL mutation is not inherently a BFF. The signal only appears when its
    direct deterministic flow writes state or emits an event, which warrants a
    human check for reusable policy leaking into a consumer-facing facade.
    """
    if entrypoint["kind"] != "graphql":
        return []
    findings = []
    for edge in edges:
        if edge["kind"] not in {"writes", "publishes"}:
            continue
        findings.append(
            {
                "kind": "possible_bff_domain_leakage",
                "severity": "warning",
                "reason": (
                    "GraphQL entrypoint directly writes domain state; validate whether this service is a BFF and "
                    "move reusable domain policy behind a domain service."
                ),
                "evidence_target": edge["to_symbol"],
            }
        )
    return findings
