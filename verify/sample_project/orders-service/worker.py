"""Standalone worker process: runs the saga-compensation Kafka consumer.

Deployed as a separate process from the FastAPI web process (`main.py`) —
the usual pattern for a service that both serves HTTP and reacts to events.
Not invoked by any test; just needs to import and read correctly.
"""
from __future__ import annotations

from adapters.kafka_event_consumer import KafkaEventConsumer
from adapters.kafka_event_publisher import KafkaEventPublisher
from adapters.postgres_order_repository import PostgresOrderRepository
from application.cancel_order_use_case import CancelOrderUseCase


def main() -> None:
    repo = PostgresOrderRepository()
    events = KafkaEventPublisher()
    cancel_order_use_case = CancelOrderUseCase(repo=repo, events=events)

    consumer = KafkaEventConsumer(cancel_order_use_case=cancel_order_use_case)
    consumer.consume_forever()


if __name__ == "__main__":
    main()
