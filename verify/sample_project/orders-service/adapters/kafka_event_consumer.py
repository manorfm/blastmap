"""Saga-compensation consumer: cancels orders when a downstream step fails.

Listens for payment.failed (from payments-service) and
stock.reservation_failed (from inventory-service) and triggers
CancelOrderUseCase for the affected order.
"""
from __future__ import annotations

import json

from kafka import KafkaConsumer

from application.cancel_order_use_case import CancelOrderUseCase

_TOPICS = ["payment.failed", "stock.reservation_failed"]


class KafkaEventConsumer:
    def __init__(self, cancel_order_use_case: CancelOrderUseCase, bootstrap_servers: str = "localhost:9092") -> None:
        self._cancel_order_use_case = cancel_order_use_case
        self.consumer = KafkaConsumer(
            bootstrap_servers=bootstrap_servers,
            client_id="orders-service",
            group_id="orders-service-saga",
        )
        self.consumer.subscribe(_TOPICS)

    def consume_forever(self) -> None:
        for message in self.consumer:
            event = json.loads(message.value.decode("utf-8"))
            order_id = event.get("order_id")
            if not order_id:
                continue
            self._cancel_order_use_case.execute(
                order_id=order_id, reason=f"saga_compensation:{message.topic}"
            )
