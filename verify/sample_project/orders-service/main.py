from fastapi import FastAPI, Header, HTTPException
import requests
from kafka import KafkaProducer
import json

app = FastAPI()
producer = KafkaProducer(bootstrap_servers="localhost:9092")


@app.post("/orders")
def create_order(payload: dict, authorization: str = Header(...)):
    """Create a new order: charge the customer and reserve stock."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    amount = payload["amount"]
    currency = payload["currency"]
    payment_token = payload["payment_token"]
    sku = payload["sku"]
    qty = payload["qty"]

    charge_resp = requests.post(
        "http://payments-service/charge",
        json={"amount": amount, "currency": currency, "payment_token": payment_token},
    )
    charge_resp.raise_for_status()

    stock_resp = requests.get(f"http://inventory-service/stock/{sku}", params={"qty": qty})
    stock_resp.raise_for_status()

    order_id = "ord_123"
    producer.send("order_created", json.dumps({"order_id": order_id, "sku": sku, "qty": qty}).encode())

    return {"order_id": order_id, "status": "confirmed"}
