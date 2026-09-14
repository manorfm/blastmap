package com.example.inventory;

public class StockResponse {
    private final String sku;
    private final int quantity;

    public StockResponse(String sku, int quantity) {
        this.sku = sku;
        this.quantity = quantity;
    }

    public String getSku() {
        return sku;
    }

    public int getQuantity() {
        return quantity;
    }
}
