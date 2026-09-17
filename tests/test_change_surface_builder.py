"""TDD coverage for ChangeSurfaceBuilder, which replaced the flat dict assembled
inline in analyze_change_surface (see generation/change_surface.py)."""
from blastmap.generation.change_surface import ChangeSurfaceBuilder


def test_build_emits_empty_lists_by_default():
    result = ChangeSurfaceBuilder().build()

    assert result == {
        "primary": [],
        "secondary": [],
        "no_change_hint": [],
        "flow": [],
        "external_integrations": [],
        "unmapped_internal_hint": [],
        "freshness": {},
        "unknowns": [],
    }


def test_build_omits_note_when_not_set():
    result = ChangeSurfaceBuilder().build()
    assert "note" not in result


def test_with_note_includes_it_in_the_built_result():
    result = ChangeSurfaceBuilder().with_note("no indexed service matched this task").build()
    assert result["note"] == "no indexed service matched this task"


def test_with_findings_sets_primary_secondary_and_no_change_hint():
    primary = [{"service": "checkout-service", "reason": "r", "confidence": 0.9, "evidence": []}]
    secondary = [{"service": "order-service", "reason": "r2", "confidence": 0.4, "evidence": []}]
    no_change = [{"service": "notification-service", "reason": "r3", "confidence": 0.8, "evidence": []}]

    result = ChangeSurfaceBuilder().with_findings(primary, secondary, no_change).build()

    assert result["primary"] == primary
    assert result["secondary"] == secondary
    assert result["no_change_hint"] == no_change


def test_builder_methods_are_chainable():
    result = (
        ChangeSurfaceBuilder()
        .with_findings([], [], [])
        .with_flow([{"from": "a", "to": "b", "type": "HTTP"}])
        .with_external_integrations([{"service": "Stripe API"}])
        .with_unmapped_internal_hint([{"service": "fraud-service"}])
        .with_freshness({"a": {"stale": False}})
        .build()
    )

    assert result["flow"] == [{"from": "a", "to": "b", "type": "HTTP"}]
    assert result["external_integrations"] == [{"service": "Stripe API"}]
    assert result["unmapped_internal_hint"] == [{"service": "fraud-service"}]
    assert result["freshness"] == {"a": {"stale": False}}
