"""Contract coverage for static feature flags exposed to agents."""

from orbitkb.analysis.models import AnalysisResult, Evidence, FeatureFlag
from orbitkb.db.connection import open_db
from orbitkb.db.repositories import flows, services
from orbitkb.mcp import queries


def test_describe_feature_flags_returns_paginated_source_proven_flags(tmp_path):
    conn = open_db(tmp_path / "feature-flags.db")
    service_id = services.ensure_service(conn, "checkout", "/repos/checkout", "node-ts")
    flows.replace_analysis(conn, service_id, AnalysisResult(feature_flags=[
        FeatureFlag(
            "checkout.checkout", "checkout.new-payment-flow", "launchdarkly",
            Evidence("checkout.ts", 8, 8),
        ),
        FeatureFlag(
            "checkout.checkout", "checkout.button-style", "launchdarkly",
            Evidence("checkout.ts", 9, 9),
        ),
    ]))

    result = queries.describe_feature_flags(conn, "checkout", limit=1, offset=1)

    assert result == {
        "service": "checkout",
        "repository": None,
        "flags": [{
            "source": "checkout.checkout",
            "key": "checkout.new-payment-flow",
            "provider": "launchdarkly",
            "evidence": {"file": "checkout.ts", "start_line": 8, "end_line": 8},
        }],
        "total": 2,
        "truncated": False,
    }
