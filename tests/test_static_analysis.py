from pathlib import Path

from orbitkb.analysis.engine import StaticAnalysisEngine


def test_go_analyzer_maps_route_to_internal_and_persistence_flow(tmp_path: Path):
    source = tmp_path / "main.go"
    source.write_text(
        '''package main
type Orders struct{}
func (o *Orders) Create() { o.useCase.Execute(); o.repo.Save() }
func main() { router.POST("/orders", orders.Create) }
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "go")

    entry = result.entrypoints[0]
    assert (entry.kind, entry.method, entry.name) == ("http", "POST", "/orders")
    assert entry.symbol.endswith("Create")
    assert {(edge.kind, edge.target) for edge in result.edges} >= {
        ("invokes", "o.useCase.Execute"),
        ("writes", "o.repo.Save"),
    }


def test_go_analyzer_classifies_explicit_gorm_database_operations(tmp_path: Path):
    (tmp_path / "orders.go").write_text(
        '''package orders
func FindOrder(db *gorm.DB, id string) { db.First(&Order{}, id) }
func CreateOrder(db *gorm.DB, order Order) { db.Create(&order) }
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "go")

    assert {(edge.source, edge.target, edge.kind) for edge in result.edges} >= {
        ("orders.FindOrder", "db.First", "reads"),
        ("orders.CreateOrder", "db.Create", "writes"),
    }


def test_go_analyzer_classifies_simple_gorm_fluent_operations(tmp_path: Path):
    (tmp_path / "orders.go").write_text(
        '''package orders
func FindOrder(db *gorm.DB, status string) { db.Where("status = ?", status).First(&Order{}) }
func CreateOrder(ctx context.Context, db *gorm.DB, order Order) { db.WithContext(ctx).Create(&order) }
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "go")

    assert {(edge.source, edge.target, edge.kind) for edge in result.edges} >= {
        ("orders.FindOrder", 'db.Where("status = ?", status).First', "reads"),
        ("orders.CreateOrder", "db.WithContext(ctx).Create", "writes"),
    }


def test_go_analyzer_classifies_explicit_database_sql_operations(tmp_path: Path):
    (tmp_path / "orders.go").write_text(
        '''package orders
func FindOrder(db *sql.DB, id string) { return db.QueryRowContext(ctx, "select id from orders where id = ?", id) }
func CreateOrder(tx *sql.Tx, id string) { tx.ExecContext(ctx, "insert into orders(id) values(?)", id) }
func Unproven(client Client, id string) { client.ExecContext(ctx, "insert into orders(id) values(?)", id) }
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "go")

    assert {(edge.source, edge.target, edge.kind) for edge in result.edges} >= {
        ("orders.FindOrder", "db.QueryRowContext", "reads"),
        ("orders.CreateOrder", "tx.ExecContext", "writes"),
        ("orders.Unproven", "client.ExecContext", "invokes"),
    }


