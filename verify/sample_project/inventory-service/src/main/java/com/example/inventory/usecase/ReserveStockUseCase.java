package com.example.inventory.usecase;

import com.example.inventory.domain.InsufficientStockException;
import com.example.inventory.domain.Stock;
import com.example.inventory.domain.StockEventPublisher;
import com.example.inventory.domain.StockRepository;
import java.util.Optional;
import org.springframework.stereotype.Component;

@Component
public class ReserveStockUseCase {

    private final StockRepository stockRepository;
    private final StockEventPublisher stockEventPublisher;

    public ReserveStockUseCase(StockRepository stockRepository, StockEventPublisher stockEventPublisher) {
        this.stockRepository = stockRepository;
        this.stockEventPublisher = stockEventPublisher;
    }

    public void reserve(String sku, int qty) {
        Optional<Stock> maybeStock = stockRepository.findBySku(sku);
        if (maybeStock.isEmpty()) {
            stockEventPublisher.publishStockReservationFailed(sku, qty);
            return;
        }

        Stock stock = maybeStock.get();
        try {
            stock.reserve(qty);
            stockRepository.save(stock);
            stockEventPublisher.publishStockReserved(sku, qty);
        } catch (InsufficientStockException e) {
            stockEventPublisher.publishStockReservationFailed(sku, qty);
        }
    }
}
