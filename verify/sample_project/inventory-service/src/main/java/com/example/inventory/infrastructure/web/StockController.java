package com.example.inventory.infrastructure.web;

import com.example.inventory.usecase.CheckStockUseCase;
import com.example.inventory.usecase.ReserveStockUseCase;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class StockController {

    private final CheckStockUseCase checkStockUseCase;
    private final ReserveStockUseCase reserveStockUseCase;

    public StockController(CheckStockUseCase checkStockUseCase, ReserveStockUseCase reserveStockUseCase) {
        this.checkStockUseCase = checkStockUseCase;
        this.reserveStockUseCase = reserveStockUseCase;
    }

    @GetMapping("/stock/{sku}")
    public StockResponse getStock(@PathVariable String sku, @RequestParam int qty) {
        boolean available = checkStockUseCase.isAvailable(sku, qty);
        int quantity = checkStockUseCase.currentQuantity(sku);
        return new StockResponse(sku, available, quantity);
    }

    @PostMapping("/stock/{sku}/reserve")
    public StockResponse reserveStock(@PathVariable String sku, @RequestBody ReserveRequest request) {
        reserveStockUseCase.reserve(sku, request.getQty());
        boolean available = checkStockUseCase.isAvailable(sku, 0);
        int quantity = checkStockUseCase.currentQuantity(sku);
        return new StockResponse(sku, available, quantity);
    }
}
