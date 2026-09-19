package com.example.inventory.infrastructure.messaging;

import com.example.inventory.domain.StockEventPublisher;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

@Component
public class StockEventsPublisher implements StockEventPublisher {

    private final KafkaTemplate<String, String> kafkaTemplate;

    public StockEventsPublisher(KafkaTemplate<String, String> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    @Override
    public void publishStockReserved(String sku, int quantity) {
        String payload = "{\"sku\":\"" + sku + "\",\"qty\":" + quantity + "}";
        kafkaTemplate.send("stock.reserved", payload);
    }

    @Override
    public void publishStockReservationFailed(String sku, int requestedQuantity) {
        String payload = "{\"sku\":\"" + sku + "\",\"qty\":" + requestedQuantity + "}";
        kafkaTemplate.send("stock.reservation_failed", payload);
    }
}
