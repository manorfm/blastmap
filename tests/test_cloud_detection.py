from pathlib import Path

from orbitkb.analysis.cloud_detection import detect_cloud_facts
from orbitkb.analysis.engine import StaticAnalysisEngine


def test_node_sdk_v3_command_construction_is_detected(tmp_path: Path):
    (tmp_path / "publisher.ts").write_text(
        'import { SQSClient, SendMessageCommand } from "@aws-sdk/client-sqs";\n\n'
        "const sqs = new SQSClient({ region: 'us-east-1' });\n"
        "await sqs.send(new SendMessageCommand({ QueueUrl: url, MessageBody: body }));\n"
    )

    facts = detect_cloud_facts([tmp_path / "publisher.ts"], tmp_path)

    assert len(facts) == 1
    fact = facts[0]
    assert fact.provider == "aws"
    assert fact.resource_type == "queue"
    assert fact.service_name == "sqs"
    assert fact.operation == "SendMessage"
    assert fact.operation_kind == "publish"
    assert fact.sdk == "aws-sdk-js-v3"
    assert fact.target_name is None
    assert fact.evidence.file_path == "publisher.ts"
    assert fact.evidence.start_line == 4


def test_node_sdk_v3_resolves_an_import_alias(tmp_path: Path):
    (tmp_path / "publisher.ts").write_text(
        'import { PutObjectCommand as PutCmd } from "@aws-sdk/client-s3";\n\n'
        "await s3.send(new PutCmd({ Bucket: b, Key: k }));\n"
    )

    facts = detect_cloud_facts([tmp_path / "publisher.ts"], tmp_path)

    assert len(facts) == 1
    assert facts[0].service_name == "s3"
    assert facts[0].resource_type == "object_storage"
    assert facts[0].operation == "PutObject"
    assert facts[0].operation_kind == "write"


def test_node_import_from_an_unrelated_package_is_ignored(tmp_path: Path):
    (tmp_path / "unrelated.ts").write_text(
        'import { SendMessageCommand } from "./local-commands";\n\n'
        "new SendMessageCommand({});\n"
    )

    assert detect_cloud_facts([tmp_path / "unrelated.ts"], tmp_path) == []


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


def test_java_field_declared_with_sdk_v2_client_type_is_detected(tmp_path: Path):
    (tmp_path / "OrderPublisher.java").write_text(
        "public class OrderPublisher {\n"
        "    private final SqsClient sqsClient;\n\n"
        "    void publish(String body) {\n"
        "        sqsClient.sendMessage(SendMessageRequest.builder().build());\n"
        "    }\n"
        "}\n"
    )

    facts = detect_cloud_facts([tmp_path / "OrderPublisher.java"], tmp_path)

    assert len(facts) == 1
    fact = facts[0]
    assert fact.provider == "aws"
    assert fact.service_name == "sqs"
    assert fact.operation == "SendMessage"
    assert fact.sdk == "aws-sdk-java-v2"


def test_java_field_declared_with_sdk_v1_client_type_is_detected(tmp_path: Path):
    (tmp_path / "OrderPublisher.java").write_text(
        "public class OrderPublisher {\n"
        "    private AmazonSQSClient sqsClient;\n\n"
        "    void publish(String body) {\n"
        "        sqsClient.sendMessage(new SendMessageRequest());\n"
        "    }\n"
        "}\n"
    )

    facts = detect_cloud_facts([tmp_path / "OrderPublisher.java"], tmp_path)

    assert len(facts) == 1
    assert facts[0].sdk == "aws-sdk-java-v1"
    assert facts[0].service_name == "sqs"


def test_kotlin_field_declared_with_sdk_v2_client_type_is_detected(tmp_path: Path):
    (tmp_path / "OrderPublisher.kt").write_text(
        "class OrderPublisher(private val sqsClient: SqsClient) {\n"
        "    fun publish(body: String) {\n"
        "        sqsClient.sendMessage(SendMessageRequest.builder().build())\n"
        "    }\n"
        "}\n"
    )

    facts = detect_cloud_facts([tmp_path / "OrderPublisher.kt"], tmp_path)

    assert len(facts) == 1
    assert facts[0].service_name == "sqs"
    assert facts[0].sdk == "aws-sdk-java-v2"


