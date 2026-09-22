"""Canonical, vendor-sourced taxonomy for deterministic cloud/infra detection.

Every table here mirrors an officially published vocabulary — Terraform provider
resource types, the CloudFormation Resource Type Reference, each AWS/Azure SDK's
own class/method names, and OpenTelemetry's semantic-convention registry for the
values shared with `runtime_flow_observations` — never an invented or guessed
label. `orbitkb/discovery/integration_heuristics.py` stays the keyword-based
last-resort fallback this taxonomy sits above: it only fills a gap neither this
structural layer nor the LLM's own judgment resolved.

Scope is deliberately v1-sized: AWS (SQS, SNS, S3, EventBridge) and Azure Blob
Storage. Adding a provider/service later means adding rows to these tables, not
changing how detection works.
"""
from __future__ import annotations

import re

CLOUD_PROVIDERS = frozenset({"aws", "azure"})
CLOUD_RESOURCE_TYPES = frozenset({"queue", "pubsub", "event_bus", "object_storage"})

# Literal Terraform resource type / CloudFormation `Type` string, exactly as it
# appears in source, -> (provider, resource_type, service_name). Sourced directly
# from each provider's own resource reference — never inferred from a name.
IAC_RESOURCE_TYPE_TABLE: dict[str, tuple[str, str, str]] = {
    "aws_sqs_queue": ("aws", "queue", "sqs"),
    "AWS::SQS::Queue": ("aws", "queue", "sqs"),
    "aws_sns_topic": ("aws", "pubsub", "sns"),
    "AWS::SNS::Topic": ("aws", "pubsub", "sns"),
    "aws_s3_bucket": ("aws", "object_storage", "s3"),
    "AWS::S3::Bucket": ("aws", "object_storage", "s3"),
    "aws_cloudwatch_event_bus": ("aws", "event_bus", "eventbridge"),
    "AWS::Events::EventBus": ("aws", "event_bus", "eventbridge"),
    "aws_cloudwatch_event_rule": ("aws", "event_bus", "eventbridge"),
    "AWS::Events::Rule": ("aws", "event_bus", "eventbridge"),
    "azurerm_storage_account": ("azure", "object_storage", "blob_storage"),
    "azurerm_storage_container": ("azure", "object_storage", "blob_storage"),
    "azurerm_storage_blob": ("azure", "object_storage", "blob_storage"),
}

# service_name -> resource_type for AWS, derived from IAC_RESOURCE_TYPE_TABLE so
# this stays one source of truth instead of a hand-typed duplicate (used by
# orbitkb/iac/compose.py to interpret LocalStack's own SERVICES env var).
AWS_SERVICE_RESOURCE_TYPE: dict[str, str] = {
    service_name: resource_type
    for provider, resource_type, service_name in IAC_RESOURCE_TYPE_TABLE.values()
    if provider == "aws"
}

# Which literal attribute(s) on a declaration may hold a resolvable physical name,
# per IaC resource type. Used only when the parsed attribute value is a plain
# literal string; an interpolated expression stays unresolved (see
# is_unresolved_literal) rather than guessed.
IAC_NAME_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    # Terraform resource type -> attribute(s)
    "aws_sqs_queue": ("name",),
    "aws_sns_topic": ("name",),
    "aws_s3_bucket": ("bucket",),
    "aws_cloudwatch_event_bus": ("name",),
    "aws_cloudwatch_event_rule": ("name",),
    "azurerm_storage_account": ("name",),
    "azurerm_storage_container": ("name",),
    "azurerm_storage_blob": ("name",),
    # CloudFormation `Type` -> Properties key(s)
    "AWS::SQS::Queue": ("QueueName",),
    "AWS::SNS::Topic": ("TopicName",),
    "AWS::S3::Bucket": ("BucketName",),
    "AWS::Events::EventBus": ("Name",),
    "AWS::Events::Rule": ("Name",),
}

# operation_kind is one of: 'publish' | 'consume' | 'read' | 'write' | 'admin'.

# AWS SDK for JavaScript v3 Command class name -> (operation_kind, canonical
# operation name). Constructing the Command class itself is the proof of the
# operation, regardless of which variable later sends it.
AWS_SDK_JS_V3_COMMANDS: dict[str, tuple[str, str]] = {
    "SendMessageCommand": ("publish", "SendMessage"),
    "SendMessageBatchCommand": ("publish", "SendMessageBatch"),
    "ReceiveMessageCommand": ("consume", "ReceiveMessage"),
    "DeleteMessageCommand": ("consume", "DeleteMessage"),
    "PublishCommand": ("publish", "Publish"),
    "SubscribeCommand": ("admin", "Subscribe"),
    "PutObjectCommand": ("write", "PutObject"),
    "GetObjectCommand": ("read", "GetObject"),
    "DeleteObjectCommand": ("write", "DeleteObject"),
    "ListObjectsV2Command": ("read", "ListObjectsV2"),
    "PutEventsCommand": ("publish", "PutEvents"),
}

