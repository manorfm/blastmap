package com.example.inventory.domain;

public class Stock {

    private final String sku;
    private int quantity;
    private int reserved;

    public Stock(String sku, int quantity, int reserved) {
        this.sku = sku;
        this.quantity = quantity;
        this.reserved = reserved;
    }

    public boolean isAvailable(int qty) {
        return (quantity - reserved) >= qty;
    }

    public void reserve(int qty) throws InsufficientStockException {
        if (!isAvailable(qty)) {
            throw new InsufficientStockException(sku, qty, quantity - reserved);
        }
        this.reserved += qty;
    }

    public void release(int qty) {
        this.reserved = Math.max(0, this.reserved - qty);
    }

    public String getSku() {
        return sku;
    }

    public int getQuantity() {
        return quantity;
    }

    public int getReserved() {
        return reserved;
    }
}
