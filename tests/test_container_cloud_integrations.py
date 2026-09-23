"""Opt-in container smoke E2E for cloud SDK detection: proves a real AWS
protocol round-trip against LocalStack (not a mock), the same posture
test_container_integrations.py already takes for RabbitMQ/Postgres/Mongo.
"""
from __future__ import annotations

import json
from pathlib import Path

from orbitkb.analysis.engine import StaticAnalysisEngine
from tests.container_e2e import ContainerStack, require_container_e2e

_ENDPOINT_ARGS = ("--endpoint-url", "http://localhost:4566", "--region", "us-east-1")


def test_localstack_sqs_round_trip_and_static_recognition(tmp_path: Path):
    """Create a real SQS queue in LocalStack, send and receive a real message
    through it, then assert StaticAnalysisEngine recognizes representative
    Node/TS source referencing the same queue name as a CloudFact.
    """
    require_container_e2e()
    stack = ContainerStack()
    stack.start()
    try:
        stack.exec("localstack", "aws", *_ENDPOINT_ARGS, "sqs", "create-queue", "--queue-name", "orbitkb-orders")
        queue_url = json.loads(stack.exec(
            "localstack", "aws", *_ENDPOINT_ARGS, "sqs", "get-queue-url", "--queue-name", "orbitkb-orders",
        ))["QueueUrl"]
        stack.exec(
            "localstack", "aws", *_ENDPOINT_ARGS, "sqs", "send-message",
            "--queue-url", queue_url, "--message-body", "indexed",
        )
        received = json.loads(stack.exec(
            "localstack", "aws", *_ENDPOINT_ARGS, "sqs", "receive-message", "--queue-url", queue_url,
        ))
        assert received["Messages"][0]["Body"] == "indexed"

        _write_representative_source(tmp_path)
        result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")
        facts = {(f.service_name, f.operation, f.operation_kind) for f in result.cloud_facts}
        assert facts == {("sqs", "SendMessage", "publish")}
    finally:
        stack.stop()


def _write_representative_source(root: Path) -> None:
    (root / "publisher.ts").write_text(
        'import { SQSClient, SendMessageCommand } from "@aws-sdk/client-sqs";\n\n'
        "const sqs = new SQSClient({ region: 'us-east-1' });\n\n"
        "export async function publishOrder(body: string) {\n"
        "  await sqs.send(new SendMessageCommand({ QueueUrl: process.env.ORDERS_QUEUE_URL, MessageBody: body }));\n"
        "}\n",
        encoding="utf-8",
    )
