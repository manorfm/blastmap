"""Cancel-order use case.

Shared by two triggers: the manual `/orders/{order_id}/cancel` HTTP endpoint
and the saga-compensation Kafka consumer (payment.failed / stock.reservation_failed).
"""
from __future__ import annotations

from domain.ports import EventPublisherPort, OrderRepositoryPort


class OrderNotFoundError(Exception):
    """Raised when the referenced order does not exist."""


class CancelOrderUseCase:
    def __init__(self, repo: OrderRepositoryPort, events: EventPublisherPort) -> None:
        self._repo = repo
        self._events = events

    def execute(self, order_id: str, reason: str) -> None:
        order = self._repo.get(order_id)
        if order is None:
            raise OrderNotFoundError(f"order {order_id} not found")

        order.cancel(reason)
        self._repo.save(order)

        self._events.publish(
            "order.cancelled",
            {"order_id": order.order_id, "reason": reason},
        )