def test_kotlin_spring_analyzer_finds_constructor_injection_and_route(tmp_path: Path):
    source = tmp_path / "OrdersController.kt"
    source.write_text(
        '''@RestController
class OrdersController(private val useCase: CreateOrderUseCase) {
  @PostMapping("/orders")
  fun create(request: OrderRequest) = useCase.execute(request)
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert [(entry.method, entry.name) for entry in result.entrypoints] == [("POST", "/orders")]
    assert any(edge.kind == "injects" and edge.target == "CreateOrderUseCase" for edge in result.edges)
    assert any(edge.kind == "invokes" and edge.target == "useCase.execute" for edge in result.edges)


def test_native_literal_route_prefixes_are_composed(tmp_path: Path):
    (tmp_path / "OrdersController.java").write_text(
        '''@RequestMapping("/api") class OrdersController {
  @PostMapping("/orders") Order create(Order order) { return order; }
}
''',
        encoding="utf-8",
    )
    (tmp_path / "routes.go").write_text(
        '''package api
func Create() {}
func register() { orders := router.Group("/api/orders"); orders.POST("/create", Create) }
''',
        encoding="utf-8",
    )

    java = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")
    go = StaticAnalysisEngine().analyze(tmp_path, "go")

    assert any(entry.name == "/api/orders" for entry in java.entrypoints)
    assert any(entry.name == "/api/orders/create" for entry in go.entrypoints)


def test_node_graphql_analyzer_exposes_mutation_and_rabbit_publish(tmp_path: Path):
    source = tmp_path / "resolvers.ts"
    source.write_text(
        '''export const resolvers = {
  Mutation: { createOrder: (_: unknown, input: CreateOrderInput, { service }) => { service.create(input); channel.publish("orders", "created", input, { headers: { schema_version: "1" } }); } }
};
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert [(entry.kind, entry.method, entry.name) for entry in result.entrypoints] == [
        ("graphql", "MUTATION", "createOrder")
    ]
    assert any(edge.kind == "publishes" and edge.target == "channel.publish" for edge in result.edges)
    assert [(item.channel, item.routing_key, item.payload_type, item.message_version) for item in result.message_contracts] == [
        ("orders", "created", "CreateOrderInput", "1"),
    ]


def test_spring_analyzers_extract_literal_amqp_publications_with_declared_payloads(tmp_path: Path):
    (tmp_path / "OrderPublisher.java").write_text(
        '''class OrderPublisher {
  RabbitTemplate publisher;
  void publish(OrderCreated event) { publisher.convertAndSend("orders", "order.created", event, message -> { message.getMessageProperties().setHeader("schema_version", "1"); return message; }); }
}
''',
        encoding="utf-8",
    )
    (tmp_path / "PaymentPublisher.kt").write_text(
        '''class PaymentPublisher(private val publisher: AmqpTemplate) {
  fun publish(event: PaymentCreated) { publisher.convertAndSend("payments", "payment.created", event) }
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert [(item.channel, item.routing_key, item.payload_type, item.message_version) for item in result.message_contracts] == [
        ("orders", "order.created", "OrderCreated", "1"),
        ("payments", "payment.created", "PaymentCreated", None),
    ]


def test_go_analyzer_extracts_literal_amqp_publications_with_declared_payloads(tmp_path: Path):
    source = tmp_path / "publisher.go"
    source.write_text(
        '''package orders
func publish(channel *amqp.Channel, event OrderCreated) error {
  return channel.Publish("orders", "order.created", false, false, amqp.Publishing{Body: event, Headers: amqp.Table{"schema_version": "1"}})
}
func publishWithContext(ctx context.Context, channel *amqp.Channel, event OrderCreated) error {
  return channel.PublishWithContext(ctx, "orders", "order.created", false, false, amqp.Publishing{Body: event, Headers: amqp.Table{"schema_version": "1"}})
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "go")

    assert [(item.channel, item.routing_key, item.payload_type, item.message_version) for item in result.message_contracts] == [
        ("orders", "order.created", "OrderCreated", "1"),
        ("orders", "order.created", "OrderCreated", "1"),
    ]


def test_node_analyzer_exposes_rabbit_consumer_and_its_bounded_handler_flow(tmp_path: Path):
    source = tmp_path / "consumer.ts"
    source.write_text(
        '''channel.assertExchange("orders", "topic");
channel.assertQueue("orders.created", { deadLetterRoutingKey: "orders.dlq", messageTtl: 5000 });
channel.bindQueue("orders.created", "orders", "order.created");
channel.consume("orders.created", async (message: OrderCreated) => {
  await orderService.handle(message);
});
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert [(entry.kind, entry.method, entry.name) for entry in result.entrypoints] == [
        ("message", "CONSUME", "orders.created")
    ]
    assert any(edge.source == "message.consume:orders.created" and edge.target == "orderService.handle" for edge in result.edges)
    assert result.contracts["message.consume:orders.created"]["payload"] == {
        "name": "message", "type": "OrderCreated", "required": True,
    }
    assert result.contracts["message.consume:orders.created"]["dead_letter_routing_key"] == "orders.dlq"
    assert result.contracts["message.consume:orders.created"]["retry_delay_ms"] == 5000
    assert result.contracts["message.consume:orders.created"]["bindings"] == [
        {"exchange": "orders", "routing_key": "order.created"},
    ]


def test_go_analyzer_links_a_literal_amqp_queue_binding_to_its_consumer(tmp_path: Path):
    source = tmp_path / "consumer.go"
    source.write_text(
        '''package orders
func consume(channel *amqp.Channel) {
  channel.QueueDeclare("orders.created", true, false, false, false, amqp.Table{"x-dead-letter-routing-key": "orders.dlq", "x-message-ttl": 5000})
  channel.QueueBind("orders.created", "order.created", "orders", false, nil)
  channel.Consume("orders.created", "", false, false, false, false, func(message amqp.Delivery) {})
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "go")

    assert result.contracts["message.consume:orders.created"]["bindings"] == [
        {"exchange": "orders", "routing_key": "order.created"},
    ]
    assert result.contracts["message.consume:orders.created"]["dead_letter_routing_key"] == "orders.dlq"
    assert result.contracts["message.consume:orders.created"]["retry_delay_ms"] == 5000


def test_kotlin_analyzer_exposes_rabbit_listener_and_its_handler_flow(tmp_path: Path):
    source = tmp_path / "OrderListener.kt"
    source.write_text(
        '''class OrderListener {
  @RabbitListener(queues = ["orders.created"])
  fun consume(message: String) { orderService.handle(message) }
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert [(entry.kind, entry.method, entry.name) for entry in result.entrypoints] == [
        ("message", "CONSUME", "orders.created")
    ]
    assert any(edge.source == "OrderListener.consume" and edge.target == "orderService.handle" for edge in result.edges)
    assert result.contracts["OrderListener.consume"] == {
        "transport": "rabbitmq", "direction": "consumes", "queue": "orders.created",
        "payload": {"name": "message", "type": "String", "required": True},
    }


def test_java_analyzer_links_a_literal_spring_queue_binding_to_its_consumer(tmp_path: Path):
    source = tmp_path / "OrderListener.java"
    source.write_text(
        '''class QueueConfig {
  Queue orderQueue() { return new Queue("orders.created"); }
  TopicExchange orderExchange() { return new TopicExchange("orders"); }
  Binding orderBinding() { return BindingBuilder.bind(orderQueue()).to(orderExchange()).with("order.created"); }
}
class OrderListener {
  @RabbitListener(queues = "orders.created")
  void consume(OrderCreated message) { orderService.handle(message); }
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert result.contracts["OrderListener.consume"]["bindings"] == [
        {"exchange": "orders", "routing_key": "order.created"},
    ]


def test_kotlin_analyzer_links_a_literal_spring_queue_binding_to_its_consumer(tmp_path: Path):
    source = tmp_path / "OrderListener.kt"
    source.write_text(
        '''class QueueConfig {
  fun orderQueue() = Queue("orders.created")
  fun orderExchange() = TopicExchange("orders")
  fun orderBinding() = BindingBuilder.bind(orderQueue()).to(orderExchange()).with("order.created")
}
class OrderListener {
  @RabbitListener(queues = ["orders.created"])
  fun consume(message: OrderCreated) { orderService.handle(message) }
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert result.contracts["OrderListener.consume"]["bindings"] == [
        {"exchange": "orders", "routing_key": "order.created"},
    ]


def test_service_create_is_not_misclassified_as_direct_persistence(tmp_path: Path):
    source = tmp_path / "resolvers.ts"
    source.write_text(
        '''export const resolvers = {
  Mutation: { createOrder: (_, input, { service }) => service.create(input) }
};
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert any(edge.kind == "invokes" and edge.target == "service.create" for edge in result.edges)


def test_kotlin_analyzer_resolves_a_bounded_flow_across_injected_classes(tmp_path: Path):
    (tmp_path / "OrdersController.kt").write_text(
        '''class OrdersController(private val useCase: CreateOrderUseCase) {
  @PostMapping("/orders")
  fun create(request: OrderRequest) = useCase.execute(request)
}
''',
        encoding="utf-8",
    )
    (tmp_path / "CreateOrderUseCase.kt").write_text(
        '''class CreateOrderUseCase(private val orderRepository: OrderRepository) {
  fun execute(request: OrderRequest) { orderRepository.save(request) }
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert {(edge.source, edge.target, edge.kind) for edge in result.edges} >= {
        ("OrdersController.create", "CreateOrderUseCase.execute", "invokes"),
        ("CreateOrderUseCase.execute", "orderRepository.save", "writes"),
    }


def test_java_spring_analyzer_maps_controller_and_cross_file_use_case(tmp_path: Path):
    (tmp_path / "OrdersController.java").write_text(
        '''@RestController
class OrdersController {
  private final CreateOrderUseCase useCase;
  OrdersController(CreateOrderUseCase useCase) { this.useCase = useCase; }
  @PostMapping("/orders")
  Order create(Order order) { return useCase.execute(order); }
}
''',
        encoding="utf-8",
    )
    (tmp_path / "CreateOrderUseCase.java").write_text(
        '''class CreateOrderUseCase {
  private final OrderRepository repository;
  Order execute(Order order) { return repository.save(order); }
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert [(entry.method, entry.name, entry.symbol) for entry in result.entrypoints] == [
        ("POST", "/orders", "OrdersController.create")
    ]
    assert any(edge.target == "CreateOrderUseCase.execute" for edge in result.edges)
    assert any(edge.kind == "writes" and edge.target == "repository.save" for edge in result.edges)


def test_spring_analyzers_classify_only_locally_injected_repository_receivers(tmp_path: Path):
    (tmp_path / "Orders.java").write_text(
        '''class Orders {
  private final OrderRepository repository;
  Order find(String id) { return repository.findById(id); }
  Order save(Order order) { return repository.save(order); }
}
''',
        encoding="utf-8",
    )
    (tmp_path / "Payments.kt").write_text(
        '''class Payments(private val repository: PaymentRepository) {
  fun find(id: String) = repository.findById(id)
  fun save(payment: Payment) = repository.save(payment)
}
''',
        encoding="utf-8",
    )
    (tmp_path / "Unproven.java").write_text(
        '''class Unproven {
  Order save(Order order) { return repository.save(order); }
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert {(edge.source, edge.target, edge.kind) for edge in result.edges} >= {
        ("Orders.find", "repository.findById", "reads"),
        ("Orders.save", "repository.save", "writes"),
        ("Payments.find", "repository.findById", "reads"),
        ("Payments.save", "repository.save", "writes"),
        ("Unproven.save", "repository.save", "invokes"),
    }


def test_spring_data_derived_operations_require_a_local_repository_interface(tmp_path: Path):
    (tmp_path / "OrderRepository.java").write_text(
        '''interface OrderRepository extends JpaRepository<Order, String> {
  Order findByStatus(String status);
  long deleteByCustomerId(String customerId);
}
''',
        encoding="utf-8",
    )
    (tmp_path / "Orders.java").write_text(
        '''class Orders {
  private final OrderRepository repository;
  Order find(String status) { return repository.findByStatus(status); }
  long delete(String customerId) { return repository.deleteByCustomerId(customerId); }
}
''',
        encoding="utf-8",
    )
    (tmp_path / "Unproven.java").write_text(
        '''class Unproven {
  private final UnknownRepository repository;
  Order find(String status) { return repository.findByStatus(status); }
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert {(edge.source, edge.target, edge.kind) for edge in result.edges} >= {
        ("Orders.find", "repository.findByStatus", "reads"),
        ("Orders.delete", "repository.deleteByCustomerId", "writes"),
        ("Unproven.find", "repository.findByStatus", "invokes"),
    }


def test_spring_data_query_operations_require_local_repository_and_modifying_evidence(tmp_path: Path):
    (tmp_path / "OrderRepository.java").write_text(
        '''interface OrderRepository extends JpaRepository<Order, String> {
  @Query("select o from Order o where o.status = :status")
  Order findActive(String status);
  @Query("update Order o set o.archived = true") @Modifying
  int archiveExpired();
}
''',
        encoding="utf-8",
    )
    (tmp_path / "Orders.java").write_text(
        '''class Orders {
  private final OrderRepository repository;
  Order find(String status) { return repository.findActive(status); }
  int archive() { return repository.archiveExpired(); }
}
''',
        encoding="utf-8",
    )
    (tmp_path / "Unproven.java").write_text(
        '''class Unproven {
  private final UnknownRepository repository;
  Order find(String status) { return repository.findActive(status); }
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert {(edge.source, edge.target, edge.kind) for edge in result.edges} >= {
        ("Orders.find", "repository.findActive", "reads"),
        ("Orders.archive", "repository.archiveExpired", "writes"),
        ("Unproven.find", "repository.findActive", "invokes"),
    }


def test_graphql_schema_contract_is_linked_to_its_resolver_entrypoint(tmp_path: Path):
    (tmp_path / "resolvers.ts").write_text(
        '''export const resolvers = { Mutation: { createOrder: (_, input) => orderService.create(input) } };''',
        encoding="utf-8",
    )
    (tmp_path / "schema.graphql").write_text(
        '''type Mutation { createOrder(input: CreateOrderInput!): Order! }
input CreateOrderInput { sku: String! note: String }
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert result.contracts["Mutation.createOrder"] == {
        "arguments": [{
            "name": "input", "type": "CreateOrderInput", "required": True,
            "fields": [
                {"name": "sku", "type": "String", "required": True},
                {"name": "note", "type": "String", "required": False},
            ],
        }],
        "returns": {"type": "Order", "required": True},
    }


def test_graphql_contract_includes_local_interface_and_union_return_options(tmp_path: Path):
    (tmp_path / "resolvers.ts").write_text(
        '''export const resolvers = { Query: { node: () => null, search: () => [] } };''', encoding="utf-8",
    )
    (tmp_path / "schema.graphql").write_text(
        '''type Query { node: Node! search: [SearchResult!]! }
interface Node { id: ID! }
type User implements Node { id: ID! }
type Order implements Node { id: ID! }
union SearchResult = User | Order
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert result.contracts["Query.node"]["returns"]["possible_types"] == ["Order", "User"]
    assert result.contracts["Query.search"]["returns"]["possible_types"] == ["Order", "User"]


def test_graphql_schema_extensions_are_composed_across_local_files(tmp_path: Path):
    (tmp_path / "resolvers.ts").write_text(
        '''export const resolvers = { Query: { health: () => "ok", order: () => null } };''', encoding="utf-8",
    )
    (tmp_path / "base.graphql").write_text('''type Query { health: String! }''', encoding="utf-8")
    (tmp_path / "orders.graphql").write_text(
        "extend type Query { order(id: ID!): Order! }\ntype Order { id: ID! }\n", encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert result.contracts["Query.order"] == {
        "arguments": [{"name": "id", "type": "ID", "required": True, "fields": []}],
        "returns": {"type": "Order", "required": True},
    }


def test_java_spring_http_contract_keeps_declared_payload_validation_and_auth(tmp_path: Path):
    (tmp_path / "OrdersController.java").write_text(
        '''class OrdersController {
  @PostMapping("/orders") @PreAuthorize("hasRole('ORDER_WRITE')")
  Order create(@Valid @RequestBody CreateOrderRequest request) { return service.create(request); }
}
''',
        encoding="utf-8",
    )
    (tmp_path / "CreateOrderRequest.java").write_text(
        '''class CreateOrderRequest {
  @NotBlank String sku;
  Integer quantity;
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert result.contracts["OrdersController.create"] == {
        "request": {"name": "request", "type": "CreateOrderRequest", "required": True, "fields": [
            {"name": "sku", "type": "String", "required": True, "validations": ["NotBlank"]},
            {"name": "quantity", "type": "Integer", "required": False, "validations": []},
        ]},
        "returns": {"type": "Order", "required": True},
        "validations": ["Valid"],
        "authorization": ["PreAuthorize"],
        "parameters": [],
    }


def test_rest_contract_extracts_literal_spring_and_go_parameter_bindings(tmp_path: Path):
    (tmp_path / "OrdersController.java").write_text(
        '''class OrdersController {
  @GetMapping("/orders/{id}")
  Order get(@PathVariable("id") String id, @RequestParam("expand") String expand, @RequestHeader("X-Trace") String trace) { return null; }
}
''',
        encoding="utf-8",
    )
    (tmp_path / "orders.go").write_text(
        '''package orders
func Get(w http.ResponseWriter, r *http.Request) { r.PathValue("id"); r.URL.Query().Get("expand"); r.Header.Get("X-Trace") }
func register() { router.GET("/orders/{id}", Get) }
''',
        encoding="utf-8",
    )

    java = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")
    go = StaticAnalysisEngine().analyze(tmp_path, "go")

    assert [item["kind"] for item in java.contracts["OrdersController.get"]["parameters"]] == ["path", "query", "header"]
    assert [item["name"] for item in go.contracts["orders.Get"]["parameters"]] == ["id", "expand", "X-Trace"]


def test_rest_contract_extracts_literal_response_statuses(tmp_path: Path):
    (tmp_path / "OrdersController.java").write_text(
        '''class OrdersController {
  @PostMapping("/orders") @ResponseStatus(HttpStatus.CREATED)
  Order create(Order order) { return order; }
}
''',
        encoding="utf-8",
    )
    (tmp_path / "orders.go").write_text(
        '''package orders
func Create(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusCreated) }
func register() { router.POST("/orders", Create) }
''',
        encoding="utf-8",
    )

    java = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")
    go = StaticAnalysisEngine().analyze(tmp_path, "go")

    assert java.contracts["OrdersController.create"]["response_statuses"] == [{"code": 201, "name": "CREATED"}]
    assert go.contracts["orders.Create"]["response_statuses"] == [{"code": 201, "name": "CREATED"}]


def test_native_flow_boundaries_are_extracted_from_declared_control_flow(tmp_path: Path):
    (tmp_path / "OrdersController.java").write_text(
        '''class OrdersController {
  @PostMapping("/orders") @Transactional
  Order create(Order order) {
    if (order == null) { throw new IllegalArgumentException(); }
    retry(); return service.create(order);
  }
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert {boundary.kind for boundary in result.boundaries if boundary.source == "OrdersController.create"} == {
        "branch", "retry", "error", "transaction",
    }


def test_static_persistence_facts_require_local_entity_evidence(tmp_path: Path):
    (tmp_path / "Order.java").write_text(
        '''@Entity @Table(name = "orders") class Order { String id; }''', encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert {(fact.name, fact.kind, fact.owner) for fact in result.persistence_facts} == {("orders", "sql_table", "Order")}


def test_node_analyzer_extracts_literal_mongoose_collection_ownership(tmp_path: Path):
    (tmp_path / "order-model.ts").write_text(
        '''const Order = mongoose.model("Order", orderSchema, "orders");''', encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert [(fact.name, fact.kind, fact.owner) for fact in result.persistence_facts] == [
        ("orders", "document", "Order"),
    ]


def test_node_analyzer_classifies_explicit_mongoose_model_operations(tmp_path: Path):
    (tmp_path / "orders.ts").write_text(
        '''const Order = mongoose.model("Order", orderSchema, "orders");
function findOrder(id: string) { return Order.findById(id); }
function createOrder(input: CreateOrderInput) { return Order.create(input); }
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert {(edge.source, edge.target, edge.kind) for edge in result.edges} >= {
        ("orders.findOrder", "Order.findById", "reads"),
        ("orders.createOrder", "Order.create", "writes"),
    }


def test_spring_analyzers_extract_literal_document_collection_ownership(tmp_path: Path):
    (tmp_path / "Order.java").write_text(
        '''@Document(collection = "orders") class Order {}''', encoding="utf-8",
    )
    (tmp_path / "Payment.kt").write_text(
        '''@Document("payments") data class Payment(val id: String)''', encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert [(fact.name, fact.kind, fact.owner) for fact in result.persistence_facts] == [
        ("orders", "document", "Order"),
        ("payments", "document", "Payment"),
    ]


def test_node_analyzer_extracts_literal_prisma_model_ownership(tmp_path: Path):
    (tmp_path / "schema.prisma").write_text(
        '''datasource db {
  provider = "postgresql"
}
model Order {
  id String @id
  @@map("orders")
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert [(fact.name, fact.kind, fact.owner) for fact in result.persistence_facts] == [
        ("orders", "sql_table", "Order"),
    ]


def test_node_analyzer_classifies_explicit_prisma_client_operations(tmp_path: Path):
    (tmp_path / "orders.ts").write_text(
        '''const prisma = new PrismaClient();
function findOrder(id: string) { return prisma.order.findUnique({ where: { id } }); }
function upsertOrder(input: CreateOrderInput) { return prisma.order.upsert({ create: input }); }
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert {(edge.source, edge.target, edge.kind) for edge in result.edges} >= {
        ("orders.findOrder", "prisma.order.findUnique", "reads"),
        ("orders.upsertOrder", "prisma.order.upsert", "writes"),
    }


def test_go_http_contract_keeps_the_json_decoded_payload_type(tmp_path: Path):
    (tmp_path / "orders.go").write_text(
        '''package orders
func Create(w http.ResponseWriter, r *http.Request) {
  var request CreateOrderRequest
  json.NewDecoder(r.Body).Decode(&request)
}
func register() { router.POST("/orders", Create) }
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "go")

    assert result.contracts["orders.Create"] == {
        "request": {"name": "request", "type": "CreateOrderRequest", "required": True},
        "returns": None,
        "validations": [],
        "authorization": [],
        "parameters": [],
    }


def test_typed_symbol_index_resolves_an_injected_leaf_method_without_outgoing_calls(tmp_path: Path):
    (tmp_path / "OrdersController.kt").write_text(
        '''class OrdersController(private val useCase: CreateOrderUseCase) {
  @PostMapping("/orders")
  fun create(request: OrderRequest) = useCase.execute(request)
}
''',
        encoding="utf-8",
    )
    (tmp_path / "CreateOrderUseCase.kt").write_text(
        '''class CreateOrderUseCase { fun execute(request: OrderRequest) = Unit }
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert any(symbol.name == "CreateOrderUseCase.execute" for symbol in result.symbols)
    assert any(edge.target == "CreateOrderUseCase.execute" for edge in result.edges)


def test_java_interface_injection_resolves_a_unique_implementation_method(tmp_path: Path):
    (tmp_path / "OrdersController.java").write_text(
        '''class OrdersController {
  private final OrderService service;
  @PostMapping("/orders")
  Order create(Order order) { return service.create(order); }
}
''',
        encoding="utf-8",
    )
    (tmp_path / "DefaultOrderService.java").write_text(
        '''class DefaultOrderService implements OrderService {
  Order create(Order order) { return order; }
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert any(edge.target == "DefaultOrderService.create" for edge in result.edges)


def test_typed_symbol_index_keeps_an_ambiguous_call_unresolved(tmp_path: Path):
    (tmp_path / "Handler.java").write_text(
        '''class Handler {
  @PostMapping("/orders")
  Order create(Order order) { return worker.execute(order); }
}
''',
        encoding="utf-8",
    )
    (tmp_path / "FirstWorker.java").write_text(
        "class FirstWorker { Order execute(Order order) { return order; } }\n", encoding="utf-8",
    )
    (tmp_path / "SecondWorker.java").write_text(
        "class SecondWorker { Order execute(Order order) { return order; } }\n", encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert any(edge.target == "worker.execute" for edge in result.edges)


def test_node_import_alias_resolves_the_declared_module_among_homonymous_symbols(tmp_path: Path):
    (tmp_path / "resolvers.ts").write_text(
        '''import { createOrder as createExternalOrder } from "./orders-service";
export const resolvers = { Mutation: { createOrder: (_, input) => createExternalOrder(input) } };
''',
        encoding="utf-8",
    )
    (tmp_path / "orders-service.ts").write_text(
        "export function createOrder(input: unknown) { return input; }\n", encoding="utf-8",
    )
    (tmp_path / "admin-service.ts").write_text(
        "export function createOrder(input: unknown) { return input; }\n", encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert any(edge.source == "Mutation.createOrder" and edge.target == "orders-service.createOrder" for edge in result.edges)


def test_go_import_alias_resolves_a_declared_package_function(tmp_path: Path):
    (tmp_path / "handler.go").write_text(
        '''package api
import ordercommands "example.com/shop/orders"
func Create() { ordercommands.Create() }
func register() { router.POST("/orders", Create) }
''',
        encoding="utf-8",
    )
    (tmp_path / "orders.go").write_text(
        '''package orders
func Create() {}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "go")

    assert any(edge.source == "api.Create" and edge.target == "orders.Create" for edge in result.edges)


def test_java_qualifier_resolves_the_selected_interface_implementation(tmp_path: Path):
    (tmp_path / "OrdersController.java").write_text(
        '''class OrdersController {
  @Qualifier("partnerAuthorizer") private OrderAuthorizer authorizer;
  @PostMapping("/orders")
  Order create(Order order) { return authorizer.authorize(order); }
}
''',
        encoding="utf-8",
    )
    (tmp_path / "Authorizers.java").write_text(
        '''interface OrderAuthorizer { Order authorize(Order order); }
@Qualifier("localAuthorizer") class LocalAuthorizer implements OrderAuthorizer {
  public Order authorize(Order order) { return order; }
}
@Qualifier("partnerAuthorizer") class PartnerAuthorizer implements OrderAuthorizer {
  public Order authorize(Order order) { return order; }
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert any(
        edge.source == "OrdersController.create" and edge.target == "PartnerAuthorizer.authorize"
        for edge in result.edges
    )


def test_java_primary_resolves_an_unqualified_interface_implementation(tmp_path: Path):
    (tmp_path / "OrdersController.java").write_text(
        '''class OrdersController {
  private OrderAuthorizer authorizer;
  @PostMapping("/orders")
  Order create(Order order) { return authorizer.authorize(order); }
}
''',
        encoding="utf-8",
    )
    (tmp_path / "Authorizers.java").write_text(
        '''interface OrderAuthorizer { Order authorize(Order order); }
class LocalAuthorizer implements OrderAuthorizer {
  public Order authorize(Order order) { return order; }
}
@Primary class DefaultAuthorizer implements OrderAuthorizer {
  public Order authorize(Order order) { return order; }
}
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "jvm-spring")

    assert any(
        edge.source == "OrdersController.create" and edge.target == "DefaultAuthorizer.authorize"
        for edge in result.edges
    )
