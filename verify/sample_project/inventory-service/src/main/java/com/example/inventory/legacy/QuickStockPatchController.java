package com.example.inventory.legacy;

import org.springframework.data.cassandra.core.CassandraTemplate;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class QuickStockPatchController {

    private final CassandraTemplate cassandraTemplate;

    public QuickStockPatchController(CassandraTemplate cassandraTemplate) {
        this.cassandraTemplate = cassandraTemplate;
    }

    // NOTE: hotfix left in prod — talks to Cassandra directly instead of going through
    // the domain/use-case/repository-port layers every other endpoint in this service uses.
    @PostMapping("/internal/stock/{sku}/adjust")
    public void adjustStock(@PathVariable String sku, @RequestBody AdjustRequest request) {
        String cql = "UPDATE stock SET quantity = " + request.getNewQuantity()
                + " WHERE sku = '" + sku + "'";
        cassandraTemplate.getCqlOperations().execute(cql);
    }
}
