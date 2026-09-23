"""Python's `detect_cloud_facts` is exercised directly here since it remains a
flat, whole-file pass (see its module docstring). Go/JVM/Node no longer flow
through it — their "declared client -> method call" resolution is unit-tested
here against the public resolver functions themselves
(`node_command_imports`, `node_stateful_client_declarations`,
`jvm_client_declarations`, `go_client_declarations`), and their end-to-end
`FlowEdge`+`CloudFact` production through `StaticAnalysisEngine` is covered in
`tests/test_cloud_flow_edges.py`.
"""
from pathlib import Path

from orbitkb.analysis.cloud_detection import (
    detect_cloud_facts,
    go_client_declarations,
    jvm_client_declarations,
    node_command_imports,
    node_stateful_client_declarations,
)


def test_python_boto3_client_call_is_detected(tmp_path: Path):
    (tmp_path / "publisher.py").write_text(
        "import boto3\n\n"
        "sqs = boto3.client('sqs')\n\n"
        "def publish(body):\n"
        "    sqs.send_message(QueueUrl=url, MessageBody=body)\n"
    )

    facts = detect_cloud_facts([tmp_path / "publisher.py"], tmp_path)

    assert len(facts) == 1
    fact = facts[0]
    assert fact.provider == "aws"
    assert fact.resource_type == "queue"
    assert fact.service_name == "sqs"
    assert fact.operation == "SendMessage"
    assert fact.operation_kind == "publish"
    assert fact.sdk == "boto3"
    assert fact.evidence.file_path == "publisher.py"
    assert fact.evidence.start_line == 6


def test_python_boto3_resource_call_is_detected(tmp_path: Path):
    (tmp_path / "storage.py").write_text(
        "import boto3\n\n"
        "s3 = boto3.resource('s3')\n"
        "s3.put_object(Bucket=b, Key=k, Body=data)\n"
    )

    facts = detect_cloud_facts([tmp_path / "storage.py"], tmp_path)

    assert len(facts) == 1
    assert facts[0].service_name == "s3"
    assert facts[0].operation == "PutObject"


def test_python_call_on_an_unbound_variable_is_ignored(tmp_path: Path):
    (tmp_path / "unrelated.py").write_text(
        "def publish(body):\n"
        "    other_client.send_message(body)\n"
    )

    assert detect_cloud_facts([tmp_path / "unrelated.py"], tmp_path) == []


def test_python_syntax_error_returns_no_facts_instead_of_raising(tmp_path: Path):
    (tmp_path / "broken.py").write_text("def broken(:\n")

    assert detect_cloud_facts([tmp_path / "broken.py"], tmp_path) == []


def test_irrelevant_file_extensions_are_skipped(tmp_path: Path):
    (tmp_path / "notes.md").write_text("boto3.client('sqs').send_message()\n")

    assert detect_cloud_facts([tmp_path / "notes.md"], tmp_path) == []


def test_node_sdk_v3_command_construction_is_resolved():
    source = (
        'import { SQSClient, SendMessageCommand } from "@aws-sdk/client-sqs";\n\n'
        "const sqs = new SQSClient({ region: 'us-east-1' });\n"
    )

    imports = node_command_imports(source)

    assert imports == {"SendMessageCommand": ("client-sqs", "SendMessageCommand")}


def test_node_sdk_v3_resolves_an_import_alias():
    source = 'import { PutObjectCommand as PutCmd } from "@aws-sdk/client-s3";\n'

    imports = node_command_imports(source)

    assert imports == {"PutCmd": ("client-s3", "PutObjectCommand")}


def test_node_command_import_from_an_unrelated_package_is_ignored():
    source = 'import { SendMessageCommand } from "./local-commands";\n'

    assert node_command_imports(source) == {}


