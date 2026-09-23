"""WP12: a cloud SDK call now also produces a real FlowEdge, sourced from the
function that contains it — the same way GORM/Mongoose/Prisma calls already
do — not just a standalone CloudFact. Exercised through StaticAnalysisEngine
since the integration lives inside each language's own per-function walk,
not a standalone function anymore.
"""
from pathlib import Path

from orbitkb.analysis.engine import StaticAnalysisEngine


def test_go_sqs_call_produces_both_a_cloud_fact_and_a_flow_edge(tmp_path: Path):
    (tmp_path / "publisher.go").write_text(
        "package publisher\n\n"
        'import "github.com/aws/aws-sdk-go-v2/service/sqs"\n\n'
        "func Publish(client *sqs.Client, body string) {\n"
        "\tclient.SendMessage(ctx, &sqs.SendMessageInput{})\n"
        "}\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "go")

    assert len(result.cloud_facts) == 1
    assert result.cloud_facts[0].service_name == "sqs"
    cloud_edges = [e for e in result.edges if e.kind == "publishes"]
    assert len(cloud_edges) == 1
    assert cloud_edges[0].source == "publisher.Publish"
    assert cloud_edges[0].target == "client.SendMessage"


def test_go_sqs_call_through_a_struct_field_via_its_method_receiver_is_detected(tmp_path: Path):
    """Found via WP16's real manual index/MCP verification: a client stored
    as a struct field (the idiomatic Go dependency-injection shape) is
    accessed through its method receiver (`h.sqsClient.SendMessage(...)`),
    not bare -- go_client_declarations' key is still the bare field name
    ("sqsClient"), so the receiver-qualified call site didn't match at all
    before cloud_edge_kind_and_fact's fallback to the last dotted segment."""
    (tmp_path / "publisher.go").write_text(
        "package publisher\n\n"
        'import "github.com/aws/aws-sdk-go-v2/service/sqs"\n\n'
        "type OrderHandler struct {\n"
        "\tsqsClient *sqs.Client\n"
        "}\n\n"
        "func (h *OrderHandler) CreateOrder(body string) error {\n"
        "\t_, err := h.sqsClient.SendMessage(ctx, &sqs.SendMessageInput{})\n"
        "\treturn err\n"
        "}\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "go")

    assert len(result.cloud_facts) == 1
    assert result.cloud_facts[0].service_name == "sqs"
    cloud_edges = [e for e in result.edges if e.target == "h.sqsClient.SendMessage"]
    assert len(cloud_edges) == 1
    assert cloud_edges[0].source == "OrderHandler.CreateOrder"
    assert cloud_edges[0].kind == "publishes"


def test_go_sqs_calls_in_two_different_functions_do_not_cross_leak(tmp_path: Path):
    (tmp_path / "publisher.go").write_text(
        "package publisher\n\n"
        'import "github.com/aws/aws-sdk-go-v2/service/sqs"\n\n'
        "func PublishOrders(client *sqs.Client) {\n"
        "\tclient.SendMessage(ctx, &sqs.SendMessageInput{})\n"
        "}\n\n"
        "func PublishInvoices(client *sqs.Client) {\n"
        "\tclient.SendMessage(ctx, &sqs.SendMessageInput{})\n"
        "}\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "go")

    assert len(result.cloud_facts) == 2
    sources = sorted(e.source for e in result.edges if e.kind == "publishes")
    assert sources == ["publisher.PublishInvoices", "publisher.PublishOrders"]


def test_java_sqs_call_produces_both_a_cloud_fact_and_a_flow_edge(tmp_path: Path):
    (tmp_path / "OrderPublisher.java").write_text(
        "import software.amazon.awssdk.services.sqs.SqsClient;\n\n"
        "public class OrderPublisher {\n"
        "    private final SqsClient sqsClient;\n\n"
        "    void publish(String body) {\n"
        "        sqsClient.sendMessage(SendMessageRequest.builder().build());\n"
        "    }\n"
        "}\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert len(result.cloud_facts) == 1
    assert result.cloud_facts[0].service_name == "sqs"
    # Filtering by target specifically, not just kind == "publishes": the base
    # _call_kind fallback already name-matches "sendmessage" as a substring of
    # *any* unrelated call (e.g. SendMessageRequest.builder()), independent of
    # this integration — a separate, pre-existing heuristic outside this WP's
    # scope, not something this test should assert away.
    sqs_edges = [e for e in result.edges if e.target == "sqsClient.sendMessage"]
    assert len(sqs_edges) == 1
    assert sqs_edges[0].source == "OrderPublisher.publish"
    assert sqs_edges[0].kind == "publishes"


def test_kotlin_sqs_call_produces_both_a_cloud_fact_and_a_flow_edge(tmp_path: Path):
    (tmp_path / "OrderPublisher.kt").write_text(
        "import software.amazon.awssdk.services.sqs.SqsClient\n\n"
        "class OrderPublisher(private val sqsClient: SqsClient) {\n"
        "    fun publish(body: String) {\n"
        "        sqsClient.sendMessage(SendMessageRequest.builder().build())\n"
        "    }\n"
        "}\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert len(result.cloud_facts) == 1
    assert result.cloud_facts[0].service_name == "sqs"
    sqs_edges = [e for e in result.edges if e.target == "sqsClient.sendMessage"]
    assert len(sqs_edges) == 1
    assert sqs_edges[0].source == "OrderPublisher.publish"


def test_java_sqs_calls_in_two_different_methods_do_not_cross_leak(tmp_path: Path):
    (tmp_path / "OrderPublisher.java").write_text(
        "import software.amazon.awssdk.services.sqs.SqsClient;\n\n"
        "public class OrderPublisher {\n"
        "    private final SqsClient sqsClient;\n\n"
        "    void publishOrders(String body) {\n"
        "        sqsClient.sendMessage(SendMessageRequest.builder().build());\n"
        "    }\n\n"
        "    void publishInvoices(String body) {\n"
        "        sqsClient.sendMessage(SendMessageRequest.builder().build());\n"
        "    }\n"
        "}\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert len(result.cloud_facts) == 2
    sources = sorted(e.source for e in result.edges if e.target == "sqsClient.sendMessage")
    assert sources == ["OrderPublisher.publishInvoices", "OrderPublisher.publishOrders"]


def test_node_sqs_command_construction_produces_a_flow_edge(tmp_path: Path):
    (tmp_path / "publisher.ts").write_text(
        'import { SendMessageCommand } from "@aws-sdk/client-sqs";\n\n'
        "export async function publish(body: string) {\n"
        "  await sqs.send(new SendMessageCommand({ QueueUrl: url, MessageBody: body }));\n"
        "}\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert len(result.cloud_facts) == 1
    assert result.cloud_facts[0].service_name == "sqs"
    cloud_edges = [e for e in result.edges if e.target == "aws:sqs.SendMessage"]
    assert len(cloud_edges) == 1
    assert cloud_edges[0].source == "publisher.publish"
    assert cloud_edges[0].kind == "publishes"


def test_node_sqs_command_construction_in_two_functions_does_not_cross_leak(tmp_path: Path):
    (tmp_path / "publisher.ts").write_text(
        'import { SendMessageCommand } from "@aws-sdk/client-sqs";\n\n'
        "export async function publishOrders(body: string) {\n"
        "  await sqs.send(new SendMessageCommand({ QueueUrl: url, MessageBody: body }));\n"
        "}\n\n"
        "export async function publishInvoices(body: string) {\n"
        "  await sqs.send(new SendMessageCommand({ QueueUrl: url, MessageBody: body }));\n"
        "}\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert len(result.cloud_facts) == 2
    sources = sorted(e.source for e in result.edges if e.target == "aws:sqs.SendMessage")
    assert sources == ["publisher.publishInvoices", "publisher.publishOrders"]


def test_node_azure_blob_call_produces_both_a_cloud_fact_and_a_flow_edge(tmp_path: Path):
    (tmp_path / "uploader.ts").write_text(
        'import { BlobServiceClient } from "@azure/storage-blob";\n\n'
        "const client = new BlobServiceClient(url, credential);\n\n"
        "export async function upload(data: Buffer) {\n"
        "  await client.upload(data);\n"
        "}\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert len(result.cloud_facts) == 1
    assert result.cloud_facts[0].provider == "azure"
    cloud_edges = [e for e in result.edges if e.target == "client.upload"]
    assert len(cloud_edges) == 1
    assert cloud_edges[0].source == "uploader.upload"
    assert cloud_edges[0].kind == "writes"


# WP13 — factory-chained cloud calls also produce a real FlowEdge, same as
# the direct-call cases above. No engine.py change was needed for this: the
# factory-chain propagation lives entirely in go_client_declarations/
# node_stateful_client_declarations, whose merged output already flows
# through the same cloud_edge_kind_and_fact integration point WP12 set up.

def test_go_gcp_pubsub_factory_chain_call_produces_a_flow_edge(tmp_path: Path):
    # Named PublishOrder, not Publish: a local function sharing the exact SDK
    # method name would collide with BoundedFlowResolver's own "unique local
    # symbol named .Publish" fallback, which runs after cloud classification
    # and would rewrite the edge's target to the local symbol — a real,
    # separate resolver interaction, not something to work around here.
    (tmp_path / "publisher.go").write_text(
        "package publisher\n\n"
        'import "cloud.google.com/go/pubsub"\n\n'
        "func PublishOrder(client *pubsub.Client, body string) {\n"
        '\ttopic := client.Topic("orders")\n'
        "\ttopic.Publish(ctx, &pubsub.Message{})\n"
        "}\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "go")

    assert len(result.cloud_facts) == 1
    assert result.cloud_facts[0].provider == "gcp"
    cloud_edges = [e for e in result.edges if e.target == "topic.Publish"]
    assert len(cloud_edges) == 1
    assert cloud_edges[0].source == "publisher.PublishOrder"
    assert cloud_edges[0].kind == "publishes"


def test_node_azure_servicebus_factory_chain_call_produces_a_flow_edge(tmp_path: Path):
    (tmp_path / "publisher.ts").write_text(
        'import { ServiceBusClient } from "@azure/service-bus";\n\n'
        "const serviceBusClient = new ServiceBusClient(connectionString);\n\n"
        "export async function publish(body: string) {\n"
        '  const sender = serviceBusClient.createSender("orders-queue");\n'
        "  await sender.sendMessages(body);\n"
        "}\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert len(result.cloud_facts) == 1
    assert result.cloud_facts[0].service_name == "service_bus"
    cloud_edges = [e for e in result.edges if e.target == "sender.sendMessages"]
    assert len(cloud_edges) == 1
    assert cloud_edges[0].source == "publisher.publish"
    assert cloud_edges[0].kind == "publishes"


def test_java_azure_eventhub_call_produces_both_a_cloud_fact_and_a_flow_edge(tmp_path: Path):
    (tmp_path / "TelemetryPublisher.java").write_text(
        "import com.azure.messaging.eventhubs.EventHubProducerClient;\n\n"
        "public class TelemetryPublisher {\n"
        "    private final EventHubProducerClient producer;\n\n"
        "    void publish(Object batch) {\n"
        "        producer.sendBatch(batch);\n"
        "    }\n"
        "}\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert len(result.cloud_facts) == 1
    assert result.cloud_facts[0].provider == "azure"
    assert result.cloud_facts[0].resource_type == "stream"
    cloud_edges = [e for e in result.edges if e.target == "producer.sendBatch"]
    assert len(cloud_edges) == 1
    assert cloud_edges[0].source == "TelemetryPublisher.publish"
    assert cloud_edges[0].kind == "publishes"
