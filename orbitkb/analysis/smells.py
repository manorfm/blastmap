"""Small, evidence-led architectural smells for individual entrypoint flows."""
from __future__ import annotations

from collections.abc import Mapping, Sequence


def find_entrypoint_smells(entrypoint: Mapping, edges: Sequence[Mapping]) -> list[dict]:
    """Return hypotheses, never a categorical BFF classification.

    A GraphQL mutation is not inherently a BFF. The signal only appears when its
    direct deterministic flow writes state or emits an event, which warrants a
    human check for reusable policy leaking into a consumer-facing facade.
    """
    findings = []
    writes = [edge["to_symbol"] for edge in edges if edge["kind"] == "writes"]
    publishes = [edge["to_symbol"] for edge in edges if edge["kind"] == "publishes"]
    if writes and publishes:
        findings.append(
            {
                "kind": "possible_non_atomic_publish",
                "severity": "warning",
                "reason": "Flow writes state and publishes an event; validate a transaction boundary or transactional outbox.",
                "evidence_targets": [writes[0], publishes[0]],
            }
        )
    if entrypoint["kind"] != "graphql":
        return findings
    for target in [*writes, *publishes]:
        findings.append(
            {
                "kind": "possible_bff_domain_leakage",
                "severity": "warning",
                "reason": (
                    "GraphQL entrypoint directly writes domain state; validate whether this service is a BFF and "
                    "move reusable domain policy behind a domain service."
                ),
                "evidence_target": target,
            }
        )
    return findings
