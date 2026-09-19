package com.example.inventory.infrastructure.persistence;

import org.springframework.data.cassandra.repository.CassandraRepository;

public interface SpringDataStockRepository extends CassandraRepository<StockEntity, String> {
}
