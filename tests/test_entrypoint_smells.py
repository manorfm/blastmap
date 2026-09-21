from orbitkb.analysis.smells import find_entrypoint_smells


def test_graphql_gateway_with_direct_write_is_a_bff_leakage_hypothesis():
    findings = find_entrypoint_smells(
        {"kind": "graphql", "method": "MUTATION", "name": "createOrder", "symbol": "Mutation.createOrder"},
        [{"kind": "writes", "to_symbol": "orderRepository.save"}],
    )

    assert findings == [
        {
            "kind": "possible_bff_domain_leakage",
            "severity": "warning",
            "reason": "GraphQL entrypoint directly writes domain state; validate whether this service is a BFF and move reusable domain policy behind a domain service.",
            "evidence_target": "orderRepository.save",
        }
    ]


def test_graphql_read_composition_is_not_flagged_as_bff_leakage():
    assert find_entrypoint_smells(
        {"kind": "graphql", "method": "QUERY", "name": "order", "symbol": "Query.order"},
        [{"kind": "reads", "to_symbol": "ordersClient.get"}],
    ) == []


def test_write_and_publish_in_one_flow_flags_a_possible_missing_outbox_boundary():
    findings = find_entrypoint_smells(
        {"kind": "http", "method": "POST", "name": "/orders", "symbol": "OrdersController.create"},
        [
            {"kind": "writes", "to_symbol": "orderRepository.save"},
            {"kind": "publishes", "to_symbol": "eventPublisher.publish"},
        ],
    )

    assert findings == [{
        "kind": "possible_non_atomic_publish",
        "severity": "warning",
        "reason": "Flow writes state and publishes an event; validate a transaction boundary or transactional outbox.",
        "evidence_targets": ["orderRepository.save", "eventPublisher.publish"],
    }]
