from pathlib import Path

from orbitkb.iac.compose import parse_compose_file


def test_localstack_with_explicit_services_env_emits_one_resource_per_service(tmp_path: Path):
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(
        "services:\n"
        "  localstack:\n"
        "    image: localstack/localstack\n"
        "    environment:\n"
        "      - SERVICES=sqs,s3\n"
    )

    resources = parse_compose_file(compose)

    assert {(r.resource_type, r.logical_name) for r in resources} == {
        ("queue", "localstack"), ("object_storage", "localstack"),
    }
    assert all(r.source_format == "compose_hint" for r in resources)
    assert all(r.confidence == "low" for r in resources)
    assert all(r.provider == "aws" for r in resources)
    assert all(r.physical_name is None for r in resources)


def test_localstack_without_services_env_emits_nothing_rather_than_guessing(tmp_path: Path):
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(
        "services:\n"
        "  localstack:\n"
        "    image: localstack/localstack\n"
    )

    assert parse_compose_file(compose) == []


def test_azurite_image_emits_a_blob_storage_hint(tmp_path: Path):
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(
        "services:\n"
        "  azurite:\n"
        "    image: mcr.microsoft.com/azure-storage/azurite\n"
    )

    resources = parse_compose_file(compose)

    assert len(resources) == 1
    assert resources[0].provider == "azure"
    assert resources[0].resource_type == "object_storage"
    assert resources[0].source_format == "compose_hint"
    assert resources[0].confidence == "low"


def test_unrelated_images_are_ignored(tmp_path: Path):
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text(
        "services:\n"
        "  postgres:\n"
        "    image: postgres:16-alpine\n"
    )

    assert parse_compose_file(compose) == []


def test_malformed_compose_file_returns_no_resources(tmp_path: Path):
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text("services: [this is: not, valid")

    assert parse_compose_file(compose) == []
