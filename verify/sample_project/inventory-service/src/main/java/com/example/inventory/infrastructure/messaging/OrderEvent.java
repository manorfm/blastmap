package com.example.inventory.infrastructure.messaging;

/**
 * Minimal parser for the order.created / order.cancelled payloads published
 * by orders-service. Payload shape: {"sku": "...", "qty": N}.
 */
public class OrderEvent {

    private final String sku;
    private final int qty;

    private OrderEvent(String sku, int qty) {
        this.sku = sku;
        this.qty = qty;
    }

    public static OrderEvent parse(String message) {
        String sku = extract(message, "sku");
        String qtyRaw = extract(message, "qty");
        int qty = qtyRaw.isEmpty() ? 0 : Integer.parseInt(qtyRaw);
        return new OrderEvent(sku, qty);
    }

    private static String extract(String json, String field) {
        String marker = "\"" + field + "\"";
        int idx = json.indexOf(marker);
        if (idx < 0) {
            return "";
        }
        int colon = json.indexOf(':', idx);
        int end = json.indexOf(',', colon);
        if (end < 0) {
            end = json.indexOf('}', colon);
        }
        return json.substring(colon + 1, end).replaceAll("[\"\\s]", "");
    }

    public String getSku() {
        return sku;
    }

    public int getQty() {
        return qty;
    }
}
