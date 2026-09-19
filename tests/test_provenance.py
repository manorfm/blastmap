"""TDD coverage for generation.provenance: makes explicit whether a piece of
information is a deterministically extracted fact or an LLM-synthesized
interpretation — formalizing a distinction the schema already carries implicitly
via confidence being null or not."""
from impactmesh.generation.provenance import infer_provenance


def test_a_confidence_value_means_llm_sourced():
    assert infer_provenance(0.9) == {"source": "llm"}


def test_zero_confidence_still_counts_as_llm_sourced():
    assert infer_provenance(0.0) == {"source": "llm"}


def test_no_confidence_means_deterministic():
    assert infer_provenance(None) == {"source": "deterministic"}
