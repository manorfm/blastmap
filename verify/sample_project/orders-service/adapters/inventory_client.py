"""Synchronous HTTP adapter for the inventory-service availability check."""
from __future__ import annotations

import requests

from domain.ports import InventoryPort

_INVENTORY_BASE_URL = "http://inventory-service:8080"


class InventoryClient(InventoryPort):
    def check_stock(self, sku: str, qty: int) -> bool:
        response = requests.get(
            f"{_INVENTORY_BASE_URL}/stock/{sku}", params={"qty": qty}, timeout=3
        )
        response.raise_for_status()
        body = response.json()
        return bool(body.get("available")) and body.get("quantity", 0) >= qty
