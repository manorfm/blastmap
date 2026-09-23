from orbitkb.analysis.cloud_taxonomy import (
    AWS_SDK_GO_V2_METHODS,
    AWS_SDK_JAVA_V1_FQN,
    AWS_SDK_JAVA_V1_TYPES,
    AWS_SDK_JAVA_V2_FQN,
    AWS_SDK_JAVA_V2_TYPES,
    AWS_SDK_JS_V3_COMMANDS,
    AWS_SDK_JS_V3_MODULE_SERVICE,
    AWS_SDK_METHOD_TABLE,
    AZURE_BLOB_CLIENT_TYPES,
    AZURE_BLOB_JAVA_FQN,
    AZURE_BLOB_METHOD_TABLE,
    BOTO3_SERVICE_LITERALS,
    CLOUD_PROVIDERS,
    CLOUD_RESOURCE_TYPES,
    GO_CLOUD_IMPORT_PATHS,
    IAC_NAME_ATTRIBUTES,
    IAC_RESOURCE_TYPE_TABLE,
    is_unresolved_literal,
    otel_messaging_system,
)


def test_cloud_providers_and_resource_types_are_the_v1_scope():
    assert CLOUD_PROVIDERS == {"aws", "azure"}
    assert CLOUD_RESOURCE_TYPES == {"queue", "pubsub", "event_bus", "object_storage"}


def test_iac_resource_type_table_maps_terraform_and_cloudformation_literals():
    assert IAC_RESOURCE_TYPE_TABLE["aws_sqs_queue"] == ("aws", "queue", "sqs")
    assert IAC_RESOURCE_TYPE_TABLE["AWS::SQS::Queue"] == ("aws", "queue", "sqs")
    assert IAC_RESOURCE_TYPE_TABLE["aws_sns_topic"] == ("aws", "pubsub", "sns")
    assert IAC_RESOURCE_TYPE_TABLE["AWS::SNS::Topic"] == ("aws", "pubsub", "sns")
    assert IAC_RESOURCE_TYPE_TABLE["aws_s3_bucket"] == ("aws", "object_storage", "s3")
    assert IAC_RESOURCE_TYPE_TABLE["AWS::S3::Bucket"] == ("aws", "object_storage", "s3")
    assert IAC_RESOURCE_TYPE_TABLE["aws_cloudwatch_event_bus"] == ("aws", "event_bus", "eventbridge")
    assert IAC_RESOURCE_TYPE_TABLE["AWS::Events::EventBus"] == ("aws", "event_bus", "eventbridge")
    assert IAC_RESOURCE_TYPE_TABLE["aws_cloudwatch_event_rule"] == ("aws", "event_bus", "eventbridge")
    assert IAC_RESOURCE_TYPE_TABLE["AWS::Events::Rule"] == ("aws", "event_bus", "eventbridge")
    assert IAC_RESOURCE_TYPE_TABLE["azurerm_storage_account"] == ("azure", "object_storage", "blob_storage")
    assert IAC_RESOURCE_TYPE_TABLE["azurerm_storage_container"] == ("azure", "object_storage", "blob_storage")
    assert IAC_RESOURCE_TYPE_TABLE["azurerm_storage_blob"] == ("azure", "object_storage", "blob_storage")


def test_iac_name_attributes_only_cover_mapped_resource_types():
    assert set(IAC_NAME_ATTRIBUTES) <= set(IAC_RESOURCE_TYPE_TABLE)
    assert IAC_NAME_ATTRIBUTES["aws_s3_bucket"] == ("bucket",)
    assert IAC_NAME_ATTRIBUTES["aws_sqs_queue"] == ("name",)
    assert IAC_NAME_ATTRIBUTES["AWS::SQS::Queue"] == ("QueueName",)
    assert IAC_NAME_ATTRIBUTES["AWS::S3::Bucket"] == ("BucketName",)


def test_is_unresolved_literal_flags_any_interpolation_even_partial():
    assert is_unresolved_literal("${var.env}") is True
    assert is_unresolved_literal("${aws_sqs_queue.orders.name}") is True
    assert is_unresolved_literal("orders-queue") is False
    # A partial template ("alerts-${var.env}") is still not a real, evaluated
    # name — never present un-evaluated HCL syntax as a resolved fact.
    assert is_unresolved_literal("orders-${var.env}") is True


