# OrbitKB interface contract

OrbitKB is an MCP knowledge layer. Its responses are structured JSON and use
progressive disclosure: list a bounded set, select one item, then ask for detail.
The server never returns a complete repository merely because an agent asks a broad
question.

## Agent workflow

1. Call `find_change_surface(task)` for an epic.
2. Call `describe_service` only for the likely services.
3. Call `list_entrypoints(service)` to choose an HTTP, GraphQL, message, CLI or job
   entrypoint.
4. Call `describe_entrypoint(service, kind, method, name)` for the bounded,
   reachable deterministic flow evidence.
5. Use `describe_api`, `describe_persistence`, `describe_messages` or
   `get_relationships` only when the selected flow requires them.
6. Call `list_security_findings(service)` before changing credentials,
   configuration or an external integration; it returns locations and remediation,
   never source excerpts or secret values.

## Entrypoint response

```json
{
  "entrypoint": {
    "kind": "graphql",
    "method": "MUTATION",
    "name": "createOrder",
    "symbol": "Mutation.createOrder",
    "evidence": {"file": "resolvers/orders.ts", "start_line": 14, "end_line": 22}
  },
  "flow": [
    {
      "from": "Mutation.createOrder",
      "to": "ordersService.create",
      "kind": "invokes",
      "confidence": "high",
      "origin": "static",
      "evidence": {"file": "resolvers/orders.ts", "start_line": 17, "end_line": 17}
    }
  ]
}
```

`kind` is one of `invokes`, `injects`, `validates`, `reads`, `writes`,
`publishes` or `consumes`. The response follows only symbols reachable from the
selected entrypoint, with a hard edge budget; unrelated service flow is omitted.
`origin` is `static`, `codegraph` or `runtime`. The
optional `smells` list contains explicit hypotheses, never a conclusive architecture
classification; for example, a GraphQL mutation that directly writes state is flagged
as possible BFF domain-policy leakage for human validation.

## External depth-provider contract

Configure `--depth-command`, optional repeated `--depth-arg`, and `--depth-tool`.
OrbitKB starts the external process as a stdio MCP server and calls the configured
tool once per selected entrypoint with:

```json
{"repository": "/absolute/repository/path", "symbol": "OrdersController.create"}
```

The tool must return structured JSON (or a JSON text content item):

```json
{
  "edges": [
    {
      "from": "OrdersController.create",
      "to": "CreateOrderUseCase.execute",
      "kind": "invokes",
      "confidence": "high",
      "evidence": {"file": "application/CreateOrderUseCase.kt", "start_line": 28, "end_line": 28}
    }
  ]
}
```

Unknown kinds and malformed edges are dropped. In `augment` mode a provider error
leaves the native AST result intact; in `require` mode indexing fails. The CLI also
enforces a session timeout and a maximum number of enriched edges per service.
