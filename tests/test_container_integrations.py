"""Opt-in container smoke E2E for the infrastructure OrbitKB recognizes."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from orbitkb.analysis.engine import StaticAnalysisEngine
from tests.container_e2e import ContainerStack, require_container_e2e


def test_container_e2e_requires_an_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("ORBITKB_CONTAINER_E2E", raising=False)

    with pytest.raises(pytest.skip.Exception, match="ORBITKB_CONTAINER_E2E=1"):
        require_container_e2e()


def test_container_services_accept_native_operations_and_match_static_contracts(tmp_path: Path):
    """Exercise actual broker/database protocols, then project representative source.

    This is intentionally not a user-application performance test. It proves the
    pinned local infrastructure can execute one native operation per integration
    and that the corresponding source constructs remain recognized by OrbitKB.
    """
    require_container_e2e()
    stack = ContainerStack()
    stack.start()
    try:
        postgres = stack.exec(
            "postgres", "psql", "-U", "orbitkb", "-d", "orbitkb", "-Atc",
            "CREATE TABLE orders (id integer); INSERT INTO orders VALUES (7); SELECT id FROM orders;",
        )
        assert postgres.strip().endswith("7")

        mongo = stack.exec(
            "mongo", "mongosh", "orbitkb", "--quiet", "--eval",
            'db.orders.insertOne({id: 7}); if (db.orders.countDocuments({id: 7}) !== 1) throw new Error("missing order")',
        )
        assert "missing order" not in mongo

        management_url = f"http://127.0.0.1:{stack.port('rabbitmq', 15672)}"
        _rabbit_request(management_url, "PUT", "/api/queues/%2F/orbitkb.events", {"durable": False})
        published = _rabbit_request(
            management_url, "POST", "/api/exchanges/%2F/amq.default/publish",
            {"routing_key": "orbitkb.events", "payload": "indexed", "payload_encoding": "string", "properties": {}},
        )
        assert published == {"routed": True}
        messages = _rabbit_request(
            management_url, "POST", "/api/queues/%2F/orbitkb.events/get",
            {"count": 1, "ackmode": "ack_requeue_false", "encoding": "auto", "truncate": 50000},
        )
        assert messages[0]["payload"] == "indexed"

        _write_representative_source(tmp_path)
        result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")
        assert {(edge.kind, edge.target) for edge in result.edges} >= {
            ("writes", "jdbc.update"), ("writes", "mongo.save"),
        }
        assert [(item.direction, item.channel, item.routing_key) for item in result.message_contracts] == [
            ("publishes", "orders", "order.indexed"),
        ]
    finally:
        stack.stop()


def _rabbit_request(base_url: str, method: str, path: str, payload: dict) -> object:
    body = json.dumps(payload).encode("utf-8")
    credentials = base64.b64encode(b"guest:guest").decode("ascii")
    request = Request(
        f"{base_url}{path}", data=body, method=method,
        headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 -- localhost port comes from this Compose stack.
        response_body = response.read()
    return json.loads(response_body) if response_body else None


def _write_representative_source(root: Path) -> None:
    (root / "Infrastructure.java").write_text(
        '''class Infrastructure {
  private final JdbcTemplate jdbc;
  private final MongoTemplate mongo;
  private final RabbitTemplate rabbit;
  void persistAndPublish(Order event) {
    jdbc.update("insert into orders values (?)", event.id());
    mongo.save(event);
    rabbit.convertAndSend("orders", "order.indexed", event);
  }
}
''',
        encoding="utf-8",
    )
