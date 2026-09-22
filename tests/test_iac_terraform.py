from pathlib import Path

from orbitkb.iac.terraform import parse_terraform_file


def test_parses_a_literal_named_resource(tmp_path: Path):
    tf = tmp_path / "main.tf"
    tf.write_text(
        'resource "aws_sqs_queue" "orders" {\n'
        '  name = "orders-queue"\n'
        "}\n"
    )

    resources = parse_terraform_file(tf)

    assert len(resources) == 1
    resource = resources[0]
    assert resource.provider == "aws"
    assert resource.resource_type == "queue"
    assert resource.iac_resource_type == "aws_sqs_queue"
    assert resource.logical_name == "orders"
    assert resource.physical_name == "orders-queue"
    assert resource.source_format == "terraform"
    assert resource.confidence == "high"
    assert resource.start_line == 1


def test_s3_bucket_resolves_physical_name_from_bucket_attribute(tmp_path: Path):
    tf = tmp_path / "main.tf"
    tf.write_text(
        'resource "aws_s3_bucket" "assets" {\n'
        '  bucket = "my-assets-bucket"\n'
        "}\n"
    )

    resources = parse_terraform_file(tf)

    assert resources[0].resource_type == "object_storage"
    assert resources[0].physical_name == "my-assets-bucket"


def test_interpolated_name_stays_unresolved(tmp_path: Path):
    tf = tmp_path / "main.tf"
    tf.write_text(
        'resource "aws_sqs_queue" "orders" {\n'
        '  name = "${var.env}-orders"\n'
        "}\n"
    )

    resources = parse_terraform_file(tf)

    assert resources[0].physical_name is None


def test_bare_expression_name_stays_unresolved(tmp_path: Path):
    tf = tmp_path / "main.tf"
    tf.write_text(
        'resource "aws_sns_topic" "alerts" {\n'
        "  name = local.topic_name\n"
        "}\n"
    )

    resources = parse_terraform_file(tf)

    assert resources[0].physical_name is None


def test_unmapped_resource_types_are_skipped_not_guessed(tmp_path: Path):
    tf = tmp_path / "main.tf"
    tf.write_text(
        'resource "aws_instance" "web" {\n'
        '  ami = "ami-123"\n'
        "}\n"
    )

    assert parse_terraform_file(tf) == []


def test_multiple_resources_across_the_file_are_each_located(tmp_path: Path):
    tf = tmp_path / "main.tf"
    tf.write_text(
        'resource "aws_instance" "web" {\n'
        '  ami = "ami-123"\n'
        "}\n"
        "\n"
        'resource "aws_sqs_queue" "dlq" {\n'
        '  name = "orders-dlq"\n'
        "}\n"
    )

    resources = parse_terraform_file(tf)

    assert len(resources) == 1
    assert resources[0].logical_name == "dlq"
    assert resources[0].start_line == 5


def test_malformed_file_returns_no_resources_instead_of_raising(tmp_path: Path):
    tf = tmp_path / "main.tf"
    tf.write_text('resource "aws_sqs_queue" "orders" {\n  name = \n')

    assert parse_terraform_file(tf) == []
