from pathlib import Path

from orbitkb.iac.cloudformation import parse_cloudformation_file


def test_parses_a_literal_named_resource_from_yaml(tmp_path: Path):
    template = tmp_path / "template.yaml"
    template.write_text(
        "Resources:\n"
        "  OrdersQueue:\n"
        "    Type: AWS::SQS::Queue\n"
        "    Properties:\n"
        "      QueueName: orders-queue\n"
    )

    resources = parse_cloudformation_file(template)

    assert len(resources) == 1
    resource = resources[0]
    assert resource.provider == "aws"
    assert resource.resource_type == "queue"
    assert resource.iac_resource_type == "AWS::SQS::Queue"
    assert resource.logical_name == "OrdersQueue"
    assert resource.physical_name == "orders-queue"
    assert resource.source_format == "cloudformation"


def test_parses_a_literal_named_resource_from_json(tmp_path: Path):
    template = tmp_path / "template.json"
    template.write_text(
        '{"Resources": {"AssetsBucket": {"Type": "AWS::S3::Bucket", '
        '"Properties": {"BucketName": "my-assets-bucket"}}}}'
    )

    resources = parse_cloudformation_file(template)

    assert len(resources) == 1
    assert resources[0].resource_type == "object_storage"
    assert resources[0].physical_name == "my-assets-bucket"


def test_intrinsic_function_value_stays_unresolved(tmp_path: Path):
    template = tmp_path / "template.yaml"
    template.write_text(
        "Resources:\n"
        "  DynamicQueue:\n"
        "    Type: AWS::SQS::Queue\n"
        "    Properties:\n"
        '      QueueName: !Sub "${AWS::StackName}-orders"\n'
    )

    resources = parse_cloudformation_file(template)

    assert resources[0].physical_name is None


def test_unmapped_resource_types_are_skipped_not_guessed(tmp_path: Path):
    template = tmp_path / "template.yaml"
    template.write_text(
        "Resources:\n"
        "  WebServer:\n"
        "    Type: AWS::EC2::Instance\n"
        "    Properties:\n"
        "      ImageId: ami-123\n"
    )

    assert parse_cloudformation_file(template) == []


def test_malformed_file_returns_no_resources_instead_of_raising(tmp_path: Path):
    template = tmp_path / "template.yaml"
    template.write_text("Resources: [this is: not, valid: - cfn")

    assert parse_cloudformation_file(template) == []


def test_template_without_resources_key_returns_empty(tmp_path: Path):
    template = tmp_path / "template.yaml"
    template.write_text("AWSTemplateFormatVersion: '2010-09-09'\n")

    assert parse_cloudformation_file(template) == []
