"""Kafka-backed event publisher adapter."""
from __future__ import annotations

import json

from kafka import KafkaProducer

from domain.ports import EventPublisherPort


class KafkaEventPublisher(EventPublisherPort):
    def __init__(self, bootstrap_servers: str = "localhost:9092") -> None:
        self.producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers,
            client_id="orders-service",
        )

    def publish(self, topic: str, payload: dict) -> None:
        self.producer.send(topic, json.dumps(payload).encode("utf-8"))
        self.producer.flush()