def test_java_field_declared_with_sdk_v2_client_type_is_detected():
    source = (
        "import software.amazon.awssdk.services.sqs.SqsClient;\n\n"
        "public class OrderPublisher {\n"
        "    private final SqsClient sqsClient;\n"
        "}\n"
    )

    declarations = jvm_client_declarations(source)

    assert "sqsClient" in declarations
    provider, service_name, resource_type, sdk, _ = declarations["sqsClient"]
    assert (provider, service_name, resource_type, sdk) == ("aws", "sqs", "queue", "aws-sdk-java-v2")


def test_java_field_declared_with_sdk_v1_client_type_is_detected():
    source = (
        "import com.amazonaws.services.sqs.AmazonSQSClient;\n\n"
        "public class OrderPublisher {\n"
        "    private AmazonSQSClient sqsClient;\n"
        "}\n"
    )

    declarations = jvm_client_declarations(source)

    assert declarations["sqsClient"][3] == "aws-sdk-java-v1"
    assert declarations["sqsClient"][1] == "sqs"


def test_kotlin_field_declared_with_sdk_v2_client_type_is_detected():
    source = (
        "import software.amazon.awssdk.services.sqs.SqsClient\n\n"
        "class OrderPublisher(private val sqsClient: SqsClient)\n"
    )

    declarations = jvm_client_declarations(source)

    assert declarations["sqsClient"][1] == "sqs"
    assert declarations["sqsClient"][3] == "aws-sdk-java-v2"


def test_jvm_type_with_the_right_name_but_no_matching_import_is_ignored():
    """A local class that happens to be named SqsClient, with no relation to
    the AWS SDK, must not be mistaken for one — the false positive bare
    type-name matching alone would produce."""
    source = (
        "public class OrderPublisher {\n"
        "    private final SqsClient sqsClient;\n"
        "}\n"
    )

    assert jvm_client_declarations(source) == {}


def test_jvm_type_with_the_right_name_but_wrong_import_is_ignored():
    source = (
        "import com.example.internal.SqsClient;\n\n"
        "public class OrderPublisher {\n"
        "    private final SqsClient sqsClient;\n"
        "}\n"
    )

    assert jvm_client_declarations(source) == {}


def test_unrelated_java_field_type_is_ignored():
    source = (
        "public class Unrelated {\n"
        "    private final String sqsClient;\n"
        "}\n"
    )

    assert jvm_client_declarations(source) == {}


def test_go_parameter_typed_as_sqs_client_is_detected():
    source = (
        "package publisher\n\n"
        'import "github.com/aws/aws-sdk-go-v2/service/sqs"\n\n'
        "func Publish(client *sqs.Client, body string) {\n"
        "}\n"
    )

    declarations = go_client_declarations(source)

    assert declarations["client"][1] == "sqs"
    assert declarations["client"][3] == "aws-sdk-go-v2"


def test_go_package_alias_with_the_right_name_but_wrong_import_is_ignored():
    source = (
        "package publisher\n\n"
        'import "example.com/internal/sqs"\n\n'
        "func Publish(client *sqs.Client, body string) {\n"
        "}\n"
    )

    assert go_client_declarations(source) == {}


def test_azure_blob_client_in_java_is_detected():
    source = (
        "import com.azure.storage.blob.BlobContainerClient;\n\n"
        "public class AssetsUploader {\n"
        "    private final BlobContainerClient containerClient;\n"
        "}\n"
    )

    declarations = jvm_client_declarations(source)

    provider, service_name, resource_type, sdk, _ = declarations["containerClient"]
    assert (provider, resource_type, service_name, sdk) == ("azure", "object_storage", "blob_storage", "azure-storage-blob")


def test_azure_blob_client_in_go_is_detected():
    source = (
        "package uploader\n\n"
        'import "github.com/Azure/azure-sdk-for-go/sdk/storage/azblob"\n\n'
        "func Upload(client *azblob.Client, data []byte) {\n"
        "}\n"
    )

    declarations = go_client_declarations(source)

    assert declarations["client"][0] == "azure"
    assert declarations["client"][3] == "azure-storage-blob"


