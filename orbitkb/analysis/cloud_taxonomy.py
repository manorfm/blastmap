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

CLOUD_PROVIDERS = frozenset({"aws", "azure", "gcp"})
# "stream" (Kinesis, Event Hub) is partitioned/replayable append-only log
# semantics — distinct from "event_bus" (EventBridge/Event Grid discrete event
# routing) even though both are sometimes casually called "eventing".
CLOUD_RESOURCE_TYPES = frozenset({"queue", "pubsub", "event_bus", "object_storage", "stream"})

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
    "azurerm_servicebus_queue": ("azure", "queue", "service_bus"),
    "azurerm_servicebus_topic": ("azure", "pubsub", "service_bus"),
    "azurerm_eventhub": ("azure", "stream", "event_hub"),
    "azurerm_eventgrid_topic": ("azure", "event_bus", "event_grid"),
    "aws_kinesis_stream": ("aws", "stream", "kinesis"),
    "AWS::Kinesis::Stream": ("aws", "stream", "kinesis"),
    "google_pubsub_topic": ("gcp", "pubsub", "pubsub"),
    "google_storage_bucket": ("gcp", "object_storage", "gcs"),
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
    "azurerm_servicebus_queue": ("name",),
    "azurerm_servicebus_topic": ("name",),
    "azurerm_eventhub": ("name",),
    "azurerm_eventgrid_topic": ("name",),
    "aws_kinesis_stream": ("name",),
    "AWS::Kinesis::Stream": ("Name",),
    "google_pubsub_topic": ("name",),
    "google_storage_bucket": ("name",),
}

# Attribute(s) whose mere presence on a declaration is tracked, value never
# inspected — `redrive_policy` is almost always a `jsonencode(...)` call in
# real Terraform, not a literal string, so its *content* can't be resolved
# the way IAC_NAME_ATTRIBUTES resolves a name; only "was a dead-letter policy
# configured at all" is asked for the DLQ smell, and the key's presence alone
# answers that.
IAC_PRESENCE_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "aws_sqs_queue": ("redrive_policy",),
    "AWS::SQS::Queue": ("RedrivePolicy",),
}

# Attribute(s) whose literal value matters, same "only literal, interpolation
# discarded" rule IAC_NAME_ATTRIBUTES already uses for physical_name.
IAC_VALUE_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "aws_s3_bucket": ("acl",),
    "AWS::S3::Bucket": ("AccessControl",),
    "azurerm_storage_container": ("container_access_type",),
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
    "PutRecordCommand": ("publish", "PutRecord"),
    "GetRecordsCommand": ("consume", "GetRecords"),
}

# `@aws-sdk/client-<x>` module basename -> service_name, AWS's own package-naming
# convention for SDK v3.
AWS_SDK_JS_V3_MODULE_SERVICE: dict[str, str] = {
    "client-sqs": "sqs",
    "client-sns": "sns",
    "client-s3": "s3",
    "client-eventbridge": "eventbridge",
    "client-kinesis": "kinesis",
}

# AWS SDK for Go v2 method name -> (operation_kind, canonical operation). Go's
# method names are already the bare PascalCase operation name (SendMessage,
# PutObject, ...) — the same canonical names AWS_SDK_JS_V3_COMMANDS already
# carries as each Command class's second tuple element, so this is derived
# from it (stripping the "Command" suffix) instead of hand-typed again.
AWS_SDK_GO_V2_METHODS: dict[str, tuple[str, str]] = {
    class_name.removesuffix("Command"): value
    for class_name, value in AWS_SDK_JS_V3_COMMANDS.items()
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
    "putRecord": ("publish", "PutRecord"), "put_record": ("publish", "PutRecord"),
    "getRecords": ("consume", "GetRecords"), "get_records": ("consume", "GetRecords"),
}

# Declared client type name -> service_name, one table per still-widely-used Java
# SDK generation (v1's com.amazonaws.* and v2's software.amazon.awssdk.*).
AWS_SDK_JAVA_V1_TYPES: dict[str, str] = {
    "AmazonSQS": "sqs", "AmazonSQSClient": "sqs",
    "AmazonSNS": "sns", "AmazonSNSClient": "sns",
    "AmazonS3": "s3", "AmazonS3Client": "s3",
    "AmazonCloudWatchEvents": "eventbridge", "AmazonCloudWatchEventsClient": "eventbridge",
    "AmazonKinesis": "kinesis", "AmazonKinesisClient": "kinesis",
}
AWS_SDK_JAVA_V2_TYPES: dict[str, str] = {
    "SqsClient": "sqs",
    "SnsClient": "sns",
    "S3Client": "s3",
    "EventBridgeClient": "eventbridge",
    "KinesisClient": "kinesis",
}

