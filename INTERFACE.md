# OrbitKB interface contract

OrbitKB is an MCP knowledge layer. Its responses are structured JSON and use
progressive disclosure: list a bounded set, select one item, then ask for detail.
The server never returns a complete repository merely because an agent asks a broad
question.

## Cumulative service identity

A service is identified by `(repository, name)`, not by its name alone. Every
`list_services` item and `search` result includes `repository`; both tools accept an
optional `repository` filter. Every service-scoped tool accepts optional
`repository`: `describe_service`, `list_apis`, `describe_api`, `list_entrypoints`,
`describe_entrypoint`, `describe_persistence`, `describe_messages`,
`describe_cloud_dependencies`, `list_security_findings` and `get_relationships`. An unqualified duplicate returns
an ambiguity error with candidate repository names; agents must pass one of them.
`trace_flow` independently accepts `from_repository` and `to_repository`.

`find_change_surface(task, hint_services?, repository?)` and `orbitkb analyze`
refuse a global analysis when duplicate service names exist, because a name-only
candidate would be unsafe. Pass `repository` (or CLI `--repository`) to scope
retrieval, generated context and recommended follow-up calls to one repository.
This prevents a cumulative KB from silently mixing two independently indexed
services named, for example, `orders`.

## Index lifecycle

An `index` run is authoritative for the detected service boundaries under its
repository root. It removes facts for services no longer detected, including their
contracts, flow evidence and search entries. A same-name service moved within that
repository retains its identity; a renamed service at the same root also retains it.
Use a stable `--repository-name` when a checkout moves. Repository deletion is
explicit through `orbitkb remove --repository <name>`; an unavailable path is not
treated as deletion.

## Agent workflow

1. Call `get_change_context(task, repository?, max_services?, epic_type?)` for a compact first
   implementation briefing, or `find_change_surface(task, repository?)` when only
   impact inference is needed. Both require the repository whenever the KB reports
   duplicate service identities.
2. Call `list_services(repository?)`; use its repository field to qualify
   `describe_service` whenever the same service name exists in more than one repository.
3. Call `list_entrypoints(service)` to choose an HTTP, GraphQL, message, CLI or job
   entrypoint.
4. Call `describe_entrypoint(service, kind, method, name)` for the bounded,
   reachable deterministic flow evidence.
5. Use `describe_api`, `describe_persistence`, `describe_messages`,
   `describe_cloud_dependencies` or `get_relationships` only when the selected flow
   requires them.
6. Call `list_security_findings(service)` before changing credentials,
   configuration or an external integration; it returns locations and remediation,
   never source excerpts or secret values.

Generation and MCP storage are secret-safe boundaries: source/configuration evidence
is redacted before generation, generated structured text is redacted before it is
stored or exposed, and diagnostic failure files contain only a redacted prompt plus
an error type.

## Compact change context

`get_change_context` runs one `find_change_surface` inference and composes at most
five service cards from already-indexed facts; it never reads source. The response
contains `impact` (primary/secondary findings, cross-service flow, contracts/data and
external integrations), compact `services` cards, matching `architecture_risks`,
`unknowns`, `recommended_next_queries` and a `budget` object. Each card contains only
interfaces, outbound dependencies, persistence and messages. Use its recommended
detail tools rather than treating it as a full service dump.

## Context-budget calibration

Every `get_change_context` response has `telemetry: {recorded, run_id?}`. Its
telemetry store contains only requested/returned budget, candidate rank and
truncation, response bytes/token estimate, included/omitted service IDs, and
recommended/executed tool and service IDs. It never retains task text, prompts,
source, cards or tool arguments. A telemetry failure is non-blocking and is returned
as `{"recorded": false}`.

Call `record_change_context_feedback(run_id, outcome, note?, missing_services?)`
after using a context; `outcome` is `sufficient`, `insufficient` or `excessive`.
The optional note is stored only as a one-way digest and missing services as IDs.
Call `record_context_query_execution(run_id, tool, service?)` after following a
returned recommendation. `get_context_budget_metrics(epic_type?)` returns the budget
distribution, truncation rate, sufficiency, mean response size, query follow-through,
adequacy per epic type and a history-backed recommendation. `epic_type` is a
non-sensitive lowercase category. The hard 1–5 cap remains until a future explicit,
evidence-backed policy changes it.

When Git is available, call `verify_context_budget(run_id, repository, since_commit)`
to calculate delivered-card precision, recall and omission rate against actual
changed services. Its persisted verification uses service IDs, not task/source text.

## Runtime evidence

`ingest_runtime_evidence(service, source, observations, repository?)` accepts only
`otel` or `broker` observations in the exact shape `{from, to, kind, count}`. It
rejects trace IDs, attributes, payloads and source content, persisting normalized
symbols and counters separately from static flows. `describe_runtime_divergence`
returns observed-only and static-unobserved edges, explicitly stating that missing
runtime observation is not evidence of dead code.

## Operational limits

`backup --out <path> --db <path>` and `restore <backup> --db <path>` are explicit
CLI-only recovery operations using SQLite's consistent backup API. Indexing permits
one local process per `(repository, service)` at a time; a second attempt fails fast.
Only a lock whose recorded local PID no longer exists is recovered automatically.
Shared, multi-host SQLite locking is outside the supported operating model.

