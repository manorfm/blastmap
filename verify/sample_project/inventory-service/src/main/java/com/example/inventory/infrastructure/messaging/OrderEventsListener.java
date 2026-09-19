package com.example.inventory.infrastructure.messaging;

import com.example.inventory.usecase.ReleaseStockUseCase;
import com.example.inventory.usecase.ReserveStockUseCase;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Component
public class OrderEventsListener {

    private final ReserveStockUseCase reserveStockUseCase;
    private final ReleaseStockUseCase releaseStockUseCase;

    public OrderEventsListener(ReserveStockUseCase reserveStockUseCase, ReleaseStockUseCase releaseStockUseCase) {
        this.reserveStockUseCase = reserveStockUseCase;
        this.releaseStockUseCase = releaseStockUseCase;
    }

    @KafkaListener(topics = "order.created")
    public void onOrderCreated(String message) {
        OrderEvent event = OrderEvent.parse(message);
        reserveStockUseCase.reserve(event.getSku(), event.getQty());
    }

    @KafkaListener(topics = "order.cancelled")
    public void onOrderCancelled(String message) {
        OrderEvent event = OrderEvent.parse(message);
        releaseStockUseCase.release(event.getSku(), event.getQty());
    }
}
