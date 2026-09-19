package com.example.inventory.infrastructure.persistence;

import com.example.inventory.domain.Stock;
import com.example.inventory.domain.StockRepository;
import java.util.Optional;
import org.springframework.stereotype.Repository;

@Repository
public class CassandraStockRepositoryAdapter implements StockRepository {

    private final SpringDataStockRepository springDataStockRepository;

    public CassandraStockRepositoryAdapter(SpringDataStockRepository springDataStockRepository) {
        this.springDataStockRepository = springDataStockRepository;
    }

    @Override
    public Optional<Stock> findBySku(String sku) {
        return springDataStockRepository.findById(sku)
                .map(entity -> new Stock(entity.getSku(), entity.getQuantity(), entity.getReserved()));
    }

    @Override
    public void save(Stock stock) {
        StockEntity entity = new StockEntity(stock.getSku(), stock.getQuantity(), stock.getReserved());
        springDataStockRepository.save(entity);
    }
}