## Support boundaries

Static facts for Go, Java/Spring, Kotlin/Spring and Node/TypeScript/GraphQL are
supported only where source evidence is deterministic. Cloud/IaC facts (AWS
SQS/SNS/S3/EventBridge, Azure Blob Storage, Terraform/CloudFormation/Kubernetes)
follow the same posture but have no `FlowEdge` counterpart yet — see "Cloud and IaC
dependencies". Dynamic wiring, runtime observations and external depth enrichment
remain explicitly bounded or experimental. The server returns unknowns rather than
elevating heuristics to facts; compact change context remains capped at five cards.

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

`find_architecture_smells` exposes those signals at whole-system scope when their
direct static evidence is indexed. It also flags a write/publication behind an HTTP
safe method (`GET`, `HEAD`, `OPTIONS`) or GraphQL `QUERY` as a possible read-entrypoint
side effect, and static ownership declarations for the same table/document in distinct
services as a possible aggregate-ownership overlap. The latter carries `owners` in
`detail` plus the declaration locations; it does not assume a shared database or
reject a valid replicated read model. For every finding, `confidence`, `evidence`,
`unknowns` and `remediation` are top-level fields: ordinary structural findings default
to `1.0` and have empty remediation; hypotheses carry their specific uncertainty and a
conservative review action. When names collide across repositories, `services` uses
`repository/service` rather than an ambiguous bare name.

A RabbitMQ consumer whose indexed contract has no source-proven retry boundary,
retry delay or dead-letter route is returned as
`possible_message_consumer_without_recovery_policy`. Its confidence is intentionally
low: OrbitKB does not turn an absent local declaration into a claim about broker
topology, DLQs or idempotency. The response identifies the consumer queue/symbol and
asks the agent to validate any policy maintained outside the indexed source.

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
Spring `MongoTemplate` and `ReactiveMongoTemplate` operations also require a local
injected template; exact find/count methods read, while save/insert/update/remove
methods write.
JPA `EntityManager` operations likewise require a local injected dependency: `find`
and `getReference` read; `persist`, `merge`, `remove` and `flush` write.

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

## Cloud and IaC dependencies

`describe_cloud_dependencies(service, limit?, offset?, repository?)` returns two
independently paginated lists, never merged into one inferred claim:

```json
{
  "service": "orders-service",
  "repository": "shop",
  "static_facts": [
    {
      "provider": "aws", "resource_type": "queue", "service_name": "sqs",
      "operation": "SendMessage", "operation_kind": "publish", "sdk": "aws-sdk-js-v3",
      "target_name": null,
      "evidence": {"file": "publisher.ts", "start_line": 4, "end_line": 4}
    }
  ],
  "iac_resources": [
    {
      "provider": "aws", "resource_type": "queue", "iac_resource_type": "aws_sqs_queue",
      "logical_name": "orders", "physical_name": "orders-queue", "source_format": "terraform",
      "confidence": "high",
      "evidence": {"file": "infra/main.tf", "start_line": 1, "end_line": 3}
    }
  ],
  "pagination": {"limit": 50, "offset": 0, "static_facts": {"total": 1, "truncated": false}, "iac_resources": {"total": 1, "truncated": false}}
}
```

`static_facts` is deterministic evidence from source code: a locally-declared SDK
client type or a named SDK import actually constructed, resolved against a
vendor-sourced operation table — never a keyword guess. Supported: AWS SQS, SNS, S3
and EventBridge (Go, Java, Kotlin, Node/TypeScript, Python) and Azure Blob Storage
(Go, Java, Kotlin, Node/TypeScript). `target_name` is the literal resource name only
when the call site names it as a plain string; an interpolated or otherwise dynamic
value is `null`, never guessed. It has no `FlowEdge` counterpart yet, so it does not
appear in `trace_flow` or `describe_entrypoint`'s flow.

`iac_resources` is structurally parsed Terraform (`python-hcl2`), CloudFormation
(`cfn-flip`/JSON) and plain Kubernetes manifests in the same repository — real
grammars, not regex on infrastructure text. A resource's type and logical name are
literal by construction; `physical_name` is `null` whenever the declaring attribute
is an interpolated expression. `service_id` (and therefore whether a resource shows
up in one service's `describe_cloud_dependencies` at all) is set only when the
declaring file structurally falls under exactly one indexed service's root;
otherwise it stays repository-scoped and is visible only via
`find_architecture_smells`' cloud findings, not this tool. Unrendered Helm chart
templates are detected and skipped, never mis-parsed as plain YAML; GCP and
Dockerfile are out of scope.

`find_architecture_smells` adds three cloud-derived findings computed from the same
facts, no extra cost: `cloud_dependency_without_iac` (code names a cloud resource no
Terraform/CloudFormation in the repository declares), `cloud_iac_resource_unused`
(the inverse), and `shared_cloud_resource` (two services whose code names the same
queue/topic/bucket — coupling through shared infrastructure, the cloud analog of
`shared_database`).

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
enforces a session timeout and a maximum number of enriched edges per service. It
validates every edge, caches only bounded parsed results for the current indexing
process, and opens a circuit after repeated provider failures. Its metrics expose
counts and latency only, never provider payload or source content.
