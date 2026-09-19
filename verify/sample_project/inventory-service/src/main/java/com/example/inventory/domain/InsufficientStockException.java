package com.example.inventory.domain;

public class InsufficientStockException extends Exception {

    public InsufficientStockException(String sku, int requested, int available) {
        super("Insufficient stock for sku=" + sku + ": requested=" + requested + ", available=" + available);
    }
}