# Declared type name -> the exact fully-qualified name its import must resolve
# to before a JVM type match is trusted — closes the gap where a project's own
# unrelated class happening to share a name (e.g. a local `SqsClient`) would
# otherwise be mistaken for AWS's. Keys mirror AWS_SDK_JAVA_V1_TYPES/V2_TYPES
# exactly; every value is the SDK's own real, documented package path.
AWS_SDK_JAVA_V1_FQN: dict[str, str] = {
    "AmazonSQS": "com.amazonaws.services.sqs.AmazonSQS",
    "AmazonSQSClient": "com.amazonaws.services.sqs.AmazonSQSClient",
    "AmazonSNS": "com.amazonaws.services.sns.AmazonSNS",
    "AmazonSNSClient": "com.amazonaws.services.sns.AmazonSNSClient",
    "AmazonS3": "com.amazonaws.services.s3.AmazonS3",
    "AmazonS3Client": "com.amazonaws.services.s3.AmazonS3Client",
    "AmazonCloudWatchEvents": "com.amazonaws.services.cloudwatchevents.AmazonCloudWatchEvents",
    "AmazonCloudWatchEventsClient": "com.amazonaws.services.cloudwatchevents.AmazonCloudWatchEventsClient",
    "AmazonKinesis": "com.amazonaws.services.kinesis.AmazonKinesis",
    "AmazonKinesisClient": "com.amazonaws.services.kinesis.AmazonKinesisClient",
}
AWS_SDK_JAVA_V2_FQN: dict[str, str] = {
    "SqsClient": "software.amazon.awssdk.services.sqs.SqsClient",
    "SnsClient": "software.amazon.awssdk.services.sns.SnsClient",
    "S3Client": "software.amazon.awssdk.services.s3.S3Client",
    "EventBridgeClient": "software.amazon.awssdk.services.eventbridge.EventBridgeClient",
    "KinesisClient": "software.amazon.awssdk.services.kinesis.KinesisClient",
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
# Java's real package for the same three types, for import verification.
AZURE_BLOB_JAVA_FQN: dict[str, str] = {
    "BlobServiceClient": "com.azure.storage.blob.BlobServiceClient",
    "BlobContainerClient": "com.azure.storage.blob.BlobContainerClient",
    "BlobClient": "com.azure.storage.blob.BlobClient",
}

# Full Go import path -> (provider, service_name), for verifying a package
# alias like `sqs` in `*sqs.Client` really came from AWS's/Azure's own SDK
# module rather than an unrelated package that happens to share the same
# default alias (Go's default import alias is the path's last segment).
GO_CLOUD_IMPORT_PATHS: dict[str, tuple[str, str]] = {
    "github.com/aws/aws-sdk-go-v2/service/sqs": ("aws", "sqs"),
    "github.com/aws/aws-sdk-go-v2/service/sns": ("aws", "sns"),
    "github.com/aws/aws-sdk-go-v2/service/s3": ("aws", "s3"),
    "github.com/aws/aws-sdk-go-v2/service/eventbridge": ("aws", "eventbridge"),
    "github.com/aws/aws-sdk-go-v2/service/kinesis": ("aws", "kinesis"),
    "github.com/Azure/azure-sdk-for-go/sdk/storage/azblob": ("azure", "blob_storage"),
}

# Operation vocabulary for SDKs whose idiomatic usage is a *direct* call on a
# declared client (Python's PublisherClient.publish, Java's Publisher.publish,
# Go's *pubsub.Topic once the topic reference itself is known). Node's client
# instead returns topic/sender/producer references through a chained factory
# call (`client.topic('x').publish(...)`, `serviceBusClient.createSender('q')
# .sendMessages(...)`) that this table alone does not resolve — a real
# detection-mechanism question left open for WP13, not a data gap here (see
# plan.md's WP13 notes).
GCP_PUBSUB_METHOD_TABLE: dict[str, tuple[str, str]] = {
    "publish": ("publish", "Publish"), "Publish": ("publish", "Publish"),
}
GCS_METHOD_TABLE: dict[str, tuple[str, str]] = {
    "upload_from_string": ("write", "Upload"), "upload_from_filename": ("write", "Upload"),
    "download_as_bytes": ("read", "Download"), "download_to_filename": ("read", "Download"),
    "delete": ("write", "Delete"),
}
AZURE_SERVICEBUS_METHOD_TABLE: dict[str, tuple[str, str]] = {
    "sendMessages": ("publish", "SendMessages"), "send_messages": ("publish", "SendMessages"),
    "receiveMessages": ("consume", "ReceiveMessages"), "receive_messages": ("consume", "ReceiveMessages"),
}
AZURE_EVENTHUB_METHOD_TABLE: dict[str, tuple[str, str]] = {
    "sendBatch": ("publish", "SendBatch"), "send_batch": ("publish", "SendBatch"),
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
