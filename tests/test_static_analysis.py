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


def test_node_graphql_analyzer_exposes_mutation_and_rabbit_publish(tmp_path: Path):
    source = tmp_path / "resolvers.ts"
    source.write_text(
        '''export const resolvers = {
  Mutation: { createOrder: (_, input, { service }) => { service.create(input); channel.publish("orders", "created", input); } }
};
''',
        encoding="utf-8",
    )

    result = StaticAnalysisEngine().analyze(tmp_path, "node-ts")

    assert [(entry.kind, entry.method, entry.name) for entry in result.entrypoints] == [
        ("graphql", "MUTATION", "createOrder")
    ]
    assert any(edge.kind == "publishes" and edge.target == "channel.publish" for edge in result.edges)
    assert [(item.channel, item.routing_key) for item in result.message_contracts] == [("orders", "created")]


def test_node_analyzer_exposes_rabbit_consumer_and_its_bounded_handler_flow(tmp_path: Path):
    source = tmp_path / "consumer.ts"
    source.write_text(
        '''channel.consume("orders.created", async (message: OrderCreated) => {
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
        '''class CreateOrderUseCase {
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
    }


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