# `@aws-sdk/client-<x>` module basename -> service_name, AWS's own package-naming
# convention for SDK v3.
AWS_SDK_JS_V3_MODULE_SERVICE: dict[str, str] = {
    "client-sqs": "sqs",
    "client-sns": "sns",
    "client-s3": "s3",
    "client-eventbridge": "eventbridge",
}

# AWS SDK for JavaScript v2 and boto3 share the same operation vocabulary, only
# differing in casing convention (camelCase vs. snake_case) — one table, both
# spellings, both resolving to the same canonical operation.
AWS_SDK_METHOD_TABLE: dict[str, tuple[str, str]] = {
    "sendMessage": ("publish", "SendMessage"), "send_message": ("publish", "SendMessage"),
    "receiveMessage": ("consume", "ReceiveMessage"), "receive_message": ("consume", "ReceiveMessage"),
    "deleteMessage": ("consume", "DeleteMessage"), "delete_message": ("consume", "DeleteMessage"),
    "publish": ("publish", "Publish"),
    "subscribe": ("admin", "Subscribe"),
    "putObject": ("write", "PutObject"), "put_object": ("write", "PutObject"),
    "getObject": ("read", "GetObject"), "get_object": ("read", "GetObject"),
    "deleteObject": ("write", "DeleteObject"), "delete_object": ("write", "DeleteObject"),
    "listObjectsV2": ("read", "ListObjectsV2"), "list_objects_v2": ("read", "ListObjectsV2"),
    "putEvents": ("publish", "PutEvents"), "put_events": ("publish", "PutEvents"),
}

# Declared client type name -> service_name, one table per still-widely-used Java
# SDK generation (v1's com.amazonaws.* and v2's software.amazon.awssdk.*).
AWS_SDK_JAVA_V1_TYPES: dict[str, str] = {
    "AmazonSQS": "sqs", "AmazonSQSClient": "sqs",
    "AmazonSNS": "sns", "AmazonSNSClient": "sns",
    "AmazonS3": "s3", "AmazonS3Client": "s3",
    "AmazonCloudWatchEvents": "eventbridge", "AmazonCloudWatchEventsClient": "eventbridge",
}
AWS_SDK_JAVA_V2_TYPES: dict[str, str] = {
    "SqsClient": "sqs",
    "SnsClient": "sns",
    "S3Client": "s3",
    "EventBridgeClient": "eventbridge",
}

# The literal string boto3.client(<literal>)/boto3.resource(<literal>) is called
# with is itself the proof of which service it talks to.
BOTO3_SERVICE_LITERALS: dict[str, str] = {
    "sqs": "sqs", "sns": "sns", "s3": "s3", "events": "eventbridge",
}

# Azure's Blob Storage SDKs use identical class/method names across languages by
# design, so one shared table covers Node/Java/Go/.NET alike.
AZURE_BLOB_CLIENT_TYPES = frozenset({"BlobServiceClient", "BlobContainerClient", "BlobClient"})
AZURE_BLOB_METHOD_TABLE: dict[str, tuple[str, str]] = {
    "upload": ("write", "Upload"), "uploadData": ("write", "Upload"), "upload_blob": ("write", "Upload"),
    "download": ("read", "Download"), "downloadToBuffer": ("read", "Download"), "download_blob": ("read", "Download"),
    "delete": ("write", "Delete"), "deleteBlob": ("write", "Delete"), "delete_blob": ("write", "Delete"),
}

_INTERPOLATION_RE = re.compile(r"\$\{.*\}")


def is_unresolved_literal(value: str) -> bool:
    """True whenever the value contains a Terraform interpolation (`${...}`)
    anywhere — HCL2's own syntax marker for "this is a template, not a literal",
    not a heuristic guess. A partially interpolated value like
    `"orders-${var.env}"` is still a template whose real name depends on runtime
    configuration, so it is treated as unresolved the same as a value that is
    nothing but an interpolation — storing the raw, un-evaluated text as if it
    were the resource's real name would misrepresent a template as a fact."""
    return bool(_INTERPOLATION_RE.search(value))


def otel_messaging_system(provider: str, service_name: str) -> str | None:
    """Canonical OpenTelemetry `messaging.system` value for a queue/pubsub
    resource, aligned with the semantic-convention registry OrbitKB already
    speaks for `runtime_flow_observations`. Returns None for event_bus/
    object_storage, which OTel does not model as a messaging system —
    intentional, not a gap to silently fill."""
    if provider == "aws" and service_name == "sqs":
        return "aws_sqs"
    if provider == "aws" and service_name == "sns":
        return "aws_sns"
    return None
