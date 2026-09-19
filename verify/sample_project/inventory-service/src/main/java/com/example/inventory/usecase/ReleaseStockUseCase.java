package com.example.inventory.usecase;

import com.example.inventory.domain.Stock;
import com.example.inventory.domain.StockRepository;
import java.util.Optional;
import org.springframework.stereotype.Component;

@Component
public class ReleaseStockUseCase {

    private final StockRepository stockRepository;

    public ReleaseStockUseCase(StockRepository stockRepository) {
        this.stockRepository = stockRepository;
    }

    public void release(String sku, int qty) {
        Optional<Stock> maybeStock = stockRepository.findBySku(sku);
        maybeStock.ifPresent(stock -> {
            stock.release(qty);
            stockRepository.save(stock);
        });
    }
}
