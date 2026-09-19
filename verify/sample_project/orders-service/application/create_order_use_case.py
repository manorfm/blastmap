"""Create-order use case: the synchronous checkout flow."""
from __future__ import annotations

import uuid

from domain.order import Order
from domain.ports import EventPublisherPort, InventoryPort, OrderRepositoryPort, PaymentGatewayPort


class OutOfStockError(Exception):
    """Raised when inventory cannot cover the requested quantity."""


class PaymentDeclinedError(Exception):
    """Raised when the payment gateway rejects the charge."""


class CreateOrderUseCase:
    def __init__(
        self,
        inventory: InventoryPort,
        payments: PaymentGatewayPort,
        repo: OrderRepositoryPort,
        events: EventPublisherPort,
    ) -> None:
        self._inventory = inventory
        self._payments = payments
        self._repo = repo
        self._events = events

    def execute(
        self,
        sku: str,
        qty: int,
        amount: float,
        currency: str,
        payment_token: str,
    ) -> Order:
        if not self._inventory.check_stock(sku, qty):
            raise OutOfStockError(f"sku {sku} cannot cover requested qty {qty}")

        order_id = str(uuid.uuid4())

        charge_result = self._payments.charge(
            amount=amount, currency=currency, payment_token=payment_token, order_id=order_id,
        )
        if charge_result.status != "charged":
            raise PaymentDeclinedError(f"payment gateway declined order {order_id}")

        order = Order(
            order_id=order_id,
            sku=sku,
            qty=qty,
            amount=amount,
            currency=currency,
            payment_token=payment_token,
        )
        order.confirm()
        self._repo.save(order)

        self._events.publish(
            "order.created",
            {"order_id": order.order_id, "sku": order.sku, "qty": order.qty},
        )

        return order
