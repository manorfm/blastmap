package com.example.inventory;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class StockController {

    private final StockRepository stockRepository;

    public StockController(StockRepository stockRepository) {
        this.stockRepository = stockRepository;
    }

    @GetMapping("/stock/{sku}")
    public StockResponse getStock(@PathVariable String sku) {
        Stock stock = stockRepository.findBySku(sku);
        return new StockResponse(stock.getSku(), stock.getQuantity());
    }
}
