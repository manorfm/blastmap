package com.example.inventory.infrastructure.persistence;

import org.springframework.data.cassandra.core.mapping.PrimaryKey;
import org.springframework.data.cassandra.core.mapping.Table;

@Table("stock")
public class StockEntity {

    @PrimaryKey
    private String sku;

    private int quantity;

    private int reserved;

    public StockEntity() {
    }

    public StockEntity(String sku, int quantity, int reserved) {
        this.sku = sku;
        this.quantity = quantity;
        this.reserved = reserved;
    }

    public String getSku() {
        return sku;
    }

    public void setSku(String sku) {
        this.sku = sku;
    }

    public int getQuantity() {
        return quantity;
    }

    public void setQuantity(int quantity) {
        this.quantity = quantity;
    }

    public int getReserved() {
        return reserved;
    }

    public void setReserved(int reserved) {
        this.reserved = reserved;
    }
}
