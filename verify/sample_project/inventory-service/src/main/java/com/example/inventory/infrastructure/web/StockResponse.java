package com.example.inventory.infrastructure.web;

public class StockResponse {

    private String sku;
    private boolean available;
    private int quantity;

    public StockResponse(String sku, boolean available, int quantity) {
        this.sku = sku;
        this.available = available;
        this.quantity = quantity;
    }

    public String getSku() {
        return sku;
    }

    public boolean isAvailable() {
        return available;
    }

    public int getQuantity() {
        return quantity;
    }
}
