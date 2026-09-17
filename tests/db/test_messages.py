from pathlib import Path

from blastmap.db.connection import open_db
from blastmap.db.repositories import messages as messages_repo
from blastmap.db.repositories import services as services_repo

EVIDENCE = [{"file": "main.py", "start_line": 10, "end_line": 20}]


def test_message_links(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    orders_id = services_repo.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    notif_id = services_repo.ensure_service(conn, "notification-service", "/tmp/notif", "python")

    messages_repo.replace_messages(
        conn, orders_id, [{"direction": "publishes", "channel": "order_created", "shape_json": {}, "description": "d"}], EVIDENCE,
    )
    messages_repo.replace_messages(
        conn, notif_id, [{"direction": "consumes", "channel": "order_created", "shape_json": {}, "description": "d"}], EVIDENCE,
    )

    links = messages_repo.list_message_links(conn, orders_id)
    assert len(links) == 1
    assert links[0]["channel"] == "order_created"
    assert links[0]["local_direction"] == "publishes"
    assert links[0]["other_service"] == "notification-service"

    reverse_links = messages_repo.list_message_links(conn, notif_id)
    assert len(reverse_links) == 1
    assert reverse_links[0]["local_direction"] == "consumes"
    assert reverse_links[0]["other_service"] == "orders-service"


def test_messages_store_evidence(tmp_path: Path):
    conn = open_db(tmp_path / "test.db")
    service_id = services_repo.ensure_service(conn, "orders-service", "/tmp/orders", "python")
    messages_repo.replace_messages(
        conn, service_id, [{"direction": "publishes", "channel": "order_created", "shape_json": [], "description": "d"}], EVIDENCE,
    )

    messages = messages_repo.list_messages(conn, service_id)
    assert messages[0]["evidence_json"] == '[{"file": "main.py", "start_line": 10, "end_line": 20}]'
