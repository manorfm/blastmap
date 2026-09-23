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


def test_sqs_queue_with_redrive_policy_tracks_presence(tmp_path: Path):
    tf = tmp_path / "main.tf"
    tf.write_text(
        'resource "aws_sqs_queue" "orders" {\n'
        '  name = "orders-queue"\n'
        "  redrive_policy = jsonencode({\n"
        "    deadLetterTargetArn = aws_sqs_queue.dlq.arn\n"
        "    maxReceiveCount     = 5\n"
        "  })\n"
        "}\n"
    )

    resources = parse_terraform_file(tf)

    assert resources[0].attributes == {"redrive_policy": True}


def test_sqs_queue_without_redrive_policy_has_no_tracked_attributes(tmp_path: Path):
    tf = tmp_path / "main.tf"
    tf.write_text(
        'resource "aws_sqs_queue" "orders" {\n'
        '  name = "orders-queue"\n'
        "}\n"
    )

    resources = parse_terraform_file(tf)

    assert resources[0].attributes == {}


def test_s3_bucket_tracks_literal_acl_value(tmp_path: Path):
    tf = tmp_path / "main.tf"
    tf.write_text(
        'resource "aws_s3_bucket" "assets" {\n'
        '  bucket = "my-assets-bucket"\n'
        '  acl    = "public-read"\n'
        "}\n"
    )

    resources = parse_terraform_file(tf)

    assert resources[0].attributes == {"acl": "public-read"}


def test_s3_bucket_with_interpolated_acl_does_not_track_it(tmp_path: Path):
    tf = tmp_path / "main.tf"
    tf.write_text(
        'resource "aws_s3_bucket" "assets" {\n'
        '  bucket = "my-assets-bucket"\n'
        "  acl    = local.bucket_acl\n"
        "}\n"
    )

    resources = parse_terraform_file(tf)

    assert resources[0].attributes == {}


def test_sqs_queue_with_kms_key_tracks_encryption_presence(tmp_path: Path):
    tf = tmp_path / "main.tf"
    tf.write_text(
        'resource "aws_sqs_queue" "orders" {\n'
        '  name              = "orders-queue"\n'
        '  kms_master_key_id = "alias/aws/sqs"\n'
        "}\n"
    )

    resources = parse_terraform_file(tf)

    assert resources[0].attributes == {"kms_master_key_id": True}


def test_s3_bucket_with_legacy_inline_versioning_block_tracks_presence(tmp_path: Path):
    tf = tmp_path / "main.tf"
    tf.write_text(
        'resource "aws_s3_bucket" "assets" {\n'
        '  bucket = "my-assets-bucket"\n'
        "  versioning {\n"
        "    enabled = true\n"
        "  }\n"
        "}\n"
    )

    resources = parse_terraform_file(tf)

    assert resources[0].attributes == {"versioning": True}


def test_s3_bucket_versioning_resource_is_correlated_back_to_its_bucket(tmp_path: Path):
    """Modern Terraform (AWS provider v4+) splits versioning into its own
    resource, referencing the bucket instead of declaring it inline."""
    tf = tmp_path / "main.tf"
    tf.write_text(
        'resource "aws_s3_bucket" "orders" {\n'
        '  bucket = "orders-bucket"\n'
        "}\n"
        'resource "aws_s3_bucket_versioning" "orders" {\n'
        "  bucket = aws_s3_bucket.orders.id\n"
        "  versioning_configuration {\n"
        '    status = "Enabled"\n'
        "  }\n"
        "}\n"
    )

    resources = parse_terraform_file(tf)

    bucket = next(r for r in resources if r.iac_resource_type == "aws_s3_bucket")
    assert bucket.attributes == {"versioning_configured": True}
    # aws_s3_bucket_versioning is a settings attachment, not a cloud resource
    # of its own -- it must not also appear as a separate IacResource row.
    assert len(resources) == 1


def test_s3_bucket_encryption_resource_is_correlated_back_to_its_bucket(tmp_path: Path):
    tf = tmp_path / "main.tf"
    tf.write_text(
        'resource "aws_s3_bucket" "orders" {\n'
        '  bucket = "orders-bucket"\n'
        "}\n"
        'resource "aws_s3_bucket_server_side_encryption_configuration" "orders" {\n'
        "  bucket = aws_s3_bucket.orders.id\n"
        "  rule {\n"
        "    apply_server_side_encryption_by_default {\n"
        '      sse_algorithm = "AES256"\n'
        "    }\n"
        "  }\n"
        "}\n"
    )

    resources = parse_terraform_file(tf)

    bucket = next(r for r in resources if r.iac_resource_type == "aws_s3_bucket")
    assert bucket.attributes == {"encryption_configured": True}


def test_s3_bucket_versioning_resource_referencing_a_different_bucket_is_not_correlated(tmp_path: Path):
    tf = tmp_path / "main.tf"
    tf.write_text(
        'resource "aws_s3_bucket" "orders" {\n'
        '  bucket = "orders-bucket"\n'
        "}\n"
        'resource "aws_s3_bucket_versioning" "payments" {\n'
        "  bucket = aws_s3_bucket.payments.id\n"
        "  versioning_configuration {\n"
        '    status = "Enabled"\n'
        "  }\n"
        "}\n"
    )

    resources = parse_terraform_file(tf)

    bucket = next(r for r in resources if r.iac_resource_type == "aws_s3_bucket")
    assert bucket.attributes == {}
