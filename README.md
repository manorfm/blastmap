# OrbitKB

<!-- BADGES:START -->
[![PyPI](https://img.shields.io/pypi/v/orbitkb.svg)](https://pypi.org/project/orbitkb/)
[![Python versions](https://img.shields.io/pypi/pyversions/orbitkb.svg)](https://pypi.org/project/orbitkb/)
[![CI](https://github.com/manorfm/orbitkb/actions/workflows/ci.yml/badge.svg)](https://github.com/manorfm/orbitkb/actions/workflows/ci.yml)
[![Security](https://github.com/manorfm/orbitkb/actions/workflows/security.yml/badge.svg)](https://github.com/manorfm/orbitkb/actions/workflows/security.yml)
[![License](https://img.shields.io/pypi/l/orbitkb.svg)](https://pypi.org/project/orbitkb/)
<!-- BADGES:END -->

OrbitKB is an MCP knowledge layer that gives coding agents the smallest useful,
evidence-backed view of a software change before they edit code.

It indexes one or many repositories into a cumulative SQLite knowledge base, then
answers questions such as: “What needs to change to add feature X?” Agents receive
bounded impact, flow, contract, dependency and risk context instead of searching a
whole repository or loading unrelated documentation.

> [!IMPORTANT]
> OrbitKB is not an autonomous coding agent and does not modify source code. It is
> also not a generic code graph or repository-wide RAG system. Its purpose is to
> give an agent precise context so the agent can plan and act safely.

## Why use it?

Large systems make seemingly small changes expensive to understand. A new feature can
cross an HTTP endpoint, application flow, database, broker, another service and an
external partner. The relevant knowledge is normally fragmented across source files,
repositories and people.

OrbitKB turns that into progressive, queryable context:

1. It extracts deterministic facts locally from source.
2. It stores concise service and contract knowledge with provenance.
3. An agent asks for a compact change briefing, then drills into only the endpoints,
   flows or dependencies that matter.
4. After delivery, Git-based verification and feedback can measure whether the
   predicted change surface was useful.

The result is not “more context”; it is less irrelevant context and a clearer record
of what is known, inferred, stale or still unknown.

## Quick start

Install the CLI:

```bash
pip install orbitkb
# or, for an isolated executable:
pipx install orbitkb
```

Index a repository, ask for a bounded implementation briefing, and inspect it with an
MCP client:

```bash
orbitkb index /path/to/shop --repository-name shop --backend codex

orbitkb context "Add Pix as a checkout payment method" \
  --repository shop \
  --max-services 3

orbitkb serve --backend codex
```

Every command has runnable help:

```bash
orbitkb --help
orbitkb index --help
```

The core workflow is **index → ask → verify**.

```bash
# After the implementation has landed, compare the predicted surface with Git.
orbitkb verify <run-id> --repository shop --since <commit>
```

## Connect an MCP client

Register `orbitkb serve` as a stdio MCP server. A generic client configuration looks
like this:

```json
{
  "mcpServers": {
    "orbitkb": {
      "type": "stdio",
      "command": "orbitkb",
      "args": ["serve", "--backend", "codex"]
    }
  }
}
```

Use the absolute executable path if `orbitkb` is not on the PATH inherited by the
MCP client. Indexing and change-surface synthesis require an authenticated supported
headless backend. Read-only inspection of an existing database does not.

## What an agent can ask

Start broad, then narrow the request.

| Need | Start with | Follow with |
| --- | --- | --- |
| Start a change plan | `plan_change` | `refine_change_plan`, `describe_change_unit`, `assess_working_change` |
| Plan an epic | `get_change_context` | `describe_service`, `describe_entrypoint` |
| Find likely impact | `find_change_surface` | `get_relationships`, `describe_api` |
| Understand a request path or its static error mapping | `list_entrypoints` | `describe_entrypoint` |
| Inspect data or events | `describe_persistence`, `describe_messages` | `get_relationships` |
| Inspect runtime configuration use | `describe_configuration` | `describe_entrypoint`, `list_security_findings` |
| Inspect Kubernetes configuration sources | `describe_runtime_configuration` | `describe_configuration`, `describe_entrypoint` |
| Inspect feature-flag reads | `describe_feature_flags` | `describe_entrypoint`, `list_security_findings` |
| Inspect cloud/infra dependencies | `describe_cloud_dependencies` | `find_architecture_smells` |
| Find architectural risks | `find_architecture_smells` | evidence and remediation in the finding |
| Compare runtime and static paths | `describe_runtime_divergence` | `describe_entrypoint` |

`get_change_context` returns at most five compact service cards. It includes likely
impact, flow, contracts at risk, persistence, messaging, architectural hypotheses,
unknowns and recommended next queries. A selected service card can additionally
include up to three source-proven, unambiguous static HTTP dependencies with their literal
target routes and resolved service identity; when indexed, the remote endpoint link
and code location are included. It does not reread the source tree while answering.
Its privacy-safe telemetry persists the same token measurement provenance as
`plan_change`; context-budget metrics group runs by `tiktoken:o200k_base` or the
explicitly approximate `byte_estimate`, without retaining response text.
Repeated `find_change_surface` requests with the same backend and unchanged candidate
context reuse their prior structured synthesis at zero model-call cost; current graph,
freshness and contract facts are still recomputed for each response.
`plan_change.budget.measurement` records how its response count was calculated. The
default `byte_estimate` is explicitly approximate; install `orbitkb[tokens]` to use
the local `tiktoken:o200k_base` tokenizer for a reproducible measurement. The method
is persisted with the plan, so its token-budget audit metadata remains traceable.
With the optional local semantic backend installed, semantic and keyword/graph
candidates are blended before that synthesis, so a broad lexical match does not hide a
relevant meaning-based candidate. This stays within the normal candidate budget and
does not call an LLM for retrieval.

`describe_persistence` also exposes bounded, source-proven migration facts from SQL
files (including Prisma migrations) and Liquibase XML change sets in conventional
migration/changelog directories, plus Flyway-style filenames. It reports only literal
table, column and index operations; drop table/column is marked as destructive.
Parameterized Liquibase values and YAML changelogs are intentionally not inferred.
These facts never claim that a migration has run or is safe for deployed data.

`plan_change` is the stable entrypoint for the planning workflow. Its initial version
returns the evidence-backed affected surface, explicit unknowns and a response budget;
it emits a blocking decision only when an indexed primary event producer has known
consumers, and never guesses code-level change units from service-level matches. Each
response has a durable plan ID with audit metadata; it links to an existing surface
run when available and does not duplicate the task text. `refine_change_plan` records
one declared option for every pending decision without repeating retrieval or calling
an LLM, then returns source-bounded contract units for the selected event strategy; a
finalized decision is immutable within that plan.
`describe_change_unit` exposes one accepted unit with its validation and the smallest
producer/consumer contract queries needed before editing.
When a primary service has a source-proven internal HTTP call whose remote method and
route are indexed, `plan_change` also creates a contract-review unit for that boundary.
It also adds an error-mapping review only for a high-confidence, source-proven local
degradation of a known client/domain error into HTTP 5xx. This remains a review because
middleware, gateways and external contracts can affect the runtime response.
When an affected SQL table has an exactly matching indexed migration fact,
`plan_change` adds a schema-review unit. A destructive operation requires deployment,
backup and rollback validation; it never asserts that the migration needs to run.
When a primary service has a source-proven feature-flag read, `plan_change` adds one
review unit per provider/key, grouping the read locations. It asks for confirmation of
targeting, default behavior and rollout only when the guarded behavior changes; it
never infers that the flag itself must be modified.
When a primary service has an exact source-proven environment-key match between code
and an attributed Kubernetes workload, `plan_change` adds one configuration review
unit per key. It verifies compatibility and rollout references without assuming a
ConfigMap, Secret or deployed value must change.
When an indexed Kubernetes reference has a source-proven missing key in its single
local declaration, `plan_change` adds a separate configuration review. It requires
confirmation before rollout and does not choose whether the source or workload must
change.
When a primary service references a Kubernetes source that was not declared locally,
`plan_change` adds a low-confidence ownership review. It asks which repository,
chart, controller or delivery process owns that source; it never assumes the source
must be created in the indexed repository.
The same ownership review is created once per unresolved `envFrom` source for a
primary service, grouping its references even when prefixes differ. It does not
infer which keys the imported source supplies. For an optional source, it validates
safe behavior when absent rather than requiring its keys before rollout. When indexed,
the change target also names the affected Kubernetes workloads and containers.
Its `describe_change_unit` reading purpose repeats that proven scope, without adding
another retrieval or model call, with up to two file/line locations per workload.
The structured workload target uses the same bound and flags `evidence_truncated`
when the complete unit-level evidence should be consulted.
For a truncated scope, `evidence_total` makes that remaining evidence volume explicit.
When that flag is present, `describe_change_unit` returns an `evidence_follow_up`
query for the complete runtime-configuration context, scoped to the affected
workloads and containers.
For up to five known evidence locations it recommends reading the complete unit evidence
first; otherwise (or for legacy plans without a count) it recommends the runtime
configuration query.
For that query path, the follow-up supplies an initial pagination guide and asks
the agent to continue at `next_offset` only when `source_import_truncated` is
returned, stopping as soon as all selected workloads are found.
After each runtime-configuration page, `validate_runtime_configuration_follow_up`
compares its returned workload identities with the persisted target and reports
whether another page is useful.
It accepts either direct workload identities or the `source_imports` entries returned
by `describe_runtime_configuration`; contradictory direct and nested identities are
rejected rather than continuing from ambiguous context.
When another page is needed, the response includes `page_fingerprint`. Send it as
`previous_page_fingerprint` while validating the next page: a repeated page becomes
`stalled` instead of producing another query. It is the complete SHA-256 digest in
URL-safe Base64 (43 characters without padding), reducing the continuation payload.
The default progress response returns `matched_workload_count` and keeps only the
decision-relevant `missing_workloads` list. Pass `include_matched_workloads=true`
only when an audit needs the detailed matched scopes.
The default response uses a short deterministic `trace_id` instead of repeating the
plan and change-unit identifiers. Set `include_trace_details=true` to return both
original identifiers when audit or external correlation requires them.
`describe_runtime_configuration` accepts optional exact workload scopes. Runtime
follow-ups use this filter for their remaining workloads, so later pages do not repeat
already verified workload references. It accepts at most 500 identities; duplicates
do not change the result, which remains in stable indexed order rather than request order.
Use `binding_workloads` and `source_import_workloads` when the two lists need different
scopes; they cannot be combined with the general `workloads` filter.
For imports, `source_import_declaration_statuses` filters `not_declared_locally` or
`not_reported` findings, while `source_import_availabilities` filters `optional`,
`required`, or `unknown`. `not_reported` means no matching indexed finding, not proof
that a declaration exists.
`binding_declaration_statuses` filters `key_not_declared`, `not_declared_locally`,
or `not_reported`; again, `not_reported` denotes no matching indexed finding rather
than confirmation of a declaration.
`binding_source_kinds` and `source_import_source_kinds` independently filter literal
`config_map` or `secret` sources for each list.
`source_import_container_roles` filters imports by indexed `application`,
`initialization`, or `unknown` role. Bindings are not role-filterable because that
fact is not indexed for them.
`source_import_prefixes` filters exact non-empty `envFrom` prefixes; set
`source_import_include_unprefixed=true` to include only (or also include) imports
without a prefix. It does not infer individual imported environment keys.
`binding_evidence_files` and `source_import_evidence_files` filter each list by exact
persisted evidence path; they do not read or glob repository files.
`binding_evidence_ranges` and `source_import_evidence_ranges` accept exact file and
positive line intervals, selecting indexed evidence that overlaps the requested range.
They do not read source files.
When any filter is active, `filter_summary` reports only indexed and selected totals
for each filtered surface, so an agent can verify context reduction without receiving
the filter values a second time.
If a filtered surface has no selected records, `filter_conflict_guidance` reports the
indexed total selected by each active filter dimension independently. It distinguishes
an absent indexed fact from an incompatible filter intersection without returning
additional records or invoking an LLM.
Each surface accepts at most four active filter dimensions. Split an overly specific
query instead of interpreting an empty intersection as an architectural fact. The
structured `split_guidance.query_groups` response lists the filter dimensions to use
in each follow-up query. Its zero-based `execution_order` is a deterministic heuristic
that first prioritizes smaller `query_group_selected_totals` from the index, then
evidence-local dimensions on ties. It returns only counts, not additional records.
Pass that page's `current_offset`; when another page is useful, the response returns
the next `describe_runtime_configuration` call ready to execute. Pass the matching
`current_limit` as well so the next offset remains consistent; the evaluated offset
must be a multiple of that limit.
After implementation, `assess_working_change` compares a ready plan with a Git diff
from a supplied base commit, including local tracked and untracked files. It is
advisory: it reports evidence-backed units not touched, files outside the planned
service surface, and validation still required; it labels units without source evidence
as unassessable rather than guessing coverage.

All MCP responses are structured JSON and use progressive disclosure. The detailed
tool contract, pagination, response examples and ambiguity rules are in
[INTERFACE.md](INTERFACE.md).

Node JavaScript/TypeScript REST indexing currently recognizes literal Express app/router routes
that point to a named function/arrow handler in the same file or to an inline handler.
The same applies to literal `app.route(path).method(handler)` chains. Inline routes
receive a stable route-derived symbol. Router routes require exactly one direct literal
mount, and inherit that prefix. Dynamic registration, wrappers, unmounted/
multiply-mounted routers and unresolved handlers are intentionally left out rather than
guessed into an endpoint.
The deterministic subset scans `.js`, `.jsx`, `.ts` and `.tsx` sources under a
detected Node service; dynamic registration and unsupported syntax remain unknown.
For a literal Express route, `describe_entrypoint` also returns
`contract.route_middlewares` when middleware is registered as a direct identifier
before the handler. This association belongs to the exact route, even when handlers
are reused. It records registration only: factories, inline/computed middleware and
whether a middleware performs authorization or validation remain unknown.
The same literal method/path and handler rules cover Fastify instances created from a
locally imported factory, including `app.route({ method, url, handler })` objects with
all three fields literal or directly named in the same file. A literal `method` array
produces one endpoint per supported verb (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`,
`HEAD` and `OPTIONS`). Dynamic fields, prefixes and plugin registration remain out of
scope.
NestJS controllers are also indexed when `@Controller` and an HTTP method decorator
from `@nestjs/common` use literal paths; the combined route is linked to its controller
method. `describe_entrypoint` includes `contract.route_guards` for direct identifiers
registered through Nest's imported `@UseGuards`, preserving whether registration is at
controller or handler scope. This is registration context only: factories, expressions,
global guards and guard policy remain unknown. Dynamic decorator arguments remain
explicit unknowns. It also reports `contract.request` for exactly one imported
`@Body()` parameter with a direct nominal DTO type. DTO fields, validation pipes,
property reads such as `@Body("field")`, generics and multiple body parameters remain
unknown rather than being expanded or selected arbitrarily. `contract.parameters`
also includes direct `@Param("name")`, `@Query("name")` and `@Headers("name")`
bindings with a literal name and simple TypeScript type. Aggregate bindings, dynamic
names and composite types remain unknown. `contract.validation_pipes` similarly
records direct identifiers registered through imported `@UsePipes`, with controller
or handler scope. Pipe instances, factories, options and the validation rules they
enforce remain unknown.

For Java/Kotlin Spring flows, `describe_entrypoint` also returns source-proven error
contracts (raised or explicitly mapped exception types and explicit local timeout
fallbacks). Timeout fallbacks require a typed `catch`, `onErrorResume` or
`onErrorReturn`; generic callbacks are not guessed. Error messages and stack traces
are never indexed. Dynamic global handlers, proxies and gateways remain explicit
unknowns rather than inferred behavior.

When a conventional `openapi`/`swagger` JSON or YAML file declares the exact method
and path of an AST-proven HTTP entrypoint, its `contract.formal_contract` adds the
operation ID, declared response statuses, request-body requirement, security state
and file/line evidence. Server prefixes, parameter-name variations and endpoints
documented without a matching implementation remain unlinked. Referenced schemas are
not expanded; a referenced request body is reported as unknown rather than assumed.
Conflicting operations from more than one specification are also left unlinked.

Literal Protobuf service RPC signatures are exposed as `grpc` entrypoints, with
package, request/response types, streaming flags, imports and source evidence. They
describe the declared wire contract only: OrbitKB does not infer a server handler,
client or runtime registration from a `.proto` file. Duplicate service/RPC declarations
are omitted rather than arbitrarily selecting one.

`describe_configuration` lists literal environment-variable keys read by local
Node/TypeScript, Java/Kotlin and Go symbols, plus Java/Kotlin `System.getProperty`
keys and a single literal Spring `@Value("${key}")` placeholder on a Java field or
constructor parameter, or Kotlin primary-constructor parameter. It returns only key
name, kind, sensitivity indicator and file/line evidence: values, `.env` files,
SpEL, composed or multiple Spring placeholders, dynamic key names and runtime
resolution are never indexed or returned. A literal Spring
`@ConfigurationProperties` prefix also yields canonical keys for direct Java fields
and Kotlin primary-constructor parameters; dynamic prefixes and other binding styles
remain unknown.
When an indexed code-read environment key exactly matches an attributed Kubernetes
`env` reference, the binding includes up to three compact runtime references. Their
absence is unknown, not evidence that deployment configuration is missing.

`describe_runtime_configuration` lists literal `env` references in indexed plain
Kubernetes workload manifests. It identifies the environment variable, ConfigMap or
Secret name/key, workload/container and source evidence, without exposing values.
Literal `envFrom` imports are listed separately with source, optional prefix, workload,
container role (`application` or `initialization`) and evidence, but their individual
environment keys remain `unknown`; dynamic names and unrendered Helm templates remain
unknown.
For a literal `optional` field, the import also states whether its source is
`optional` or `required`; an omitted field is `required` by the Kubernetes default.
When an `envFrom` source has no matching local declaration, it is labeled
`not_declared_locally` as an external-ownership hypothesis, not a missing-source
deployment failure.
When exactly one local source declaration exists and it does not list a referenced
key, the response marks that source-proven mismatch and returns its declaration
evidence. Sources absent from the indexed repository remain unknown.
Those absent sources are labeled `not_declared_locally`: they may be managed by a
different repository, chart, controller or deployment process.
`find_architecture_smells` reports a source-proven key mismatch as
`possible_kubernetes_configuration_key_not_declared`. It separately reports an
absent local source as the low-confidence, informational
`possible_kubernetes_configuration_source_not_declared_locally`; both require
confirmation because transforms and runtime mutation may change the deployment.
An unresolved `envFrom` source is also reported separately as the informational,
low-confidence `possible_kubernetes_configuration_source_import_not_declared_locally`.
It groups repeated imports of the same source per service, retaining evidence and
prefixes while imported environment keys remain unknown. Its availability is
`optional`, `required`, `mixed` or `unknown`; optional sources prompt a behavioral
absence review instead of an assertion that rollout must provision the source. When
an initialization container imports the source, the review also checks that it
completes before application containers start. When both roles import it, the detail
orders them by lifecycle: `initialization`, then `application`. It includes the
proven workload/container scope and each scope's known prefixes when available;
it also keeps that scope's source file/line evidence. Legacy snapshots omit this
context rather than guessing it.

`describe_feature_flags` lists literal feature-flag reads through a locally proven
SDK, currently LaunchDarkly's Node server SDK. It returns the key, provider and
symbol/file evidence only: flag values, targeting, rollout state, dynamic keys and
runtime evaluation are never indexed or returned.

Explicit Spring mappings for known timeout exception types retain their literal HTTP
status, including 500, 503 and 504, so agents can distinguish an internal-error
translation from an unavailable or gateway-timeout contract.

For Java/Kotlin Spring, the same bounded response includes `service_calls` when an
entrypoint reaches a Feign client with a literal service name and HTTP route, or an
injected `RestTemplate` or `WebClient` with a literal single-label service host. Feign
calls include a literal interface `@RequestMapping` prefix when present; WebClient
requires an explicit verb or literal `.method(HttpMethod.X)` followed by
`.uri("http://service/path")`; `RestTemplate.exchange` likewise requires a literal
`HttpMethod.X`. This gives an agent the target service and endpoint without scanning
source; placeholder configuration, dynamic URLs, IPs, localhost and external domains
remain unknown.

The same response includes `resilience_policies` for source-proven limits on its
reachable Spring symbols: `@Retryable(maxAttempts = N)`, Reactor
`.retry(N)`, and Reactor `.timeout(Duration.ofMillis|Seconds|Minutes(N))` on
an injected `WebClient` chain. These are declared limits, not proof that a call
will be retried or time out at runtime. Dynamic values, property-backed policies,
`retryWhen`, and client configuration outside the method remain unknown.

Each returned static service call also carries `resolved_target`: it links to the
indexed remote endpoint when the target is unique (or uniquely belongs to the caller's
repository), reports an indexed service when only the route is absent, and reports
repository candidates when the name is ambiguous. OrbitKB never picks among duplicate
service names automatically.

When an indexed internal HTTP dependency exposes a source-proven 4xx error, OrbitKB
can also flag that the caller has no same-type client-error mapping indexed. This is a
review signal, not proof that the caller returns HTTP 500. When a literal static route
also resolves to an indexed downstream endpoint, only error contracts reachable from
that endpoint's static flow are considered.

`describe_error_flow` is the narrow follow-up for an HTTP entrypoint. It returns a
cross-service flow only when a reachable literal client call, a reachable downstream
HTTP error contract and a reachable caller mapping share an exact declared error type
or public code. This makes a proven `409 → 409` distinct from `409 → 500`; an absent
or ambiguous link is returned as an unknown, never assumed to be a `500`. Error
messages, payloads, causes and stack traces are not indexed or returned.

For Node REST, OrbitKB indexes only an explicit 4xx/5xx reply through a conventional
second handler parameter (`res`, `response` or `reply`): Express
`status(...).json/send/end` or `sendStatus(...)`, and Fastify `code/status(...).send`.
It does not infer error semantics from `throw`, promise rejection, global middleware,
dynamic statuses or the response payload.

For Go HTTP, OrbitKB indexes only literal 4xx/5xx `http.Error` and
`http.ResponseWriter.WriteHeader` replies when the local source imports `net/http`
and the function declares an `http.ResponseWriter` parameter. Dynamic statuses,
custom writers, returned `error` values, `recover` and response bodies remain unknown.

For GraphQL, OrbitKB indexes only `throw new GraphQLError` in a local resolver when
the constructor is explicitly imported from `graphql` and `extensions.code` is a safe
literal. The code is exposed as a public GraphQL code, not an HTTP status; generic
throws, dynamic extensions, formatters and error messages remain unknown.

`find_architecture_smells` also flags a source-proven internal HTTP call with no
literal timeout or retry policy on the same source symbol. This is a prompt to review
the client boundary, not evidence that production has no protection: defaults and
client configuration may live outside indexed source. Retrying non-idempotent work is
not recommended without an idempotency guarantee.

When a source-proven retry policy and a source-proven `POST` or `PATCH` call occur in
the same Spring symbol, OrbitKB also emits a retry/idempotency review signal. It does
not assume the request is unsafe: an idempotency key or server-side deduplication may
exist outside indexed source. It instead directs the agent to validate repeat safety
before preserving or expanding retries.

OrbitKB also correlates retry declarations with source-proven downstream 4xx
contracts. When the target route is indexed, the signal is limited to errors reachable
from that endpoint; otherwise it uses the target service's contracts with lower
confidence. It does not claim a retry predicate accepts the 4xx: rate limits and other
documented transient client responses may be legitimate exceptions.

For a literal timeout on a source-proven internal HTTP call, OrbitKB can also flag the
absence of a typed local timeout fallback in that same symbol. This is deliberately
narrow: controller/gateway handlers and client-factory behavior may still provide a
valid response. The signal asks an agent to verify a safe fallback or controlled error
translation close to the client boundary.

When an HTTP endpoint explicitly catches a timeout from an internal call and returns
`ResponseEntity.ok(...)`, OrbitKB also flags the potential for a masked failure. This
is intentionally narrower than all fallback values: cached or partial success can be
valid, and the indexed source cannot prove whether a degradation signal reaches the
client.

An explicit Spring mapping of a known timeout exception to HTTP 500 is also surfaced
for review. OrbitKB does not flag the same mapping to 503 or 504; those retain explicit
unavailability or gateway-timeout semantics. A 500 mapping may still be intentional,
so the result asks for contract validation rather than prescribing a status change.

OrbitKB also correlates a broad handler that maps `Exception`, `Throwable`, `Error` or
`RuntimeException` to HTTP 500 with a timeout-protected internal HTTP call in the same
service. It does not claim that the handler captures that timeout; it highlights a
review point for adding or documenting a more specific timeout contract before the
generic fallback.

When the same source symbol has a local write, an internal HTTP call and a literal
timeout or retry policy, OrbitKB flags a possible partial-write flow. It does not infer
operation order or claim that recovery is absent: transactions, outboxes, idempotency
keys and compensation may be implemented outside the indexed facts. The signal focuses
the review on treating the write and remote call as one failure boundary.

OrbitKB separately flags a literal retry on a symbol that both writes local state and
publishes an event. It is a focused outbox and duplicate-delivery review: source facts
do not establish ordering, atomicity or whether producer/consumer deduplication is
already in place.

When that retrying write/publication flow has consumers in other indexed services on
the exact same literal channel, OrbitKB prioritizes it as a cross-service duplicate
delivery review. It does not infer exchanges, bindings, routing, delivery or runtime
processing from the channel match alone.

OrbitKB raises the priority further when that known consumer has a matching message
entrypoint and source-proven local write. This identifies a possible cross-service
duplicate-state boundary, while still leaving delivery, ordering, outbox and consumer
deduplication as explicit unknowns.

For that same boundary, OrbitKB can narrow the review to a RabbitMQ consumer with no
source-proven retry boundary, retry delay or dead-letter route. This is deliberately
an absence-of-proof signal: broker policy, idempotency and de-duplication can exist
outside indexed source, and consumers on other brokers are not treated as lacking
recovery. A local idempotency marker in that consumer's handler is shown to the agent
as declared context, but it does not hide the signal or claim that duplicate handling
has been verified. The same handler-scoped posture applies to timeout context, so one
consumer's marker does not describe another consumer in the same service.

The same transaction/outbox review now covers delegated service methods that write and
publish without a source-proven transaction boundary. Entrypoints remain covered by the
existing direct-flow signal, so OrbitKB does not duplicate that finding. As elsewhere,
an outbox or compensation outside the indexed facts remains an explicit unknown.

When `find_change_surface` identifies a primary Java/Kotlin Spring service, up to
three unambiguous static HTTP targets can be added as `secondary` findings. They are
marked `origin: static_dependency` with confidence `0.6`: the dependency is proven,
but whether the requested change crosses that client boundary remains a review item.

## What OrbitKB knows

OrbitKB keeps three kinds of information separate:

| Kind | Source | How to interpret it |
| --- | --- | --- |
| Static fact | Local source analysis | Deterministic structure with file and line evidence. |
| Semantic description | Bounded generation from redacted evidence | Useful interpretation, not a source fact. |
| Change inference | Task-specific analysis | A hypothesis with reason, confidence, evidence and unknowns. |

Unknown or ambiguous data stays explicit. OrbitKB does not choose an implementation
candidate silently, turn missing configuration into proof, or present an architecture
smell as a production defect.

### Source coverage

The supported deterministic subset is intentionally focused:

| Area | Current coverage | Boundary |
| --- | --- | --- |
| Go | HTTP handlers, calls, GORM and `database/sql`, RabbitMQ, Kafka (`segmentio/kafka-go`) | Dynamic routing and types remain unknown. |
| Java and Kotlin with Spring | HTTP, injection, repositories, JDBC, Mongo, RabbitMQ, Kafka (`KafkaTemplate`/`@KafkaListener`), scheduled jobs; Feign and injected `RestTemplate`/`WebClient` calls with literal internal service/route mappings; explicit method-level retry/timeout limits | Only unambiguous local wiring and literal limits are resolved; dynamic URLs, policies and values, IPs, localhost and external domains are not inferred. |
| Node and TypeScript | Express literal routes and direct route middleware registration, Fastify routes, NestJS controllers, direct `@UseGuards`/`@UsePipes` registrations, direct `@Body()` DTO types and literal path/query/header bindings, GraphQL, Mongoose, Prisma, RabbitMQ, Kafka (`kafkajs`) | Dynamic imports and runtime composition remain unknown. |
| GraphQL | Operations, local schema contracts, input/output shapes | Remote composition, directives and federation behavior are not inferred. |
| Persistence and messaging | Postgres/Mongo evidence, RabbitMQ bindings and contracts, Kafka producer/consumer contracts | Only literal, source-proven configuration is exposed; Kafka consumer detection is Go/JVM/Node only, no Python. |
| Cloud/infra | AWS (SQS, SNS, S3, EventBridge, Kinesis), Azure (Blob Storage, Service Bus, Event Hub) and GCP (Pub/Sub) call sites (Go, Java, Kotlin, Node/TS; Python is AWS-only via `boto3`), plus Terraform/CloudFormation/plain Kubernetes declarations, parsed with real grammars (`python-hcl2`, `cfn-flip`) — never keyword matching. A cloud call also produces a `FlowEdge`, visible in `trace_flow`/`describe_entrypoint`, for every language except Python. | GCS, Dockerfile, and unrendered Helm templates are not resolved; Azure Service Bus code facts can't distinguish queue from topic (defaults to queue — see `describe_cloud_dependencies`). |
| Runtime evidence | Normalized OTel or broker edges | Experimental; payloads, trace IDs and attributes are rejected. |

An optional external depth provider can enrich a selected flow when native resolution
is insufficient. It is bounded by timeout, edge budget, validation, cache and a local
circuit breaker; it never injects an opaque whole-repository graph.

## Cumulative knowledge across repositories

One database can accumulate independent repositories or a monorepo:

```bash
orbitkb index /work/checkout --repository-name checkout --backend codex
orbitkb index /work/payments --repository-name payments --backend codex
orbitkb list
```

A service identity is `(repository, name)`. When names collide, agents must pass the
repository to service-scoped tools. This avoids silently mixing two services named
`orders` from different repositories.

Re-indexing is authoritative for detected service boundaries. Removed services are
removed from the knowledge base; moved services keep their identity by name, while a
renamed service at the same root keeps its identity by root path. Repository removal
is explicit:

```bash
orbitkb remove --repository retired-service
```

## Safety and data handling

- Source and configuration evidence is redacted before generation, storage, logs and
  embeddings.
- Security findings retain locations and remediation, never secret values.
- Context-budget telemetry stores only metadata such as service IDs, counts, response
  size and a token estimate; it never stores task text, prompts, source or cards.
- Telemetry failures are non-blocking.
- Runtime ingestion accepts only normalized edge counters and rejects payloads and
  tracing attributes.

Feedback closes the loop without retaining sensitive text:

```bash
orbitkb context-feedback <run-id> insufficient --missing-services fraud-service
orbitkb context-metrics
orbitkb context-verify <run-id> --repository shop --since <commit>
```

The compact-context cap remains 1–5 service cards until historical evidence supports
a different policy.

## Operations

The default database is `~/.orbitkb/orbitkb.db`. Pass `--db <path>` to use another
knowledge base.

Back up before destructive maintenance:

```bash
orbitkb backup --out /safe/orbitkb-backup.db --db /path/orbitkb.db
orbitkb restore /safe/orbitkb-backup.db --db /path/orbitkb.db
```

SQLite backup and restore use its consistent backup API. Indexing is serialized per
`(repository, service)`; a concurrent attempt fails fast and a lock from a terminated
local process is recovered on the next attempt.

> [!WARNING]
> Shared multi-host SQLite is unsupported. Run one host/process domain per database,
> or use storage and coordination that provide distributed locking.

### Production readiness

OrbitKB is suitable for controlled production use when its support boundaries are
accepted and the target environment is validated. It is not an unconditional
production approval out of the box.

```bash
make readiness-audit
```

The audit verifies deterministic static and change-surface candidate corpora, then
returns `"status": "conditional"`. The remaining conditions are intentional:

- Run the opt-in container E2E in the target Docker or CI environment.
- Validate change-surface predictions against actual Git changes in representative
  repositories.
- Use the supported SQLite deployment model.
- Profile representative repositories before adding an AST cache or parallel index
  traversal.

The audit output contains only results and conditions; it does not include source,
prompts or secrets.

## Development and verification

From a source checkout:

```bash
pip install -e ".[dev]"
make verify
```

Useful focused checks:

```bash
make evaluate-static
make evaluate-change-surface
make benchmark-scale
make integration-containers
make readiness-audit
```

`make integration-containers` starts ephemeral RabbitMQ, Postgres, MongoDB and
LocalStack containers, runs native operations against each (a real SQS
create-queue/send-message/receive-message round-trip for LocalStack), and checks
representative static contracts. It is opt-in locally and runs in CI. It is an
infrastructure smoke E2E, not a benchmark of a user's application or driver
compatibility.

`make benchmark-scale` reports median analysis time and peak traced Python memory for
generated Go handler corpora. For a comparable local baseline, change its inputs:

```bash
make benchmark-scale SCALE_FILES="5000" SCALE_REPEAT=5
```

Incremental indexing skips unchanged LLM-derived units by file hash. Static analysis
also stores a versioned digest of every local artifact it reads (stack source files,
OpenAPI, Protobuf and supported migrations) and skips AST parsing, flow replacement
and reconstruction when that digest is unchanged. It still reads those inputs to
calculate the digest. `--force`, external depth enrichment, or a source change during
analysis bypasses or withholds snapshot reuse, preserving correctness over speed.

Architecture rules have fact-mutation tests for cycles, fan-out, shared storage,
read-entrypoint side effects, RabbitMQ recovery-policy hypotheses, cloud
dependencies undeclared in IaC (or declared but unreferenced in code), and cloud
security/misconfiguration smells (missing dead-letter queue, public object
storage, missing encryption or bucket versioning). Static and change-surface
evaluations are deterministic regression checks; they do not claim to measure an
LLM's judgment on arbitrary codebases.

## Further reading

- [MCP interface contract](INTERFACE.md)
