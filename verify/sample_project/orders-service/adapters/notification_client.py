"""Client for the external notify-hub vendor.

Called independently by both orders-service and payments-service — that
duplication is intentional, not shared code, per the cross-service contract.
"""
from __future__ import annotations

import requests

_NOTIFY_HUB_URL = "https://notify-hub.vendor.io/v1/send"


class NotificationClient:
    def send_order_confirmation(self, order_id: str, recipient: str) -> None:
        response = requests.post(
            _NOTIFY_HUB_URL,
            json={
                "recipient": recipient,
                "template_id": "order-confirmed",
                "order_id": order_id,
            },
            timeout=3,
        )
        response.raise_for_status()
