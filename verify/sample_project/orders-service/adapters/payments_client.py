"""Synchronous HTTP adapter for the payments-service charge endpoint."""
from __future__ import annotations

import requests

from domain.ports import ChargeResult, PaymentGatewayPort

_PAYMENTS_BASE_URL = "http://payments-service:3000"


class PaymentsClient(PaymentGatewayPort):
    def __init__(self, bearer_token: str) -> None:
        self._bearer_token = bearer_token

    def charge(self, amount: float, currency: str, payment_token: str, order_id: str) -> ChargeResult:
        response = requests.post(
            f"{_PAYMENTS_BASE_URL}/charge",
            json={
                "amount": amount,
                "currency": currency,
                "payment_token": payment_token,
                "order_id": order_id,
            },
            headers={"Authorization": self._bearer_token},
            timeout=5,
        )
        if response.status_code >= 300:
            return ChargeResult(transaction_id="", status="declined")

        body = response.json()
        return ChargeResult(transaction_id=body.get("transactionId", ""), status=body.get("status", "declined"))
