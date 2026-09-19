package com.example.inventory.usecase;

import com.example.inventory.domain.Stock;
import com.example.inventory.domain.StockRepository;
import java.util.Optional;
import org.springframework.stereotype.Component;

@Component
public class CheckStockUseCase {

    private final StockRepository stockRepository;

    public CheckStockUseCase(StockRepository stockRepository) {
        this.stockRepository = stockRepository;
    }

    public boolean isAvailable(String sku, int qty) {
        Optional<Stock> stock = stockRepository.findBySku(sku);
        return stock.map(s -> s.isAvailable(qty)).orElse(false);
    }

    public int currentQuantity(String sku) {
        return stockRepository.findBySku(sku).map(Stock::getQuantity).orElse(0);
    }
}
