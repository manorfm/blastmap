"""Inbound HTTP adapter: the orders-service REST API.

Wiring note: `main.py` (the composition root) sets the module-level use-case
singletons below after constructing the concrete adapters. Handlers are kept
as methods of a single class so the discovery tool's endpoint-to-component
scan resolves all three routes to the same `OrdersController` component.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from adapters.notification_client import NotificationClient
from adapters.payments_client import PaymentsClient
from application.cancel_order_use_case import CancelOrderUseCase, OrderNotFoundError
from application.create_order_use_case import CreateOrderUseCase, OutOfStockError, PaymentDeclinedError
from application.get_order_use_case import GetOrderUseCase
from domain.ports import EventPublisherPort, InventoryPort, OrderRepositoryPort

router = APIRouter()

# Populated by main.py during application startup. The payments adapter is
# built per-request (it needs the caller's forwarded bearer token), so only
# the collaborators that don't vary per request are shared singletons here.
inventory_client: InventoryPort | None = None
order_repository: OrderRepositoryPort | None = None
event_publisher: EventPublisherPort | None = None
get_order_use_case: GetOrderUseCase | None = None
cancel_order_use_case: CancelOrderUseCase | None = None
notification_client = NotificationClient()


class OrdersController:
    @router.post("/orders")
    def create_order(payload: dict, authorization: str = Header(...)):
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")

        try:
            sku = payload["sku"]
            qty = payload["qty"]
            amount = payload["amount"]
            currency = payload["currency"]
            payment_token = payload["payment_token"]
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=f"missing field: {exc}") from exc

        payments = PaymentsClient(bearer_token=authorization)
        use_case = CreateOrderUseCase(
            inventory=inventory_client, payments=payments, repo=order_repository, events=event_publisher,
        )

        try:
            order = use_case.execute(
                sku=sku, qty=qty, amount=amount, currency=currency, payment_token=payment_token,
            )
        except OutOfStockError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PaymentDeclinedError as exc:
            raise HTTPException(status_code=402, detail=str(exc)) from exc

        notification_client.send_order_confirmation(order_id=order.order_id, recipient=payment_token)

        return {"order_id": order.order_id, "status": order.status.value}

    @router.get("/orders/{order_id}")
    def get_order(order_id: str):
        order = get_order_use_case.execute(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="order not found")
        return {
            "order_id": order.order_id,
            "sku": order.sku,
            "qty": order.qty,
            "amount": order.amount,
            "currency": order.currency,
            "status": order.status.value,
        }

    @router.post("/orders/{order_id}/cancel")
    def cancel_order(order_id: str):
        try:
            cancel_order_use_case.execute(order_id=order_id, reason="manual_cancel")
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"order_id": order_id, "status": "cancelled"}