def test_aws_sdk_js_v3_commands_map_to_operation_kind_and_canonical_name():
    assert AWS_SDK_JS_V3_COMMANDS["SendMessageCommand"] == ("publish", "SendMessage")
    assert AWS_SDK_JS_V3_COMMANDS["ReceiveMessageCommand"] == ("consume", "ReceiveMessage")
    assert AWS_SDK_JS_V3_COMMANDS["PutObjectCommand"] == ("write", "PutObject")
    assert AWS_SDK_JS_V3_COMMANDS["GetObjectCommand"] == ("read", "GetObject")
    assert AWS_SDK_JS_V3_COMMANDS["PutEventsCommand"] == ("publish", "PutEvents")


def test_aws_sdk_js_v3_module_basename_resolves_to_service_name():
    assert AWS_SDK_JS_V3_MODULE_SERVICE["client-sqs"] == "sqs"
    assert AWS_SDK_JS_V3_MODULE_SERVICE["client-s3"] == "s3"
    assert AWS_SDK_JS_V3_MODULE_SERVICE["client-eventbridge"] == "eventbridge"


def test_aws_sdk_method_table_covers_both_js_v2_and_boto3_casing():
    assert AWS_SDK_METHOD_TABLE["sendMessage"] == ("publish", "SendMessage")
    assert AWS_SDK_METHOD_TABLE["send_message"] == ("publish", "SendMessage")
    assert AWS_SDK_METHOD_TABLE["put_object"] == ("write", "PutObject")


def test_go_sdk_v2_methods_are_derived_from_js_v3_commands():
    assert AWS_SDK_GO_V2_METHODS["SendMessage"] == ("publish", "SendMessage")
    assert AWS_SDK_GO_V2_METHODS["PutObject"] == ("write", "PutObject")
    assert "SendMessageCommand" not in AWS_SDK_GO_V2_METHODS


def test_java_sdk_type_tables_cover_both_generations():
    assert AWS_SDK_JAVA_V1_TYPES["AmazonSQSClient"] == "sqs"
    assert AWS_SDK_JAVA_V1_TYPES["AmazonSQS"] == "sqs"
    assert AWS_SDK_JAVA_V2_TYPES["SqsClient"] == "sqs"
    assert AWS_SDK_JAVA_V2_TYPES["S3Client"] == "s3"


def test_boto3_service_literals_map_to_taxonomy_service_names():
    assert BOTO3_SERVICE_LITERALS["sqs"] == "sqs"
    assert BOTO3_SERVICE_LITERALS["events"] == "eventbridge"


def test_azure_blob_client_types_and_methods():
    assert "BlobServiceClient" in AZURE_BLOB_CLIENT_TYPES
    assert "BlobClient" in AZURE_BLOB_CLIENT_TYPES
    assert AZURE_BLOB_METHOD_TABLE["upload"] == ("write", "Upload")
    assert AZURE_BLOB_METHOD_TABLE["download_blob"] == ("read", "Download")


def test_otel_messaging_system_matches_semantic_convention_values():
    assert otel_messaging_system("aws", "sqs") == "aws_sqs"
    assert otel_messaging_system("aws", "sns") == "aws_sns"


def test_otel_messaging_system_is_none_for_non_messaging_resource_kinds():
    # EventBridge and S3/Blob aren't modeled as a messaging.system by OTel itself —
    # an intentional, documented gap, not something to silently fill in.
    assert otel_messaging_system("aws", "eventbridge") is None
    assert otel_messaging_system("aws", "s3") is None
    assert otel_messaging_system("azure", "blob_storage") is None


def test_java_fqn_tables_cover_every_type_key():
    assert set(AWS_SDK_JAVA_V1_FQN) == set(AWS_SDK_JAVA_V1_TYPES)
    assert set(AWS_SDK_JAVA_V2_FQN) == set(AWS_SDK_JAVA_V2_TYPES)
    assert AWS_SDK_JAVA_V2_FQN["SqsClient"] == "software.amazon.awssdk.services.sqs.SqsClient"
    assert AWS_SDK_JAVA_V1_FQN["AmazonSQSClient"] == "com.amazonaws.services.sqs.AmazonSQSClient"


def test_azure_blob_java_fqn_covers_every_client_type():
    assert set(AZURE_BLOB_JAVA_FQN) == set(AZURE_BLOB_CLIENT_TYPES)
    assert AZURE_BLOB_JAVA_FQN["BlobServiceClient"] == "com.azure.storage.blob.BlobServiceClient"


def test_go_cloud_import_paths_resolve_provider_and_service():
    assert GO_CLOUD_IMPORT_PATHS["github.com/aws/aws-sdk-go-v2/service/sqs"] == ("aws", "sqs")
    assert GO_CLOUD_IMPORT_PATHS["github.com/Azure/azure-sdk-for-go/sdk/storage/azblob"] == ("azure", "blob_storage")
