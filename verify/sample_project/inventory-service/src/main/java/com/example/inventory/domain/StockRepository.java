package com.example.inventory.domain;

import java.util.Optional;

public interface StockRepository {

    Optional<Stock> findBySku(String sku);

    void save(Stock stock);
}
