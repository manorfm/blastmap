package com.example.inventory;

import org.springframework.data.jpa.repository.JpaRepository;

public interface StockRepository extends JpaRepository<Stock, String> {
    Stock findBySku(String sku);
}
