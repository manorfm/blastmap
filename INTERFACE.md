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
  ],
  "contract": {
    "arguments": [{"name": "input", "type": "CreateOrderInput", "required": true, "fields": []}],
    "returns": {"type": "Order", "required": true}
  }
}
```

`kind` is one of `invokes`, `injects`, `validates`, `reads`, `writes`,
`publishes` or `consumes`. The response follows only symbols reachable from the
selected entrypoint, with a hard edge budget; unrelated service flow is omitted.
`origin` is `static`, `codegraph` or `runtime`. The
optional `smells` list contains explicit hypotheses, never a conclusive architecture
classification; for example, a GraphQL mutation that directly writes state is flagged
as possible BFF domain-policy leakage for human validation. A flow that both writes
state and publishes an event is also flagged for a transaction/outbox review.

When local resolution has multiple plausible implementations, OrbitKB preserves the
observed call rather than choosing one. A resolved static edge therefore carries only
an implementation justified by local type/injection evidence or a Node/TypeScript
named import declared by the calling module (including an imported alias), or a Go
package import that resolves the observed receiver and function name. Spring
interface injection can additionally resolve from one explicit `@Qualifier` or one
`@Primary` implementation; competing candidates remain as the observed call.

`contract` is present only when deterministic local extraction found one. For GraphQL
it comes from `.graphql`/`.gql` schema definitions; absent data remains `null`, never
a fabricated contract.
For locally declared GraphQL interface or union returns, `contract.returns` may
include sorted `possible_types`. Directives and federation semantics remain outside
this deterministic subset.
Operation type extensions in local `.graphql`/`.gql` files are composed before
extracting a contract; remote schema composition is not assumed.

For HTTP entrypoints, `contract` may include `request`, `returns`, `validations` and
`authorization`. Spring values are extracted from its handler declaration and
annotations; Go request data requires a locally declared value passed to JSON
`Decode`. Unavailable fields are `null` or empty lists.
HTTP entrypoint names include a Spring `@RequestMapping` or Go `Group` prefix only
when both source literals make the resulting route deterministic.
HTTP `contract.parameters` lists literal `path`, `query` and `header` bindings with
name, variable, type and required status when available. Unproven Go types remain
`null` rather than inferred.
HTTP `contract.response_statuses` is an optional, ordered list of source-proven
`{code, name}` values. It is emitted only for literal Spring status declarations or
Go `http.Status...` calls; dynamic response outcomes remain absent.
When a local Java DTO or Go `struct` declaration is available, request/response
objects may additionally expose `fields` with their source-declared type, required
status and validation tags.

RabbitMQ consumer contracts use `transport`, `direction`, `queue` and optional
`payload` fields. Producer calls remain bounded flow evidence until publication
contracts have their own persisted representation. `describe_messages` now returns
that representation in `static_contracts`, with exchange, routing key, payload type
when proven, and file/line evidence. `exchange` is the literal first destination of
a RabbitMQ publication; the legacy internal storage name is not exposed. Go AMQP
publications require a receiver locally declared as `*amqp.Channel` and literal
exchange/routing-key arguments.
For Node/TypeScript publications, `payload_type` is present only when the payload
identifier resolves to an explicitly typed parameter in an enclosing lexical function.
`static_contracts.message_version` is `null` unless the publication itself contains
one literal version header named `schema_version`, `schemaVersion`,
`x-schema-version`, or `x-version`.
Matching consumer contracts may include literal `dead_letter_routing_key` and
`retry_delay_ms` extracted from local queue declarations; missing or dynamic values
are not represented.
For Go, these values require a literal `amqp.Table` supplied to `QueueDeclare` on a
receiver locally declared as `*amqp.Channel`.
Consumer `contract.bindings` is an optional ordered list of `{exchange,
routing_key}` relations. A relation is emitted only when local RabbitMQ declarations
prove the queue, exchange and routing key as literals; multiple bindings are kept.
For Go, this requires a literal `QueueBind` call on a receiver locally declared as
`*amqp.Channel`.

`describe_entrypoint` returns `boundaries` alongside `flow`: branch, async, retry,
error or transaction markers whose source symbol is reachable under the selected
edge budget. They report syntactic evidence only, never runtime reachability.
It also returns `persistence_operations`, a compact list of bounded-flow `reads` and
`writes` with target and source evidence. It is a projection of `flow`, not a
separate analysis or reachability claim.
For Node/TypeScript, Mongoose calls are classified only when the receiver was
locally declared by `mongoose.model`; exact operation names determine `reads` or
`writes`.
Prisma calls are classified only for model delegates on a client locally constructed
with `new PrismaClient`, again using exact operation names.
Go GORM calls are classified for exact operations invoked directly, or through a
simple fluent chain, on a parameter locally typed as `*gorm.DB`; unproven roots
remain generic flow calls.
Go standard-library SQL operations require a local `*sql.DB` or `*sql.Tx`
parameter: `Query*` methods read and `Exec*` methods write.
Java and Kotlin Spring standard repository operations require a locally declared
injected member whose type is a Spring repository; a receiver name alone is not
treated as persistence evidence.
Derived Spring Data methods, such as `findByStatus` and `deleteByCustomerId`, also
require a local interface that extends a supported Spring Data contract.
Local Spring Data `@Query` methods are reads by default; only a companion
`@Modifying` annotation establishes a write.
Spring `JdbcTemplate` and `NamedParameterJdbcTemplate` operations require a locally
injected template: `query*` reads, while `update` and `batchUpdate` write.

`describe_persistence` also returns `static_facts` for locally proven JPA, GORM or
Mongoose mappings, including entity/table or collection name, local owner and
file/line evidence. Mongoose requires `mongoose.model` with a literal third
collection argument; Spring Data Mongo requires a literal `@Document` collection
argument. Prisma requires a literal datasource provider and `@@map`; supported SQL
providers yield `sql_table` facts while MongoDB yields `document` facts. These do
not replace the generated persistence entities.

`describe_entrypoint` accepts `max_edges` (default 50, maximum 200). Its
`flow_pagination.truncated` field is `true` when more reachable flow exists, so an
agent can deliberately request more depth instead of receiving it by default.

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