def test_go_parameter_typed_as_sqs_client_is_detected(tmp_path: Path):
    (tmp_path / "publisher.go").write_text(
        "package publisher\n\n"
        "func Publish(client *sqs.Client, body string) {\n"
        "\tclient.SendMessage(ctx, &sqs.SendMessageInput{})\n"
        "}\n"
    )

    facts = detect_cloud_facts([tmp_path / "publisher.go"], tmp_path)

    assert len(facts) == 1
    fact = facts[0]
    assert fact.provider == "aws"
    assert fact.service_name == "sqs"
    assert fact.operation == "SendMessage"
    assert fact.sdk == "aws-sdk-go-v2"


def test_azure_blob_client_in_java_is_detected(tmp_path: Path):
    (tmp_path / "AssetsUploader.java").write_text(
        "public class AssetsUploader {\n"
        "    private final BlobContainerClient containerClient;\n\n"
        "    void upload(byte[] data) {\n"
        "        containerClient.upload(data);\n"
        "    }\n"
        "}\n"
    )

    facts = detect_cloud_facts([tmp_path / "AssetsUploader.java"], tmp_path)

    assert len(facts) == 1
    fact = facts[0]
    assert fact.provider == "azure"
    assert fact.resource_type == "object_storage"
    assert fact.service_name == "blob_storage"
    assert fact.sdk == "azure-storage-blob"


def test_azure_blob_client_in_go_is_detected(tmp_path: Path):
    (tmp_path / "uploader.go").write_text(
        "package uploader\n\n"
        "func Upload(client *azblob.Client, data []byte) {\n"
        "\tclient.upload(data)\n"
        "}\n"
    )

    facts = detect_cloud_facts([tmp_path / "uploader.go"], tmp_path)

    assert len(facts) == 1
    assert facts[0].provider == "azure"
    assert facts[0].sdk == "azure-storage-blob"


def test_unrelated_java_field_type_is_ignored(tmp_path: Path):
    (tmp_path / "Unrelated.java").write_text(
        "public class Unrelated {\n"
        "    private final String sqsClient;\n\n"
        "    void publish() {\n"
        "        sqsClient.sendMessage(\"x\");\n"
        "    }\n"
        "}\n"
    )

    assert detect_cloud_facts([tmp_path / "Unrelated.java"], tmp_path) == []


def test_static_analysis_engine_surfaces_cloud_facts_for_node_ts(tmp_path: Path):
    (tmp_path / "publisher.ts").write_text(
        'import { SendMessageCommand } from "@aws-sdk/client-sqs";\n\n'
        "await sqs.send(new SendMessageCommand({ QueueUrl: url, MessageBody: body }));\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert len(result.cloud_facts) == 1
    assert result.cloud_facts[0].service_name == "sqs"


def test_static_analysis_engine_surfaces_cloud_facts_for_python(tmp_path: Path):
    (tmp_path / "publisher.py").write_text(
        "import boto3\n\nsqs = boto3.client('sqs')\nsqs.send_message(QueueUrl=url, MessageBody=body)\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "python")

    assert len(result.cloud_facts) == 1
    assert result.cloud_facts[0].service_name == "sqs"


def test_static_analysis_engine_surfaces_cloud_facts_for_jvm_spring(tmp_path: Path):
    (tmp_path / "OrderPublisher.java").write_text(
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


def test_static_analysis_engine_surfaces_cloud_facts_for_go(tmp_path: Path):
    (tmp_path / "publisher.go").write_text(
        "package publisher\n\n"
        "func Publish(client *sqs.Client, body string) {\n"
        "\tclient.SendMessage(ctx, &sqs.SendMessageInput{})\n"
        "}\n"
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "go")

    assert len(result.cloud_facts) == 1
    assert result.cloud_facts[0].service_name == "sqs"