def test_azure_blob_client_in_node_is_detected():
    source = (
        'import { BlobServiceClient } from "@azure/storage-blob";\n\n'
        "const client = new BlobServiceClient(url, credential);\n"
    )

    declarations = node_stateful_client_declarations(source)

    provider, service_name, resource_type, sdk, _ = declarations["client"]
    assert (provider, resource_type, service_name, sdk) == ("azure", "object_storage", "blob_storage", "azure-storage-blob")


def test_node_variable_not_constructed_from_an_azure_blob_type_is_ignored():
    source = "const client = new SomeOtherClient(url);\n"

    assert node_stateful_client_declarations(source) == {}


def test_node_type_with_the_right_name_but_no_matching_import_is_ignored():
    """A local class named BlobServiceClient with no relation to Azure's SDK
    (no import from @azure/storage-blob at all) must not be mistaken for
    one."""
    source = "const client = new BlobServiceClient(url, credential);\n"

    assert node_stateful_client_declarations(source) == {}


def test_node_type_with_the_right_name_but_wrong_import_is_ignored():
    source = (
        'import { BlobServiceClient } from "./local-fake-azure";\n\n'
        "const client = new BlobServiceClient(url, credential);\n"
    )

    assert node_stateful_client_declarations(source) == {}


def test_static_analysis_engine_surfaces_cloud_facts_for_python(tmp_path: Path):
    from orbitkb.analysis.engine import StaticAnalysisEngine

    (tmp_path / "publisher.py").write_text(
        "import boto3\n\nsqs = boto3.client('sqs')\nsqs.send_message(QueueUrl=url, MessageBody=body)\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "python")

    assert len(result.cloud_facts) == 1
    assert result.cloud_facts[0].service_name == "sqs"


# WP13 — GCP Pub/Sub, Azure Service Bus/Event Hub, Kinesis (Python)

def test_python_kinesis_client_call_is_detected(tmp_path: Path):
    (tmp_path / "producer.py").write_text(
        "import boto3\n\nkinesis = boto3.client('kinesis')\nkinesis.put_record(StreamName=s, Data=d, PartitionKey=k)\n"
    )

    facts = detect_cloud_facts([tmp_path / "producer.py"], tmp_path)

    assert len(facts) == 1
    assert facts[0].service_name == "kinesis"
    assert facts[0].operation == "PutRecord"


def test_gcp_pubsub_publisher_client_in_java_is_detected():
    source = (
        "import com.google.cloud.pubsub.v1.Publisher;\n\n"
        "public class OrderPublisher {\n"
        "    private final Publisher publisher;\n"
        "}\n"
    )

    declarations = jvm_client_declarations(source)

    provider, service_name, resource_type, sdk, _ = declarations["publisher"]
    assert (provider, service_name, resource_type, sdk) == ("gcp", "pubsub", "pubsub", "gcp-pubsub")


def test_gcp_pubsub_type_with_the_right_name_but_wrong_import_is_ignored():
    source = (
        "import com.example.internal.Publisher;\n\n"
        "public class OrderPublisher {\n"
        "    private final Publisher publisher;\n"
        "}\n"
    )

    assert jvm_client_declarations(source) == {}


def test_gcp_pubsub_go_factory_chain_is_detected():
    source = (
        "package publisher\n\n"
        'import "cloud.google.com/go/pubsub"\n\n'
        "func Publish(client *pubsub.Client, body string) {\n"
        '\ttopic := client.Topic("orders")\n'
        "\ttopic.Publish(ctx, &pubsub.Message{})\n"
        "}\n"
    )

    declarations = go_client_declarations(source)

    assert declarations["client"][0] == "gcp"
    assert declarations["topic"][0] == "gcp"
    assert declarations["topic"][1] == "pubsub"


def test_gcp_pubsub_go_factory_chain_is_not_propagated_from_an_unverified_base():
    source = (
        "package publisher\n\n"
        'import "example.com/internal/pubsub"\n\n'
        "func Publish(client *pubsub.Client, body string) {\n"
        '\ttopic := client.Topic("orders")\n'
        "}\n"
    )

    declarations = go_client_declarations(source)

    assert "client" not in declarations
    assert "topic" not in declarations


def test_gcp_pubsub_node_factory_chain_is_detected():
    source = (
        'import { PubSub } from "@google-cloud/pubsub";\n\n'
        "const pubsub = new PubSub();\n\n"
        "export async function publish(body) {\n"
        '  const topic = pubsub.topic("orders");\n'
        "  await topic.publishMessage({ data: body });\n"
        "}\n"
    )

    declarations = node_stateful_client_declarations(source)

    assert declarations["pubsub"][0] == "gcp"
    assert declarations["topic"][0] == "gcp"
    assert declarations["topic"][1] == "pubsub"


def test_node_factory_chain_ignores_an_unrecognized_method_name():
    """`pubsub.someOtherMethod(...)` isn't a documented Pub/Sub factory
    method — propagating a ClientKind through it would be a guess, not a
    proof, so it must not be recognized."""
    source = (
        'import { PubSub } from "@google-cloud/pubsub";\n\n'
        "const pubsub = new PubSub();\n"
        "const thing = pubsub.someOtherMethod();\n"
    )

    declarations = node_stateful_client_declarations(source)

    assert "thing" not in declarations


def test_azure_servicebus_sender_client_in_java_is_detected():
    source = (
        "import com.azure.messaging.servicebus.ServiceBusSenderClient;\n\n"
        "public class OrderPublisher {\n"
        "    private final ServiceBusSenderClient sender;\n"
        "}\n"
    )

    declarations = jvm_client_declarations(source)

    provider, service_name, resource_type, sdk, _ = declarations["sender"]
    # resource_type defaults to 'queue' — the SDK client type doesn't
    # distinguish queue vs. topic, see NON_AWS_SERVICE_RESOURCE_TYPE's
    # own comment in cloud_taxonomy.py.
    assert (provider, service_name, resource_type, sdk) == ("azure", "service_bus", "queue", "azure-servicebus")


def test_azure_servicebus_node_factory_chain_is_detected():
    source = (
        'import { ServiceBusClient } from "@azure/service-bus";\n\n'
        "const serviceBusClient = new ServiceBusClient(connectionString);\n\n"
        "export async function publish(body) {\n"
        '  const sender = serviceBusClient.createSender("orders-queue");\n'
        "  await sender.sendMessages(body);\n"
        "}\n"
    )

    declarations = node_stateful_client_declarations(source)

    assert declarations["serviceBusClient"][0] == "azure"
    assert declarations["sender"][1] == "service_bus"


def test_azure_eventhub_producer_client_in_java_is_detected():
    source = (
        "import com.azure.messaging.eventhubs.EventHubProducerClient;\n\n"
        "public class TelemetryPublisher {\n"
        "    private final EventHubProducerClient producer;\n"
        "}\n"
    )

    declarations = jvm_client_declarations(source)

    provider, service_name, resource_type, sdk, _ = declarations["producer"]
    assert (provider, service_name, resource_type, sdk) == ("azure", "event_hub", "stream", "azure-eventhub")


def test_azure_eventhub_producer_client_in_node_is_detected_directly():
    """Unlike Service Bus, Event Hub's producer client is constructed
    directly (`new EventHubProducerClient(...)`) and called on directly —
    same shape as Azure Blob, not a factory chain."""
    source = (
        'import { EventHubProducerClient } from "@azure/event-hubs";\n\n'
        "const producer = new EventHubProducerClient(connectionString, eventHubName);\n\n"
        "export async function publish(batch) {\n"
        "  await producer.sendBatch(batch);\n"
        "}\n"
    )

    declarations = node_stateful_client_declarations(source)

    provider, service_name, resource_type, sdk, _ = declarations["producer"]
    assert (provider, service_name, resource_type, sdk) == ("azure", "event_hub", "stream", "azure-eventhub")
