from orbitkb.analysis.models import AnalysisResult, CloudFact, Evidence


def _fact(target_name: str | None = "orders-queue") -> CloudFact:
    return CloudFact(
        provider="aws",
        resource_type="queue",
        service_name="sqs",
        operation="SendMessage",
        operation_kind="publish",
        sdk="aws-sdk-js-v3",
        target_name=target_name,
        evidence=Evidence(file_path="orders.ts", start_line=10, end_line=12),
    )


def test_cloud_fact_holds_target_name_when_proven():
    fact = _fact()
    assert fact.target_name == "orders-queue"


def test_cloud_fact_target_name_is_none_when_unresolved():
    fact = _fact(target_name=None)
    assert fact.target_name is None


def test_analysis_result_extend_merges_cloud_facts():
    result = AnalysisResult(cloud_facts=[_fact("a")])
    other = AnalysisResult(cloud_facts=[_fact("b")])
    result.extend(other)
    assert [f.target_name for f in result.cloud_facts] == ["a", "b"]
