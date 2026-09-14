package com.example.inventory;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;

@Entity
public class Stock {
    @Id
    private String sku;
    private int quantity;

    public String getSku() {
        return sku;
    }

    public int getQuantity() {
        return quantity;
    }
}
