from pathlib import Path

from impactmesh.analysis.engine import StaticAnalysisEngine


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


def test_node_analyzer_exposes_rabbit_consumer_and_its_bounded_handler_flow(tmp_path: Path):
    source = tmp_path / "consumer.ts"
    source.write_text(
        '''channel.consume("orders.created", async (message) => {
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
