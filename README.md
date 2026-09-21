# orbitkb

<!-- BADGES:START -->
[![PyPI](https://img.shields.io/pypi/v/orbitkb.svg)](https://pypi.org/project/orbitkb/)
[![Python versions](https://img.shields.io/pypi/pyversions/orbitkb.svg)](https://pypi.org/project/orbitkb/)
[![CI](https://github.com/manorfm/orbitkb/actions/workflows/ci.yml/badge.svg)](https://github.com/manorfm/orbitkb/actions/workflows/ci.yml)
[![Security](https://github.com/manorfm/orbitkb/actions/workflows/security.yml/badge.svg)](https://github.com/manorfm/orbitkb/actions/workflows/security.yml)
[![License](https://img.shields.io/pypi/l/orbitkb.svg)](https://pypi.org/project/orbitkb/)
<!-- BADGES:END -->

Task-aware change intelligence for AI coding agents: given an engineering task, what
is the smallest architectural surface an agent needs to understand before touching
code — with evidence, confidence and freshness made explicit, instead of implied.

## What this isn't

- **Not a generic code graph** — AST and optional code-intelligence providers are
  extraction mechanisms, not the product surface. OrbitKB stores only bounded
  entrypoint flows that help an agent make a change safely.
- **Not generic RAG** over the repository, nor "code memory".
- **Not a vector database** — `search`/`find_change_surface` use SQLite FTS5
  (bm25) as the primary, free retrieval path. An optional local (no paid API),
  CPU-only semantic fallback exists (see "Semantic retrieval fallback" below) for
  when a task's vocabulary doesn't keyword-match anything indexed, but it's an
  opt-in extra, never the default path, and every vector lives in SQLite
  (`vector_json` columns) — no separate vector-DB service.
- **Doesn't try to rewrite code or act on its own** — it's a knowledge layer
  queried via MCP; the agent is the one who decides and edits.

## Idea

AI agents that need to understand a microservices system today only have two
paths: read the entire source code (expensive in tokens, slow) or rely on manual
documentation that goes stale. `orbitkb` "grinds" one or several code trees —
repository by repository, or a whole monorepo at once, cumulatively into the same
database — uses an LLM (Claude CLI or Codex, via headless CLI, using your
subscription instead of paid API) to synthesize lean, **semantic** documentation
(not just structural) per microservice and per API, persists that in SQLite as a
queryable **System Knowledge Model**, and serves all of it to agents via MCP tool
calls with progressive drill-down: list services → describe a service → list APIs
→ detail an API.

The focus is the layer missing between "read the source code" and "ask a human",
on three fronts:

- **Why, not just what**: for each API, document why it calls another
  service/queue (the business reason), what it validates, who can call it, and
  what it persists — information that today only lives in the head of whoever
  wrote the code.
- **Task-oriented Change Intelligence**: given a free-text epic, point out the
  **smallest architectural surface** that's likely to need changing — without the
  agent having to manually explore the whole system first (`find_change_surface`,
  see below). The end product isn't "more context", it's **more relevant
  context**: whenever it's a choice between returning more information or more
  relevant information, the right answer is the second one.
- **System-wide architectural health**: beyond "how does one service work",
  answering "does this system have a dependency cycle?", "did some service turn
  into a bottleneck?", "are two services accidentally sharing the same
  database?" (`find_architecture_smells`, see below) — structural signals, not
  judgment calls.

Every response explicitly separates three layers, and never conflates them:

- **Fact**: deterministically extracted structure (e.g.: `orders-service` calls
  `payments-service`; `orders-service` exists).
- **Semantic interpretation**: LLM synthesis from real evidence (e.g.:
  "`payments-service` owns payment authorization").
- **Task inference**: a conclusion specific to one epic, always carrying
  `reason`, `confidence` and `evidence`, and never treated as absolute truth
  (e.g.: "adding Pix likely requires changing `payments-service`"). Alongside
  that, the system is also explicit about what it **doesn't know** (`unknowns`)
  and about whether the indexed knowledge might be **stale** (`freshness`) —
  absence of information never silently becomes "doesn't exist" or "isn't
  affected".

### Flow intelligence: entrypoint → boundaries

OrbitKB first parses supported source locally with Tree-sitter. This has no LLM
token cost and never sends an entire repository to a model. It identifies a narrow
entrypoint and its deterministic edges: invocation, dependency injection,
validation, read/write and message publish/consume. The agent can then call
`describe_entrypoint` to receive just that flow with file/line evidence.

For GraphQL, the same response includes a deterministic schema contract when a
local `.graphql`/`.gql` schema defines the selected resolver: arguments, required
flags, input fields and return type. This avoids spending LLM context on a schema
file just to answer a contract question.
When that local schema defines an interface or union return type, the contract also
lists its declared `possible_types`; directives and federation metadata are not
interpreted as behavior.
Base operation types and local `extend type Query`/`Mutation`/`Subscription` blocks
are composed across `.graphql` and `.gql` files before the contract is extracted.

For native HTTP handlers, the contract includes only source-proven facts: Spring
`@RequestBody` name/type, declared return type and validation/authorization
annotations; Go JSON-decoded local payload types. Missing response or DTO detail is
returned as `null` rather than guessed.
Literal Spring controller `@RequestMapping` and Go router `Group` prefixes are
composed with literal handler routes; dynamic path composition is left unchanged.
REST contracts also list source-proven path, query and header bindings. Spring
provides declared parameter types; Go HTTP extraction leaves types unknown unless a
future local type proof is available.
Literal response statuses are included when a Spring handler declares an HTTP status
or a Go handler writes a literal standard-library status; dynamic outcomes are
omitted rather than guessed.
When a referenced Java DTO or Go `struct` is declared in the indexed source, its
locally observed fields and validation tags are attached to that contract.

RabbitMQ consumer entrypoints expose a compact contract with queue, direction and a
declared payload parameter type when the listener/callback makes it available.
Literal queue declarations can additionally supply `dead_letter_routing_key` and
`retry_delay_ms`; dynamic broker configuration is deliberately omitted.
In Go, those options require a literal `amqp.Table` in `QueueDeclare` on a receiver
declared as `*amqp.Channel`.
When a local RabbitMQ binding proves a queue, exchange and routing key, the consumer
contract includes its ordered `bindings`; multiple literal bindings remain distinct.
Go bindings require a literal `QueueBind` call on a receiver declared as
`*amqp.Channel`.
Publication contracts are persisted separately and returned by `describe_messages`
as `static_contracts`, including literal exchange, routing key, payload type when
locally declared, and source evidence. Node, Spring AMQP, and Go AMQP `Publish` /
`PublishWithContext` publishers are covered.
For Node/TypeScript, a payload type is emitted only when the published identifier
matches an explicitly typed parameter in its lexical function scope.
`message_version` is emitted only from a literal `schema_version`, `schemaVersion`,
`x-schema-version`, or `x-version` header attached to that publication.

`describe_entrypoint` also returns source-proven `boundaries` for branch, async,
retry, error and transaction constructs reachable in the bounded flow. These are
observed control-flow markers, not claims that every branch is executed.
Its `persistence_operations` is a compact projection of reachable `reads` and
`writes` flow edges, retaining the same source evidence without adding traversal.
For Node/TypeScript, Mongoose operations enter that projection only when the
receiver was locally declared by `mongoose.model`; exact operation names determine
whether the operation reads or writes.
Prisma operations are classified with the same conservative rule: the client must
be locally constructed with `new PrismaClient`, and only exact model-delegate
operation names are treated as reads or writes.
For Go, GORM operations are classified when invoked directly, or through a simple
fluent chain, on a parameter locally typed as `*gorm.DB`; chains whose root cannot
be proven without speculation remain generic flow calls.
For Java and Kotlin Spring, standard repository operations require a locally
declared injected member whose type is a Spring repository; a receiver merely named
`repository` is not sufficient.
Derived Spring Data methods such as `findByStatus` and `deleteByCustomerId` require
the stronger proof of a local interface extending a supported Spring Data contract.
Local Spring Data `@Query` methods are reads by default; only a companion
`@Modifying` annotation makes their operation a write.

`describe_persistence` exposes source-proven `static_facts` independently of the
generated entity summaries. JPA `@Entity`/`@Table` and GORM-tagged structs establish
the local owner; Spring Data Mongo `@Document` and Mongoose `mongoose.model` calls
with literal collections establish document ownership. Prisma models require both a
literal datasource provider and `@@map`; supported SQL providers yield tables and
MongoDB yields documents. Absent mapping evidence creates no ownership claim.

The initial native coverage is deliberately focused on Go HTTP handlers,
Java/Kotlin Spring HTTP controllers and Rabbit listeners, and Node/TypeScript
GraphQL resolvers and Rabbit consumers. It resolves an observed call across files
when constructor/field injection or one unambiguous local implementation proves the
target. A local symbol index includes callable declarations even when they are leaf
methods, and Java interface implementations are used only when exactly one candidate
matches. Node/TypeScript named imports, including `import { source as alias }`, bind
an observed call to the declared local module before any method-name fallback. The
equivalent Go package import rule also supports aliases such as
`import ordercommands ".../orders"`. Spring interface injection may use an explicit
`@Qualifier` or a single `@Primary` implementation; otherwise the call remains
unresolved. The model also dogfoods explicit Python CLI `main` functions. Resolution
is bounded by the selected entrypoint; it never builds or returns a whole code graph.

### Optional depth provider

Static analysis is sufficient for the normal workflow. For codebases where symbol
resolution across files needs more precision, `index` and `update` can augment the
same bounded flow with an external code-intelligence MCP process:

```bash
orbitkb index /repo/orders --depth-mode augment \
  --depth-command codegraph-mcp --depth-tool trace_entrypoint
```

`off` (the default) uses only OrbitKB. `augment` keeps the native result if the
provider is unavailable; `require` fails the index explicitly. The provider gets
one repository path and one selected entrypoint symbol at a time, and may return
only documented flow edges. `--depth-timeout` bounds the full MCP session and
`--depth-max-edges` bounds added context per service. It cannot inject an opaque
whole-repository graph.

See [INTERFACE.md](INTERFACE.md) for the MCP edge contract and response formats.

### Hierarchical generation: endpoint → component → service

Each service's generation is composed bottom-up, not generated in parallel from
isolated fragments:

1. **Endpoint** (finest-grained unit) — detailed from its own code excerpt, plus
   one hop of local navigation (functions called inside the handler, resolved in
   the same file) when one exists.
2. **Component** (class/controller/module) — groups the endpoints that belong to
   the same class (or to the same file, when routing is done via loose functions,
   as is common in FastAPI/Flask) and synthesizes a summary **from the endpoint
   summaries already generated**, never re-reading raw code.
3. **Service** (overview) — composed last, from the component summaries, no
   longer from just the folder tree read before any endpoint was analyzed.

This fixes two problems with the previous model (flat, everything generated in
parallel): wrong call attribution (an endpoint picking up another endpoint's
calls just because they were in the same service) and a shallow overview
(written before any detail existed).

## Installation

From PyPI:
```bash
pip install orbitkb
# or, for an isolated CLI install:
pipx install orbitkb
```

From source:
```bash
git clone https://github.com/manorfm/orbitkb.git
cd orbitkb
pip install -e ".[dev]"
```

Either way, `orbitkb` needs a headless LLM CLI on `PATH` to actually generate
anything: `claude` (Claude CLI) or `codex` (OpenAI Codex CLI), authenticated
with your normal subscription session — see `--backend` in "Basic usage" below.
Commands that only read the already-indexed SQLite database (`list`, `status`,
`export`, and every MCP tool except `find_change_surface`) don't need either
CLI at all.

Optional: `pip install orbitkb[semantic]` (or `pip install -e ".[dev,semantic]"`
from source) adds a local, CPU-only semantic-retrieval fallback for
`find_change_surface` — see "Semantic retrieval fallback" below. It's never
required; without it, retrieval stays exactly keyword-only (FTS5), same as
before. The first embed call after installing it downloads a small model file
from Hugging Face once and caches it locally; every call after that is fully
offline.

## Configuration

### MCP client (Claude CLI, Codex, etc.)

Register `orbitkb serve` as an MCP server. For Claude CLI, add this to your
MCP client config (e.g. `~/.claude.json`'s `mcpServers`, or a project's
`.mcp.json`):
```json
{
  "mcpServers": {
    "orbitkb": {
      "type": "stdio",
      "command": "orbitkb",
      "args": ["serve", "--backend", "claude"]
    }
  }
}
```
If `orbitkb` isn't on `PATH` in the environment your MCP client launches from,
use its absolute path instead — e.g. whatever `which orbitkb` prints, often
`<your-venv>/bin/orbitkb` for a source/editable install.

### Environment variables

- `ORBITKB_BACKEND` — default backend (`claude` or `codex`) when `--backend`
  isn't passed explicitly. Defaults to `claude` when unset.
- `ANTHROPIC_API_KEY` — only read when `--claude-bare` is passed (metered API
  billing instead of the Claude CLI subscription session).
- `CODEX_API_KEY` — only read when `--codex-api-key` is passed (metered billing
  instead of the ChatGPT subscription session).

### Database location

Defaults to `~/.orbitkb/orbitkb.db`, shared across every command unless you
pass `--db <path>` explicitly (every subcommand accepts it). One database can
accumulate multiple repositories (see "Basic usage" below), so a single default
location is usually what you want; pass `--db` to keep separate projects in
separate databases.

## Basic usage

```bash
orbitkb index /path/to/repository --backend claude   # or --backend codex
orbitkb index /another/repository --repository-name another-repo  # multiple repos in the same DB, cumulative
orbitkb list
orbitkb status [service]
orbitkb export md --out docs/
orbitkb export mermaid --out docs/   # topology diagram + one ER diagram per service
orbitkb serve --backend claude   # MCP server (stdio); backend only used by find_change_surface
orbitkb analyze "Add Pix support to checkout" --backend claude   # runs find_change_surface directly, no MCP session needed
orbitkb verify <run_id> --repository <name> --since <commit>   # checks a prediction against the real git diff
```

Every subcommand is self-explanatory via `--help` (e.g. `orbitkb index --help`),
with a ready-to-copy example. `orbitkb --help` explains the whole flow: **index
→ ask → verify**.

Knowledge is cumulative by nature: you can index one repository at a time
(`orbitkb index <repo1>`, then `orbitkb index <repo2> --repository-name
<repo2>`, ...) as they become available, or point at a monorepo root all at once
— the same SQLite database accumulates both cases without name collisions, and
`find_change_surface`/`search` always see everything indexed so far, not just the
last repository indexed.

## MCP response format

Tool calls always return structured JSON (never free text), designed for an
agent to refine its request without loading everything at once. Examples below
are generated from the project's own sample fixtures (`verify/sample_project`
for the description tools; the "checkout/payments/Pix" test fixture for
`find_change_surface`).

**`list_services()`** — overview, one line per service:
```json
{
  "services": [
    {"name": "orders-service", "short_desc": "A hexagonal FastAPI service that orchestrates checkout: charges a customer's payment, reserves product stock, and manages the order lifecycle via Postgres.", "stack": "python", "api_count": 4},
    {"name": "payments-service", "short_desc": "A Node.js/Express service that charges and refunds customers via an external card gateway, persists transactions in MongoDB, and publishes payment-related events to Kafka.", "stack": "node-ts", "api_count": 3}
  ]
}
```

**`describe_service("orders-service")`** — full description + dependencies with
the business reason (`reason`, `data_needed`, `purpose_kind`) + the components
that make up the service + APIs/persistence/messaging as reference only (name,
no field-level detail) + `freshness`:
```json
{
  "name": "orders-service",
  "short_desc": "A hexagonal FastAPI service that orchestrates checkout: charges a customer's payment, reserves product stock, and manages the order lifecycle via Postgres.",
  "long_desc": "orders-service is a Python microservice built on FastAPI using a hexagonal (ports & adapters) architecture, exposing endpoints to create, fetch, and cancel orders. Creating an order synchronously checks stock and charges the customer, then persists the order to Postgres and publishes an order.created event for downstream consumers; a background consumer reacts to payment.failed and stock.reservation_failed events by cancelling the order, giving it a role as both an orchestrator of a synchronous checkout flow and a participant in an asynchronous, choreographed saga.",
  "stack": "python",
  "calls": [
    {"to_service_name": "payments-service", "call_kind": "http", "reason": "to charge the customer's payment method for the order amount", "data_needed": ["amount", "currency", "payment_token"], "purpose_kind": "other", "target_kind": "internal", "resource_type": "not_applicable"},
    {"to_service_name": "inventory-service", "call_kind": "http", "reason": "to check current stock for the requested SKU before confirming the order", "data_needed": ["sku", "qty"], "purpose_kind": "validation", "target_kind": "internal", "resource_type": "not_applicable"},
    {"to_service_name": "notify-hub.vendor.io", "call_kind": "http", "reason": "to send the customer an order-confirmation notification", "data_needed": ["order_id", "recipient"], "purpose_kind": "notification", "target_kind": "external", "resource_type": "saas"},
    {"to_service_name": "order.created", "call_kind": "queue_publish", "reason": "to notify downstream consumers that a new order was created", "data_needed": ["order_id", "sku", "qty"], "purpose_kind": "notification", "target_kind": "unknown", "resource_type": "not_applicable"}
  ],
  "apis": [
    {"method": "POST", "path": "/orders", "summary": "Creates a new order by charging the customer's payment method and checking stock availability, then publishes an order-created event."},
    {"method": "GET", "path": "/orders/{order_id}", "summary": "Fetches an order by id."},
    {"method": "POST", "path": "/orders/{order_id}/cancel", "summary": "Cancels an order and publishes an order-cancelled event."},
    {"method": "GET", "path": "/health", "summary": "Liveness check."}
  ],
  "components": [
    {"name": "OrdersController", "file_path": "adapters/http/orders_controller.py", "summary": "The inbound HTTP adapter: create/get/cancel-order endpoints, delegating to the application use cases."},
    {"name": "main", "file_path": "main.py", "summary": "Composition root; also exposes a bare /health check with no enclosing class."}
  ],
  "persists": [{"name": "orders", "kind": "sql_table", "engine": "postgres"}],
  "messages": [
    {"direction": "publishes", "channel": "order.created", "provider": "kafka", "description": "Published after a payment charge succeeds and inventory stock is confirmed; signals that a new order has been created."},
    {"direction": "consumes", "channel": "payment.failed", "provider": "kafka", "description": "Triggers order cancellation when payments-service declines or fraud-blocks a charge."}
  ],
  "freshness": {"indexed_at": "2026-03-01T12:00:00+00:00", "source_commit": "a1b2c3d", "current_commit": "a1b2c3d", "stale": false},
  "pagination": {"limit": 50, "offset": 0,
    "calls": {"total": 4, "truncated": false}, "apis": {"total": 4, "truncated": false},
    "components": {"total": 2, "truncated": false}, "persists": {"total": 1, "truncated": false},
    "messages": {"total": 3, "truncated": false}}
}
```
`stale: true` means the repository has had new commits since indexing — the
agent should consider reindexing before trusting the content too much. `stale:
null` means it's impossible to tell (the repository isn't git, or was never
indexed with an associated commit) — never treated as "everything's fine", nor
as "it's stale".

`components` reflects the hierarchical generation described above: when routing
is done via loose functions (no class), the component is the file itself
(`"name": "main"`); when there's a class/controller, the component takes its
name.

### Pagination (`describe_service`, `list_apis`, `describe_persistence`, `describe_messages`)

A benchmark fixture has one or two endpoints; a real service can have dozens.
These four tools accept optional `limit` (default 50, capped at 500) and
`offset` (default 0) parameters and cap every list they return accordingly —
`describe_service` applies them independently to each of its five lists
(`calls`/`apis`/`components`/`persists`/`messages`), reported per-list under
`pagination`; `list_apis`/`describe_persistence`/`describe_messages` report a
single top-level `total`/`truncated` next to their one list. `truncated: true`
means call again with `offset` advanced by `limit` to see the rest — the
response never silently drops items without saying so. A negative or zero
`limit`/`offset` is rejected with `{"error": "..."}`; an oversized `limit` is
silently capped at 500, never an error.

**`describe_api("orders-service", "POST", "/orders")`** — the most detailed
level: request and response shape field by field (`request_shape` includes
`required` per field — the beginning of structured Contract Intelligence, today
only at the field level, without yet comparing across indexing runs to detect a
real contract break), the same dependency calls (now scoped to just this API),
and validation/authorization rules:
```json
{
  "method": "POST",
  "path": "/orders",
  "summary": "Creates a new order by charging the customer's payment method and checking stock availability, then publishes an order-created event.",
  "description": "Accepts an order request (amount, currency, payment token, SKU, quantity), charges the customer via the payments service, checks stock availability via the inventory service, then publishes an order.created event to Kafka and notifies the customer via an external vendor. Returns the newly created order's ID and confirmation status.",
  "response_shape": [
    {"field": "order_id", "type_desc": "string, order id"},
    {"field": "status", "type_desc": "string, order status (e.g. \"confirmed\")"}
  ],
  "request_shape": [
    {"field": "amount", "type_desc": "number, order amount", "required": true},
    {"field": "currency", "type_desc": "string, currency code", "required": true},
    {"field": "payment_token", "type_desc": "string, payment token", "required": true},
    {"field": "sku", "type_desc": "string, product SKU", "required": true},
    {"field": "qty", "type_desc": "number, quantity", "required": true}
  ],
  "calls": [
    {"to_service_name": "payments-service", "call_kind": "http", "reason": "to charge the customer's payment method for the order amount", "data_needed": ["amount", "currency", "payment_token"], "purpose_kind": "other", "target_kind": "internal", "resource_type": "not_applicable"},
    {"to_service_name": "inventory-service", "call_kind": "http", "reason": "to check current stock for the requested SKU before confirming the order", "data_needed": ["sku", "qty"], "purpose_kind": "validation", "target_kind": "internal", "resource_type": "not_applicable"},
    {"to_service_name": "notify-hub.vendor.io", "call_kind": "http", "reason": "to send the customer an order-confirmation notification", "data_needed": ["order_id", "recipient"], "purpose_kind": "notification", "target_kind": "external", "resource_type": "saas"}
  ],
  "validations": [
    {"kind": "authorization", "description": "Requires an Authorization header starting with \"Bearer \"; otherwise returns 401 with detail \"missing bearer token\"."},
    {"kind": "input_validation", "description": "Requires payload fields amount, currency, payment_token, sku, and qty (accessed directly from payload dict, so missing fields would raise an error)."}
  ]
}
```
`resource_type` is only meaningful when `target_kind` is `"external"` (see
"Internal vs. external" below) — it's `"not_applicable"` in every other case,
never empty.

`describe_persistence` and `describe_messages` follow the same pattern, but
return the full field-level schema (which `describe_service` only references by
name) — they're called separately on purpose, to keep `describe_service` lean.
Both also carry `engine`/`provider` (see "Database and messaging: engine and
provider", below).

## System Intelligence: relationships, smells and change surface

Additional tools go beyond "how does a service work" and answer "what's
connected to what", "how does A reach B", "does this system have any structural
problem", "what does this task likely affect" and "did yesterday's prediction
actually hold up":

**`get_relationships("payments-service", direction="both")`** — a 1-hop graph
around one service: calls it makes (outbound), calls other services make into it
(inbound — "who depends on me", today only possible via this tool), and
queue/topic links inferred from a shared channel name (`MESSAGE_LINK`). Each edge
carries `reason`, `confidence` (when applicable), `target_kind`
(`internal`/`external`/`unknown` — see next section), `provenance` (`llm` when it
came from synthesis over real code, `deterministic` when it's a pure structural
fact, like `MESSAGE_LINK` by channel name) and `evidence` (file/line):
```json
{
  "service": "payments-service",
  "relationships": [
    {"type": "HTTP", "direction": "inbound", "source_service": "checkout-service",
     "reason": "authorize the payment for the order", "confidence": 0.9, "target_kind": "internal",
     "evidence": [{"file": "checkout.py", "start_line": 1, "end_line": 20}],
     "provenance": {"source": "llm"}},
    {"type": "HTTP", "direction": "outbound", "target_service": "Stripe API",
     "reason": "charge the customer's card via the vendor gateway", "confidence": 0.85, "target_kind": "external",
     "evidence": [], "provenance": {"source": "llm"}},
    {"type": "MESSAGE_LINK", "direction": "outbound", "channel": "payment_authorized",
     "target_service": "notification-service", "reason": null, "confidence": null, "evidence": [],
     "provenance": {"source": "deterministic"}}
  ]
}
```

**`trace_flow("orders-service", "ledger-service")`** — shortest path between two
services, walking `service_calls` (outbound) and queue links (publish→consume) —
the multi-hop counterpart to `get_relationships` (which only walks 1 hop per
call). Useful when you know two services are related but not how:
```json
{"path": [{"from": "orders-service", "to": "payments-service", "type": "HTTP", "reason": "...", "confidence": 0.9, "evidence": [...]},
          {"from": "payments-service", "to": "ledger-service", "type": "QUEUE_PUBLISH", "reason": "...", "confidence": 0.85, "evidence": []}],
 "reachable": true, "hops": 2}
```
If there's no path within `max_hops` (default 6), it returns `{"path": [],
"reachable": false, "note": "..."}`.

**`find_architecture_smells()`** — whole-system structural findings, recomputed
on every `index`/`update` purely from what's already indexed (`service_calls`,
`persistence_entities`) — **no LLM call**, pure SQL plus a Tarjan's-algorithm pass
(strongly connected components) to detect cycles. Always in risk language, never
a verdict:
```json
{
  "run_id": 3,
  "findings": [
    {"kind": "cycle", "severity": "warning", "services": ["checkout-service", "payments-service"],
     "reason": "checkout-service -> payments-service -> checkout-service form a circular dependency...",
     "detail": {}},
    {"kind": "fan_in", "severity": "info", "services": ["payments-service"],
     "reason": "4 other internal services call payments-service directly — a potential bottleneck or single point of coupling.",
     "detail": {"count": 4}},
    {"kind": "shared_database", "severity": "warning", "services": ["orders-service", "billing-service"],
     "reason": "orders-service, billing-service all persist an entity named 'orders' on postgres — likely sharing a database, which couples their schemas.",
     "detail": {"entity": "orders", "engine": "postgres"}},
    {"kind": "duplicate_external_integration", "severity": "info", "services": ["checkout-service", "refunds-service"],
     "reason": "checkout-service, refunds-service each integrate with 'Stripe API' independently — worth checking whether that's intentional or should be consolidated behind one service.",
     "detail": {"vendor": "Stripe API"}}
  ],
  "trend": {
    "new_findings": [{"kind": "fan_in", "services": ["payments-service"], "detail": {"count": 5}}],
    "resolved_findings": [{"kind": "cycle", "services": ["checkout-service", "refunds-service"], "detail": {}}],
    "count_deltas": [{"kind": "fan_out", "services": ["checkout-service"], "previous_count": 4, "current_count": 6}]
  }
}
```
4 detectors today: `cycle` (circular dependency among internal services),
`fan_in`/`fan_out` (a service with a disproportionate number of direct
dependents/dependencies — a starting threshold of 4, not a trained value),
`shared_database` (same-named entity, same engine, different services) and
`duplicate_external_integration` (two or more services independently integrating
with the same vendor). Deliberately out of scope still: legacy/strangler-fig
tagging and directional cycle severity (a cycle involving a legacy service is
more serious than one between two peers) — see "Known limitations".

`trend` compares the latest run against the one immediately before it —
`new_findings`/`resolved_findings` by `(kind, services)` identity, and
`count_deltas` for a `fan_in`/`fan_out` finding that persisted across both runs
but whose count changed — computed for free from already-stored runs, no
re-detection. It's omitted entirely (not an empty object) on the very first
architecture run ever, since there's nothing yet to compare against.

### Internal vs. external (`target_kind`, `resource_type`)

Every call (`service_calls`) carries `target_kind`: `internal` (another service
of this same system), `external` (a third-party/vendor integration) or
`unknown`. Classification uses two signals, in this precedence order:
1. **Ground truth**: if the name resolves to an already-indexed service
   (`to_service_id`), it's `internal`, full stop — overrides any earlier guess.
2. **LLM, at generation time**: the API already sees the real code (imports,
   HTTP client, URL) and classifies based on that — a stronger signal than any
   naming heuristic, because it sees the actual code.
3. **Deterministic heuristic, only for whatever stayed `unknown`**
   (`discovery/integration_heuristics.py`, no LLM): a short list of known vendors
   (Stripe, Twilio, AWS, ...) → `external`; a name that follows the same naming
   convention as already-indexed services (e.g. a `-service` suffix) →
   `internal` (not yet mapped).

When `target_kind` is `external`, the call also carries `resource_type`
(`queue`/`storage`/`compute`/`saas`/`db_managed`/`other`/`not_applicable`) — same
precedence: LLM first, the same vendor list (now also mapped to a resource type)
only fills the gap when the LLM returned `unknown`/absent.

This distinction feeds two buckets in `find_change_surface`:
- **`external_integrations`**: third-party integrations reachable from the
  `primary`/`secondary` services — the agent may need to touch that integration
  too.
- **`unmapped_internal_hint`**: dependencies that look internal but haven't been
  indexed yet — a signal to "index more of the system for the full picture".
  Each one also appears, restated, in `unknowns` (see below).

### Database and messaging: `engine` and `provider`

An ORM model/entity (SQLAlchemy, JPA, GORM) rarely reveals on its own which
database is behind it — that usually only exists in a connection string or the
dependency manifest. `persistence_entities.engine`
(`postgres`/`mysql`/`mongodb`/`cassandra`/`dynamodb`/`redis`/`elasticsearch`/`sqlite`/`unknown`)
and `messages.provider`
(`kafka`/`rabbitmq`/`sqs`/`sns`/`service_bus`/`activemq`/`nats`/`unknown`) follow
the same precedence as `target_kind`: the LLM decides from real evidence, with
two kinds of best-effort hint when the code alone isn't enough:
- **Dependency manifest** (`discovery/scan_helpers.engine_hint_from_manifest`):
  `psycopg2` in `requirements.txt` suggests Postgres, `mongoose` in
  `package.json` suggests MongoDB, etc. — a declared dependency is a cheaper,
  more reliable signal than scanning code text for a driver's name.
- **Configuration files** (`application.properties`/`.yml`, `.env`,
  `docker-compose.yml`) — used especially when the code only shows a
  transport-agnostic abstraction (JMS, Celery, NestJS microservices, Spring
  Cloud Stream) whose concrete broker only exists outside the code.

When nothing resolves it, the value stays honestly `unknown` — never a guess.

**`find_change_surface("Add Pix support to checkout")`** — the first tool an
agent should call upon receiving an epic, before opening any file. Uses only
already-indexed knowledge (keyword search over services/APIs/relationships +
graph expansion + a single LLM synthesis) — **never re-reads source code**. The
list of candidate services handed to the LLM is closed; any name it invents
outside that list is discarded before responding. The result is a **task
inference**, not a fact:
```json
{
  "primary": [
    {"service": "checkout-service", "reason": "owns the checkout entry point and forwards the payment method", "confidence": 0.95, "evidence": [...]},
    {"service": "payments-service", "reason": "owns payment method resolution and authorization", "confidence": 0.9, "evidence": [...]}
  ],
  "secondary": [
    {"service": "order-service", "reason": "consumes payment confirmation but does not own payment method logic", "confidence": 0.4, "evidence": [...]}
  ],
  "no_change_hint": [
    {"service": "notification-service", "reason": "only reacts to payment_authorized events, unrelated to the payment method itself", "confidence": 0.8, "evidence": []}
  ],
  "flow": [{"from": "checkout-service", "to": "payments-service", "type": "HTTP"}],
  "external_integrations": [
    {"service": "Stripe API", "via_service": "payments-service", "reason": "charge the customer's card via the vendor gateway", "confidence": 0.85, "evidence": []}
  ],
  "unmapped_internal_hint": [
    {"service": "shipping-service", "via_service": "order-service", "reason": "schedule delivery once the order is confirmed", "confidence": 0.6, "evidence": []}
  ],
  "contracts_at_risk": [
    {"contract": "payment_authorized", "producer": "payments-service", "consumers": ["notification-service"],
     "reason": "potentially affects its consumers; requires verification", "evidence": []}
  ],
  "persistence_affected": [
    {"service": "payments-service", "entity": "payment_method", "kind": "sql_table", "evidence": [...]}
  ],
  "unknowns": [
    {"status": "unknown", "service": "shipping-service", "reason": "looks internal but has not been indexed yet",
     "suggestion": "index this repository for a fuller picture"}
  ],
  "freshness": {
    "checkout-service": {"indexed_at": "2026-03-01T12:00:00+00:00", "source_commit": "a1b2c3d", "current_commit": "a1b2c3d", "stale": false},
    "payments-service": {"indexed_at": "2026-03-01T12:00:00+00:00", "source_commit": "a1b2c3d", "current_commit": "a1b2c3d", "stale": false},
    "order-service": {"indexed_at": "2026-03-01T12:00:00+00:00", "source_commit": "e4f5g6h", "current_commit": "9z8y7x6", "stale": true}
  },
  "recommended_next_queries": [
    {"tool": "describe_api", "arguments": {"service": "checkout-service", "method": "POST", "path": "/checkout"},
     "reason": "relevant service — inspect its API contract before changing it."},
    {"tool": "describe_messages", "arguments": {"service": "payments-service"},
     "reason": "publishes or consumes messages that may need to change too."},
    {"tool": "index", "arguments": {"service": "shipping-service"},
     "reason": "shipping-service looks internal but not indexed yet — index it for a fuller picture."}
  ],
  "similar_past_tasks": [
    {"run_id": 4, "task": "Add support for boleto in checkout", "similarity": 0.87,
     "primary_services": ["checkout-service", "payments-service"],
     "outcome": "verified precision=1.0 recall=1.0"}
  ],
  "run_id": 1,
  "run_cost_usd": 0.0123
}
```
- **`contracts_at_risk`**: events published by a `primary`/`secondary` service
  and who else consumes that channel — always in risk language ("potentially
  affects", "requires verification"), never declaring a confirmed break.
- **`persistence_affected`**: what each `primary`/`secondary` service persists,
  read straight from the index (no extra LLM cost) — answers "what about the
  data, what's affected?" without needing a separate `describe_persistence` call
  for the obvious cases.
- **`unknowns`**: every explicit gap in the response — an unmapped service, the
  whole task not matching anything indexed, **or a relevant service whose
  `freshness` came back `stale`** (meaning the stored dependency reasons about it
  may themselves be out of date) — with `status`, `reason` and `suggestion` — so
  an agent can branch on this instead of inferring "doesn't exist" from an empty
  list.
- **`freshness`**: per relevant service, whether the indexed knowledge might be
  stale (indexed commit vs. the repository's current commit). A `stale` service
  also generates a corresponding entry in `unknowns`.
- **`recommended_next_queries`**: a ranked list of `{tool, arguments, reason}` —
  the highest-value next MCP calls given what's already known, computed for free
  from what's already been read — **zero extra LLM cost**. It's the
  highest-return-per-token piece of the whole change surface: instead of just
  handing over context, it says **where to look next**.
- **`similar_past_tasks`**: up to 3 earlier `find_change_surface` runs whose task
  text was semantically closest to this one (local embeddings — see "Semantic
  retrieval fallback" below), each restated with its `primary_services` and an
  honest `outcome`: a real git-verified `"verified precision=... recall=..."`
  (from `verify_change_surface`) when one exists, a self-reported
  `"feedback: N confirmed, M rejected"` when only that exists, or
  `"no feedback yet"` — never fabricated. Only populated when the optional
  `semantic` extra is installed; an empty list otherwise, never an error.
  Historical precedent: has a similarly-worded task actually panned out before?
- **`run_cost_usd`**: this call's own LLM cost, when the backend's CLI exposed
  it — `null` (never fabricated as `0`) when it didn't.

Accepts an optional `hint_services` to anchor the search when the agent already
suspects specific services. If the task doesn't match anything indexed, it
returns empty lists with a `note` (for humans) and an `unknowns` (structured, for
the agent) explaining why — without calling the LLM.

### Semantic retrieval fallback

`find_change_surface`/`search`'s primary retrieval is always FTS5 keyword
matching — free, no model, same as before. When the optional `semantic` extra
is installed (`pip install orbitkb[semantic]`, see "Installation"), a second
strategy is layered on top as a **fallback only**: if keyword retrieval finds
zero candidates, a local, CPU-only embedding model
(`generation/embeddings.FastEmbedBackend`, via the `fastembed` package — no
paid API, no network call after the first use) embeds the task and cosine-ranks
it against every indexed service's own embedding (computed once, whenever a
service's overview is generated, stored in `service_embeddings`). Candidates
below a similarity floor (a starting constant, not a trained value — same
honesty as `find_architecture_smells`'s `FAN_THRESHOLD`) are dropped. The
common path — keyword retrieval finds something — pays zero extra cost or
latency: the embedding model is never even loaded unless the fallback actually
runs. The same embedding infrastructure also backs `similar_past_tasks` above
(one vector per `change_surface_runs` row, in `change_surface_run_embeddings`).

### Audit and feedback

Every `find_change_surface` call that actually ran the LLM is recorded
(`change_surface_runs`/`change_surface_findings`) and the `run_id` comes back in
the response. After acting on the result, the agent can close the loop by hand:
```
record_change_surface_feedback(run_id=1, service="payments-service", outcome="confirmed")
```
`outcome` is `"confirmed"` (the service really did need to change) or
`"rejected"` (it didn't). Future `find_change_surface` calls for that same
service have their `confidence` recalibrated based on this history — only after
a minimum of 3 accumulated feedback entries, and as a light adjustment (30%) on
top of the LLM's fresh guess, never replacing the judgment made from the current
task's evidence.

### Ground truth via git: `verify_change_surface` / `orbitkb verify`

Instead of relying only on the agent remembering to report the outcome, you can
check a past prediction against a repository's real `git diff` since a commit:
```
verify_change_surface(run_id=1, repository="my-monorepo", since_commit="a1b2c3d")
```
```json
{
  "run_id": 1, "repository": "my-monorepo", "since_commit": "a1b2c3d",
  "predicted": ["checkout-service", "payments-service"],
  "actual": ["checkout-service"],
  "true_positives": ["checkout-service"],
  "false_positives": ["payments-service"],
  "false_negatives": [],
  "precision": 0.5, "recall": 1.0,
  "verification_id": 7
}
```
The MCP version is read-only (it doesn't record feedback on its own); the CLI
has a `--record-feedback` flag that optionally records `confirmed`/`rejected`
automatically from the result:
```bash
orbitkb verify 1 --repository my-monorepo --since a1b2c3d --record-feedback
```
Every verification is saved (`change_surface_verifications`) and shows up in
`orbitkb status`. It's intentionally scoped to **one repository at a time** —
comparing several unrelated git histories under a single `--since` wouldn't make
sense; in a cumulative multi-repository setup, you run one `verify` per
repository, the same way indexing is also done one repository at a time.

## Token/cost accounting

Every `index_runs` row (one per `orbitkb index`/`update` invocation) and every
`change_surface_runs` row (one per `find_change_surface`/`analyze` call) carries
best-effort `input_tokens`, `output_tokens` and `cost_usd`, read from whatever
the `claude`/`codex` CLI's own JSON output reports for that call
(`generation/backend_base.LLMUsage`). "Best-effort" is deliberate here: no key
beyond `structured_output`/`is_error`/`result` was ever verified against a real
`claude -p --output-format json` payload in this codebase before this was
added, so extraction is defensive (`payload.get(...)`) and every field stays
honestly `None` instead of raising or guessing when the CLI doesn't carry it —
same posture as `persistence_entities.engine = "unknown"`.

`orbitkb index`/`update` print each service's `cost_usd` on completion;
`orbitkb status` prints tokens/cost per recent run plus a cumulative total
(service-scoped with `orbitkb status <service>`, whole-DB otherwise):
```
$ orbitkb status orders-service
service: orders-service (python) — /path/to/orders-service
last_commit: a1b2c3d
indexed files: 4
  run#12 2026-03-01T12:00:00+00:00 status=ok backend=claude files_changed=2 llm_calls=3 tokens=(in=4200,out=650) cost_usd=0.0187 notes=None
cumulative usage: input_tokens=18400 output_tokens=2950 cost_usd=0.0821
```
`find_change_surface` surfaces its own call's cost directly in the response as
`run_cost_usd` (see above) — no separate `status` round trip needed just to see
what one call cost.

## Diagrams (Mermaid)

`orbitkb export mermaid --out docs/` generates:
- **`topology.mmd`** — a `graph TD` of the whole system: every indexed service is
  a node, every external vendor reached is a rounded node, edges from
  `service_calls` and `MESSAGE_LINK` connect everything. Services involved in a
  cycle (`find_architecture_smells`) come out highlighted with a distinct style.
- **`<service>/er.mmd`** — an `erDiagram` per service, with the persisted
  entities and their fields. No relationship lines between entities — the index
  doesn't track foreign keys yet (see "Known limitations") — the file itself
  says so explicitly instead of inventing a relationship.

Text, not an image, on purpose: a `.mmd` file is versionable, shows up in a PR's
diff, and renders natively on GitHub/GitLab/most editors — a rendered image would
be a second source of truth that could go stale without anyone noticing. Both are
generated 100% from SQLite, with no LLM cost.

## What's already implemented

- **Heuristic discovery** (regex/file signature, no full AST parser) for 4
  stacks: Node.js/TypeScript (Express/NestJS), Python (FastAPI/Flask/Django), JVM
  (Java/Kotlin + Spring Boot) and Go. Covers HTTP/gRPC, Kafka/RabbitMQ/SQS/SNS and
  transport-agnostic abstractions (JMS, Celery, NestJS microservices, Spring
  Cloud Stream) across all 4 stacks.
- **Hierarchical generation** (endpoint → component → service, see "Idea"
  above): call attribution scoped to the endpoint's own files
  (`EndpointHint.dependency_files()`), one-hop navigation to a local function
  called inside the handler (`extra_excerpts`, resolved via
  `discovery.scan_helpers.resolve_local_calls`), a component layer
  (`components`) synthesized from the endpoint summaries already generated, the
  overview composed last from the component summaries.
- **Pluggable generation**: `claude` or `codex` backend, both headless via CLI,
  using the user's subscription (not a paid API) by default. The returned JSON is
  validated against a schema, with 1 retry and per-unit failure isolation (one
  failure never aborts the whole run). Invocation harness (prompt/schema loading
  + retry + best-effort token/cost usage summed across retries)
  shared between indexing and `find_change_surface`
  (`generation/llm_harness.py`).
- **SQLite as the source of truth** — a single System Knowledge Model, with
  `repositories` (explicit, cumulative multi-repository) and
  `services.repository_id`, and incremental file-hash-based updates: only the
  unit (endpoint/component/persistence/messaging/overview) whose file changed
  gets regenerated. Reconciliation of `target_kind`/`resource_type`/`to_service_id`
  (`reconcile_service_call_targets`) is scoped to the service just written during
  indexing — not a full-table scan on every API — and only runs unscoped (whole
  table) once per new service, to resolve other services' calls that named it
  before it existed.
- **Repository layer split by aggregate** (`db/repositories/`: `services`,
  `apis`, `components`, `service_calls`, `persistence`, `messages`,
  `architecture`, `indexed_files`, `change_surface`, `verification`,
  `index_runs`, `search`, `repositories`, `embeddings`) — each module only
  knows its own tables; no other module runs SQL directly. `embeddings` is one
  module for two tables (`service_embeddings`, `change_surface_run_embeddings`)
  since both are the same "vector storage for X" concern for two different
  aggregates.
- **Hierarchical generation as one Strategy/Template Method per unit**
  (`generation/orchestrator.py`: `EndpointGenerator`, `ComponentGenerator`,
  `PersistenceGenerator`, `MessagingGenerator`, `OverviewGenerator`, all
  implementing `UnitGenerator`) — `index_service` is a thin coordinator that
  runs them in the order the hierarchy above requires, instead of one long
  function inlining all five.
- **Token/cost accounting** (`generation/backend_base.LLMUsage`,
  best-effort, honestly `None` when unavailable — see "Token/cost accounting"
  above): summed per `index`/`update` run and per `find_change_surface`/`analyze`
  call, surfaced via `orbitkb status` and `run_cost_usd`.
- **Bounded, paginated MCP list responses** (`describe_service`, `list_apis`,
  `describe_persistence`, `describe_messages` — see "Pagination" above): a real
  service's endpoint/entity/message count no longer determines the response
  size by default.
- **Local semantic retrieval fallback** (`generation/embeddings.py`,
  `generation/retrieval.SemanticRetrieval`/`FallbackRetrieval` — see "Semantic
  retrieval fallback" above), and **`similar_past_tasks` historical
  precedent** in `find_change_surface`, reusing the same embedding
  infrastructure — both opt-in via `pip install orbitkb[semantic]`, both
  degrade to today's exact behavior (empty/absent, never an error) when not
  installed.
- **Architecture-smells trend** (`generation/architecture.diff_architecture_runs`,
  `find_architecture_smells`'s `trend` field — see above): new/resolved
  findings and fan-in/fan-out count deltas between the two most recent runs,
  pure SQL/in-memory diff, no LLM, no re-detection.
- **Explicit self-indexing via `--stack`** (`orbitkb index . --service <name>
  --stack <node-ts|python|jvm-spring|go>`, `discovery/registry.detector_by_id`):
  bypasses `discover_services()`/`matches()` entirely for a folder shape no
  heuristic recognizes (a library/CLI package, exactly `orbitkb`'s own shape),
  without weakening detection precision for the default heuristic path. This
  is what `tests/test_self_index_e2e.py` now drives through the real CLI, and
  what also proves cumulative multi-repository indexing end to end in the same
  test.
- **Persisted evidence**: `apis`, `components`, `service_calls`,
  `persistence_entities` and `messages` carry `evidence_json` (file + line) — the
  exact excerpt the LLM saw when it generated that piece of information, never a
  line invented afterwards. `service_calls` also carries `confidence` (0-1,
  assessed by the LLM itself), `target_kind` and `resource_type` (LLM with the
  real code as the primary signal, deterministic vendor/naming heuristic as a
  fallback only for whatever stayed unresolved). `persistence_entities` carries
  `engine` and `messages` carries `provider`, same precedence. `apis` also
  carries structured `request_shape` (field, type, `required`), mirroring the
  `response_shape` that already existed — the data foundation for deeper
  Contract Intelligence (comparing contracts across indexing runs to catch a real
  break isn't implemented yet, see "Known limitations").
- **Explicit provenance and freshness**: `provenance` (`llm` vs.
  `deterministic`) formalizes the fact/interpretation distinction where it was
  already implicit; `freshness` (`generation/freshness.py`) compares the indexed
  commit against the repository's current commit on demand — never persisted, so
  it can never itself go stale.
- **Deterministic architecture smells** (`generation/architecture.py`,
  `find_architecture_smells`): cycle (Tarjan/SCC), disproportionate
  fan-in/fan-out, shared database, duplicate external integration — recomputed on
  every `index`/`update`, no LLM, versioned in
  `architecture_runs`/`architecture_findings` the same way `change_surface_runs`
  already is.
- **Export to Markdown** (human-readable) and **Mermaid** (topology + ER), both
  generated 100% from SQLite.
- **`find_change_surface` as a Builder** (`generation/change_surface.ChangeSurfaceBuilder`):
  each piece of the response (findings, flow, external integrations, contracts at
  risk, unknowns, freshness, recommended next queries) is assembled by its own
  method, instead of one dict growing ad hoc. Candidate retrieval is a pluggable
  strategy (`generation/retrieval.KeywordGraphRetrieval`).
- **`recommended_next_queries`** (`generation/next_queries.py`): the
  highest-value next MCP call given what's already been computed, at no extra
  LLM cost.
- **`contracts_at_risk`**: consumers of an event published by a relevant
  service, reusing the same channel join `get_relationships` already uses.
- **Ground truth via git** (`generation/verification.py`, `orbitkb verify`):
  compares a past prediction against a repository's real `git diff`, computes
  precision/recall and can record feedback automatically.
- **MCP server** with 17 tools (one writes feedback; `verify_change_surface`
  records an audit entry but doesn't record feedback on its own): the 7 original
  ones (`list_services`, `describe_service`, `list_apis`, `describe_api`,
  `describe_persistence`, `describe_messages`, `search`) plus ten for
  navigation/inference/verification: `list_repositories` (cumulative
  per-repository view), `list_entrypoints`, `describe_entrypoint`,
  `list_security_findings`,
  `get_relationships`, `trace_flow`,
  `find_architecture_smells`, `find_change_surface`,
  `record_change_surface_feedback` and `verify_change_surface`. Every tool
  documents in its own docstring when to call it, what it returns, and what the
  natural next tool is — the progressive-disclosure narrative lives in the MCP
  schema, not just in this README. Registrable with any MCP client (Claude CLI,
  Codex, etc.).
- **Full-text search (SQLite FTS5)** as the primary, always-free retrieval
  path: `search` and `find_change_surface`'s candidate retrieval use an FTS5
  index (prefix match + `bm25` ranking) rebuilt per service on every indexing
  run (`db.repositories.search`). The optional semantic fallback (see above)
  only ever runs as a `FallbackRetrieval` secondary when this finds nothing.
- **CI** (GitHub Actions, `.github/workflows/ci.yml`): runs the whole suite on
  Python 3.11 and 3.12 on every push/PR — no test depends on a real
  `claude`/`codex` CLI (the backend is always fake, or data is seeded directly
  via `db.repositories.*`).
- **CLI** (`index`, `update`, `list`, `status`, `export`, `analyze`, `verify`,
  `serve`) installable globally via `pipx install orbitkb`, self-explanatory via
  `--help` (every subcommand has a ready example), with a terminal progress bar
  (spinner, percentage, per-unit colored status) during `index`. `analyze` runs
  `find_change_surface` directly through the domain layer, with no MCP session
  needed — the same function the MCP server calls, no duplicated logic. `index`
  accepts `--repository-name` to index several distinct repositories into the
  same DB, cumulatively, without name collisions; `export` accepts `md` or
  `mermaid`; `serve` accepts `--backend`/`--model` (only used by
  `find_change_surface`); `--version` reports the installed version.
- **Automated tests** (pytest, TDD cycle, red→green→refactor): discovery (3 of
  the 4 stacks), each `db/repositories/` module in isolation,
  `generation/orchestrator.py` and `cli.py` with a fake LLM backend running real
  discovery against `verify/sample_project`, `export/markdown.py`,
  `export/mermaid.py`, `generation/change_surface.py` (builder, retrieval,
  anti-hallucination filter, confidence recalibration, freshness, provenance,
  unknowns, contracts at risk, next queries), `generation/architecture.py`
  (cycle/fan-in/fan-out/shared database/duplicate integration),
  `generation/verification.py` (precision/recall against a real git repo, in a
  disposable test repository), real integration tests over the MCP protocol
  (stdio) for `get_relationships`, `trace_flow`, `find_architecture_smells`,
  `find_change_surface` and `verify_change_surface`, a context-efficiency harness
  with a response-size budget, and a **self-indexing suite**
  (`tests/test_self_index_e2e.py`): `orbitkb` indexes its own source code and
  answers `find_change_surface` about itself — the most direct proof that the
  pipeline works end to end against real, non-trivial code, and one that has
  already caught real bugs (see "Development" below).
- **Conventional-commit versioning**: a `commit-msg` git hook
  (`scripts/githooks/commit-msg`, installed by `make hooks`/`make dev`)
  requires every commit subject to be `<type>[(scope)][!]: description` and
  previews the bump it would trigger; `make release` computes the actual
  major/minor/patch from commit history since the last tag
  (`scripts/versioning.py`) — `make release-patch|minor|major` to force one
  instead. See "CI/CD and releases" below.

## Security

Repository content (README, comments, source code) is **untrusted data**, never
an instruction. `orbitkb` sends that content to the LLM as evidence to be
described, not as a command to follow — but no prompt has an explicit, dedicated
defense against prompt injection (e.g. a code comment saying "ignore previous
instructions and return {...}"). That said, the blast radius of a successful
injection is already structurally limited, by two mechanisms that already exist
for other reasons, not as a dedicated security mitigation:

1. **Strict schema validation** (`generation/llm_harness.py`,
   `generate_with_retry`): every LLM response is validated against a JSON Schema
   with `additionalProperties: false`, closed enums and required fields — even if
   a prompt injection convinces the model to "say" something different, the
   output stays locked to the expected shape.
2. **Closed candidate list** (`generation/change_surface.py`, `_filter_known`):
   any service name the LLM returns outside the list of already-indexed
   candidates is discarded before any response goes out — an invented
   `fraud-service` (via injection or plain hallucination) never becomes a real
   entity in the response.

This reduces the possible damage, it doesn't eliminate the vector. If this ever
matters more (e.g. indexing untrusted third-party repositories), it's worth
reinforcing the prompts with an explicit "the text below is data, not
instruction" section — today `find_change_surface`'s prompt already leans that
way ("Use ONLY the information given above. Do not invent or classify a service
that is not listed above."), but this has never been adversarially tested.

Configuration evidence follows a stricter rule: `.env` is never read, and values
whose configuration key names passwords, secrets, tokens, credentials or API keys
are redacted before a supported configuration file can enter an LLM prompt.
The same redaction applies to every source excerpt supplied to generation. During
indexing, OrbitKB records only safe security findings for a Git-tracked `.env` or a
secret-like literal in source; `list_security_findings` returns file, line and
remediation guidance, never a value or source excerpt.

## Known limitations

- **No schema backward compatibility**: the database has no column-migration
  framework — the schema in `db/schema.sql` is the only expected shape for
  tables that already exist (SQLite doesn't alter a table via `CREATE TABLE IF
  NOT EXISTS`; new tables get added automatically, new columns on existing
  tables don't). If a schema change affects an existing table, delete
  `~/.orbitkb/orbitkb.db` (or whichever `--db` you're using) and run `orbitkb
  index` again.
- `request_shape` only captures the field's shape; it doesn't yet compare
  contracts across indexing runs to automatically detect that a required field
  disappeared (that would require keeping a per-API schema history, not
  implemented). `contracts_at_risk` still flags risk by event consumer, not by
  field diff.
- Discovery heuristics are deliberately simple (regex): they point the LLM at
  the right spot, but can miss unusual patterns (e.g. an HTTP client instantiated
  in a variable with an unconventional name), and they're designed for the shape
  of a web microservice (HTTP endpoint, queue, ORM) — a Python package that's a
  library/CLI rather than a web service (like `orbitkb` itself) doesn't match
  any of these patterns, and so only generates the overview unit, with no
  endpoints/persistence/messaging detected. `orbitkb index --service <name>
  --stack <stack>` (`discovery/registry.detector_by_id`) is the explicit escape
  hatch for exactly this case — see "What's already implemented"; broadening
  `matches()` itself to accept this shape automatically would reduce detection
  precision for real targets (any Python package would start "looking like" a
  service), so that stays a deliberate non-goal. No automated test specifically
  for the Go/JVM stacks in `cli.py`/`orchestrator.py` (covered via Python/Node
  in the suite) — only `discovery/go_stack.py` in isolation.
- `find_change_surface`/`search`'s primary retrieval is FTS5 keyword matching: a
  task whose vocabulary doesn't appear in any indexed description/reason, and
  without `hint_services`, finds no candidates via that path alone. The optional
  semantic fallback (`pip install orbitkb[semantic]`, see "Semantic retrieval
  fallback") closes most of this gap, but its similarity floor
  (`SemanticRetrieval.SIMILARITY_FLOOR`) is a starting constant, not a trained
  value, and it's still opt-in — without the extra installed, behavior is
  exactly as before.
- Confidence recalibration (`record_change_surface_feedback`) is still by exact
  service name only, without generalizing across similar tasks or across
  services — each one accumulates its own history, from scratch.
  `similar_past_tasks` (see above, needs the optional `semantic` extra) now
  surfaces historical precedent for a similarly-worded task as informational
  context, but it's read-only — it doesn't itself feed back into confidence
  recalibration, which remains exact-service-name-only.
- `verify_change_surface`/`orbitkb verify` are scoped to one repository at a
  time — there's no notion of a "cumulative diff" across several unrelated
  repositories under a single reference commit.
- The deterministic `target_kind`/`resource_type` heuristic
  (`discovery/integration_heuristics.py`) has a short, manually curated list of
  known vendors — a vendor outside the list falls into `unknown` (never wrongly
  into `external`; the list was built to avoid false positives, not false
  negatives). It only kicks in when the LLM (which already saw the real code)
  couldn't classify — in practice it covers the minority of cases.
- `find_architecture_smells` doesn't yet model two things that were discussed
  but not implemented: legacy/strangler-fig tagging (`role: legacy` on a
  service, to tell a monolith mid-migration apart from a brand-new service) and
  directional cycle severity (a cycle involving a legacy service is more serious
  than one between two peers — the extraction leaked a dependency back). Trend
  across successive runs is now implemented (see `trend` above), but it only
  ever compares the two most recent runs, not a longer history. A modular
  monolith (several domain boundaries inside a single deploy) also isn't
  detected as such — it becomes one flattened `services` row; the component
  layer is the necessary foundation for a future clustering step, but that
  clustering itself doesn't exist yet.
- `persistence_entities`/`erDiagram` don't track relationships between entities
  (foreign keys) — each entity is described in isolation, with its fields, with
  no relationship line in the ER diagram.
- No scheduled/batch job detection (CronJob, Airflow DAG, `@Scheduled`,
  `node-cron`, Celery beat) — `service_calls` doesn't distinguish a synchronous
  call made from inside an HTTP endpoint from one made from inside a nightly
  batch job (`interaction_style` sync/async/batch, discussed, not implemented).

## Development

```bash
make dev              # pip install -e ".[dev]" + install git hooks
make hooks            # install git hooks on their own (core.hooksPath=scripts/githooks)
make test             # deterministic suite; native analysis tests run in a separate process
make coverage         # pytest with coverage report
make lint             # ruff (unused imports/vars) + vulture (dead code)
make sast             # bandit static security scan
make sca              # pip-audit dependency vulnerability scan
make security         # sast + sca
make dast             # live MCP server adversarial-input test
make verify           # test, then lint + sast + sca + dast in parallel
make build            # sdist + wheel into dist/
```
(every target is a thin wrapper — see the `Makefile` for the exact command it
runs; `coverage` combines reports from the isolated native-analysis and remaining
test processes.)

CI (`.github/workflows/ci.yml`) runs exactly the deterministic test suite on
Python 3.11/3.12 on every push/PR. The MCP E2E test builds a temporary database
from `verify/sample_project`, so it never relies on a developer's local database.
Locally, `make test` runs native Tree-sitter analysis tests in their own process so
macOS interpreter teardown cannot interfere with the MCP/async test session; both
commands must pass for the target to succeed.

`get_relationships`/`trace_flow`/`find_architecture_smells`/`find_change_surface`/
`verify_change_surface` are exercised via a real MCP session (stdio), which
spins up `orbitkb.mcp.server` in a **subprocess** — for coverage to see that
subprocess (instead of reporting `mcp/server.py`/`mcp/queries.py` as 0% even
though they're tested), you need a `coverage` hook in the venv's site-packages
plus the `COVERAGE_PROCESS_START` variable:
```bash
echo "import coverage; coverage.process_startup()" > $(python -c "import site; print(site.getsitepackages()[0])")/coverage_subprocess.pth
COVERAGE_PROCESS_START=pyproject.toml python -m coverage run -m pytest tests/
python -m coverage combine && python -m coverage report -m
```

The automated suite never calls a real LLM (fake/deterministic backends, DBs
seeded directly via `db.repositories.*`) — including the self-indexing suite
(`tests/test_self_index_e2e.py`), which drives real discovery against
`orbitkb`'s own source code through the real CLI path (`cli._cmd_index`, with
`--stack python` — see "What's already implemented") with a fake backend, and
also indexes `verify/sample_project` into the same database to prove
cumulative multi-repository indexing concretely. To validate the real pipeline
end to end — discovery → real LLM generation → SQLite → MCP —, use the
project's own fixture as a manual e2e test:
```bash
orbitkb index verify/sample_project --backend claude --db verify/sample_project.db
orbitkb serve --db verify/sample_project.db
```
`verify/sample_project.db` is gitignored; it is only a convenient local artifact
for manually querying the real generated knowledge.

Self-indexing `orbitkb` itself with a **real** backend now works via the CLI
directly, using the same `--stack` escape hatch the automated suite exercises
with a fake one:
```bash
orbitkb index orbitkb --service orbitkb-core --stack python --backend claude --db verify/self_index.db
```
This is exactly the real validation done during development (back when this
required calling `generation.orchestrator.index_service()` directly with an
explicit detector, before `--stack` existed): the generated overview correctly
described the project's own architecture (CLI/MCP, the 4 discovery stacks, the
generation pipeline, the exports), and `find_change_surface("Add NATS support
to Go-stack discovery")` pointed at `orbitkb-core` as `primary` with 0.9
confidence and the right reason — the scanner itself is what would need to
change.

### Benchmark: retrieval recall (CI) vs. real precision/recall (manual)

`benchmark/` measures whether `find_change_surface` actually finds the right
services — not just whether the mechanism doesn't break. It's deliberately split
into two levels, because a benchmark that feeds a fake backend the "right
answer" and then checks whether the pipeline reproduces that answer is circular;
it proves nothing about the system's judgment:

- **Retrieval recall (`tests/test_benchmark.py`, runs in CI)**: measures only
  whether `KeywordGraphRetrieval.candidates()` — no LLM involved — puts each
  task's expected services into the candidate set, before any LLM (real or fake)
  gets a chance to choose among them. Is a task findable today, and will it still
  be tomorrow? `benchmark/tasks.py` has 8 hand-picked tasks (there's no real
  corpus of past tasks yet) covering different reach patterns: direct keyword
  match, 1- and 2-hop expansion via calls, expansion via a messaging link, and
  anchoring by `hint_services` alone. Run `python scripts/run_benchmark_report.py`
  to see the table. The same report also shows `reduction_ratio` per task: of
  all services indexed in that task's fixture, what fraction
  `KeywordGraphRetrieval` did **not** need to put forward as a candidate — the
  deterministic, non-circular half of "exploration reduction" (the other half —
  comparing an agent's tool calls/tokens with and without real `orbitkb` — would
  require simulating a "baseline agent", which would be fabricated and
  unverifiable, so it stays a manual methodology, not code). This number depends
  heavily on the size/connectivity of the indexed system: in this benchmark's
  small, well-connected fixtures (the "Pix" scenario), 2-hop expansion naturally
  reaches the whole system and the reduction is 0 — that's not a regression,
  it's the fixture's real size. The "catalog" fixture (two disconnected
  components) shows a real reduction. Read the number as "how much this specific
  scenario reduced", not as a ceiling on what the system can achieve in
  production. A separate, narrower test in the same file
  (`test_semantic_fallback_recalls_a_task_that_defeats_keyword_matching`) proves
  the optional semantic fallback (see "Semantic retrieval fallback") actually
  recalls a task purpose-built to defeat keyword matching, using a fake
  embedding backend — kept out of the main 8-task set above since that one is
  deliberately keyword-only/zero-cost by design.
- **Real precision/recall (manual, doesn't run in CI)**: only a real LLM can
  answer whether `find_change_surface`'s *judgment* is actually right. After
  indexing `verify/sample_project` (or another real project) with a real
  backend, run `orbitkb analyze "<task>" --backend claude --db
  verify/sample_project.db` for each task in `benchmark/tasks.py` (no need to
  spin up an MCP session) and compare `primary`/`secondary` against
  `expected_services` by hand — the same manual treatment
  `verify/sample_project.db`'s real e2e already gets.
  `verify_change_surface`/`orbitkb verify` automates that comparison once a
  real "after" commit exists to compare against via `git diff`.

As real engineering tasks happen on this project (or another one indexed by it),
`benchmark/tasks.py`'s corpus should grow with them instead of invented tasks —
that's what makes it a real benchmark, not a list of illustrative examples.

## CI/CD and releases

Three GitHub Actions workflows, all in `.github/workflows/`:

- **`ci.yml`** — on every push/PR: the deterministic test suite on Python
  3.11/3.12, plus a `lint` job (ruff + vulture). Never touches a real LLM
  backend (see "Development" above).
- **`security.yml`** — on every push/PR to `main`, plus weekly: a `sast` job
  (bandit static analysis + pip-audit dependency scan) and a `dast` job that
  drives the real MCP server over stdio with adversarial inputs
  (`tests/test_dast_adversarial_inputs.py` — SQL injection, prompt injection,
  FTS5 syntax abuse, oversized/malformed payloads). This is dynamic on purpose:
  it caught a real crash during development (an SQLi-shaped query like `' OR
  '1'='1` reached FTS5's parser as a bare, unquoted `OR` token and raised
  `sqlite3.OperationalError` instead of just matching nothing — fixed in
  `db/repositories/search.py`'s `_build_fts_query` by quoting every token).
- **`publish.yml`** — on a `v*.*.*` tag push: `test`, `lint`, `sast`, `sca` and
  `dast` fan out as independent jobs; a `gate` job fans back in on all five
  (`needs: [test, lint, sast, sca, dast]`); `build` and `publish` only run if
  `gate` does. Publish pushes to PyPI via
  [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC — no
  long-lived token stored as a repo secret). This is the *authoritative* gate —
  a broken check here blocks PyPI no matter what a developer's local `make
  verify` reported.

### One-time PyPI setup

Before the first tag push, configure Trusted Publishing on PyPI: on the
project's page (or, before it exists yet, at
https://pypi.org/manage/account/publishing/), add a pending publisher with:
- Repository owner: `manorfm`
- Repository name: `orbitkb`
- Workflow name: `publish.yml`
- Environment name: `pypi`

No secret needs to be added to the GitHub repo — the `id-token: write`
permission already declared in `publish.yml` is what lets GitHub Actions prove
its identity to PyPI at publish time.

### Cutting a release

```bash
make release          # bump computed from Conventional Commits since the last tag
make release-patch    # or release-minor / release-major, to force one instead
```
Both forms run the same pipeline:

1. **Fan-out/fan-in locally** (`make verify` → `scripts/preflight.sh`):
   `test` runs first; `lint`, `sast`, `sca`, `dast` then run concurrently. Any failure aborts
   the release right here — nothing is bumped, committed, tagged or pushed.
2. Refuses to continue unless you're on `main`, the working tree is clean, and
   local `main` isn't behind `origin/main`.
3. `make release` computes the bump from commit history since the last tag
   (`scripts/versioning.py`, the same logic the `commit-msg` hook previews on
   every commit — see "What's already implemented" above);
   `release-patch|minor|major` use the forced kind instead.
4. Bumps `pyproject.toml` + `orbitkb/__init__.py`
   (`scripts/bump_version.py`), refreshes the README badges
   (`scripts/ensure_badges.py`), commits, tags `vX.Y.Z`, and pushes both the
   commit and the tag.
5. Pushing the tag triggers `publish.yml`, which reruns the same five checks
   in CI — the actual gate, since that's the only place holding the PyPI
   Trusted Publishing credential — and only then builds and publishes.

There's no "review the diff before pushing" step anymore: the fan-out/fan-in
gate (local, then CI) is what stands in for that review. If any check is red,
the release stops before touching git — see `scripts/preflight.sh` and the
`gate` job in `publish.yml`.

## Quick reference

**CLI** (`orbitkb <command> --help` for examples): `index`, `update`, `list`,
`status`, `export` (`md`|`mermaid`), `analyze`, `verify`, `serve`, `--version`.

**MCP** (`orbitkb serve`): `list_repositories`, `list_services`,
`describe_service`, `list_apis`, `describe_api`, `list_entrypoints`,
`describe_entrypoint`, `describe_persistence`,
`describe_messages`, `search`, `get_relationships`, `trace_flow`,
`find_architecture_smells`, `find_change_surface`, `record_change_surface_feedback`,
`verify_change_surface`.
