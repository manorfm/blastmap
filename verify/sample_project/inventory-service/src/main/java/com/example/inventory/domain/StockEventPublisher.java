package com.example.inventory.domain;

public interface StockEventPublisher {

    void publishStockReserved(String sku, int quantity);

    void publishStockReservationFailed(String sku, int requestedQuantity);
}
