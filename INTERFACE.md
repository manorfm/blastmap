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
`describe_configuration`, `describe_runtime_configuration`, `describe_feature_flags`, `describe_cloud_dependencies`, `list_security_findings` and `get_relationships`. An unqualified duplicate returns
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

1. Call `plan_change(task, repository?, token_budget?)` for the stable planning
   envelope, `get_change_context(task, repository?, max_services?, epic_type?)` for a
   compact implementation briefing, or `find_change_surface(task, repository?)` when
   only impact inference is needed. All require the repository whenever the KB reports
   duplicate service identities.
2. When `plan_change` returns `needs_decision`, call
   `refine_change_plan(plan_id, decisions)` with exactly one declared option for every
   pending decision. This updates the existing plan without rerunning retrieval.
3. Call `describe_change_unit(plan_id, change_unit_id)` for one accepted work item and
   its smallest suggested follow-up context.
4. After implementation, call `assess_working_change(plan_id, repository, since_commit)`
   to compare the ready plan with a bounded Git diff. It is advisory only.
5. Call `list_services(repository?)`; use its repository field to qualify
   `describe_service` whenever the same service name exists in more than one repository.
6. Call `list_entrypoints(service)` to choose an HTTP, GraphQL, gRPC, message, CLI or
   job entrypoint. A `grpc` entrypoint sourced from Protobuf describes only its declared
   wire signature; handler and client linkage remain unknown until separately proven.
7. Call `describe_entrypoint(service, kind, method, name)` for the bounded,
   reachable deterministic flow evidence. For an exact AST-proven HTTP route, its
   `contract.formal_contract` can also expose a matching conventional OpenAPI/Swagger
   operation's ID, response statuses, request-body requirement, security state and
   file/line evidence. Referenced request bodies are `null` until a future parser can
   resolve them safely; ambiguous operations from multiple specifications are omitted.
8. Call `describe_error_flow(service, kind, method, name)` only when a selected HTTP
   flow crosses an indexed internal HTTP client boundary and its error semantics matter.
   It needs a literal call, a reachable downstream HTTP contract and a matching reachable
   caller mapping; otherwise it returns explicit unknowns rather than inventing a failure.
   Node REST mappings currently require a literal 4xx/5xx Express/Fastify reply through
   `res`, `response` or `reply`; global middleware, throws and dynamic statuses remain unknown.
   Go mappings currently require literal `net/http` `http.Error` or `WriteHeader` calls
   on a declared `http.ResponseWriter`; dynamic statuses, custom writers and returned
   errors remain unknown.
   GraphQL mappings currently require a thrown `GraphQLError` explicitly imported from
   `graphql` with a literal `extensions.code`; generic throws, dynamic extensions and
   global formatters remain unknown.
9. Use `describe_api`, `describe_persistence`, `describe_configuration`, `describe_runtime_configuration`, `describe_feature_flags`, `describe_messages`,
   `describe_cloud_dependencies` or `get_relationships` only when the selected flow
   requires them.
10. Call `list_security_findings(service)` before changing credentials,
   configuration or an external integration; it returns locations and remediation,
   never source excerpts or secret values.

Generation and MCP storage are secret-safe boundaries: source/configuration evidence
is redacted before generation, generated structured text is redacted before it is
stored or exposed, and diagnostic failure files contain only a redacted prompt plus
an error type.

`describe_configuration` returns literal environment-variable key reads associated
with locally analyzed Node/TypeScript, Java/Kotlin or Go symbols, and literal
Java/Kotlin `System.getProperty` reads. It also returns a single literal Spring
`@Value("${key}")` placeholder attached to a Java field or constructor parameter,
or a Kotlin primary-constructor parameter. A sensitive-looking key is labeled, but
its value is never indexed or returned. SpEL, composed or multiple placeholders,
dynamic keys, `.env` files and runtime/config-server resolution remain unknown.
A literal Spring `@ConfigurationProperties` prefix also yields canonical property
keys for direct Java fields and Kotlin primary-constructor parameters; dynamic prefixes
and other binding styles remain unknown.
When a code-read environment key exactly matches an indexed Kubernetes `env`
reference in the same service, the result includes up to three compact runtime
references. Absence of that match is unknown, never a missing-configuration finding.

`describe_runtime_configuration` returns literal `env` references from plain
Kubernetes workload manifests, including the variable name, ConfigMap or Secret
name/key, workload/container and file evidence. Literal `envFrom` imports are
returned separately with `key_coverage: "unknown"`: their source and optional prefix
are known, but individual environment keys are not. It never reads values; dynamic
names and unrendered Helm templates remain unknown.
Their workload may include `container_role: "application"` or `"initialization"`;
legacy snapshots omit this field rather than guessing it.
When literal, `availability` is `optional` or `required`; an omitted Kubernetes
`optional` field is `required` by default. Legacy or nonliteral snapshots leave it
unknown rather than asserting availability.
An `envFrom` source without a matching local declaration is labeled
`not_declared_locally`; it may belong to another delivery boundary and is not proof
of a missing runtime source.
When a single local source declaration omits a key referenced by that workload, the
binding reports `key_not_declared` with declaration evidence; absent or ambiguous
source declarations remain unknown.
An absent source is labeled `not_declared_locally`, which is an external-dependency
hypothesis rather than a deployment failure.
The source-proven conflict appears in `find_architecture_smells` as
`possible_kubernetes_configuration_key_not_declared`, with a rollout-oriented
remediation and explicit runtime uncertainty. An absent local source appears
separately as the informational, low-confidence
`possible_kubernetes_configuration_source_not_declared_locally`.
An unresolved `envFrom` source appears as the distinct informational,
low-confidence `possible_kubernetes_configuration_source_import_not_declared_locally`;
it is an ownership hypothesis, groups repeated imports of the same source with their
prefixes and evidence, and does not infer imported keys. Its detail reports source
availability; optional sources ask for safe absence behavior rather than asserting
the source must be provisioned. An initialization-container import also reports the
need to verify initialization completes before application containers start. When
both roles occur, `container_roles` is lifecycle-ordered: `initialization`, then
`application`. The finding also includes its proven workload/container scope when
available, including each scope's known prefixes; legacy snapshots omit it rather
than guessing. Each scope also retains its own source file/line evidence.

`describe_feature_flags` returns literal reads through a locally proven feature-flag
SDK, currently LaunchDarkly's Node server SDK. It returns key, provider and source
evidence only: values, targeting, rollout state, dynamic keys and runtime evaluation
remain unknown.

## Change plan

`plan_change` is the evidence-first planning contract. Its initial response contains
a durable `plan_id`, `status`, the bounded
`surface.primary`/`secondary`/`contracts_at_risk`, explicit `unknowns`, and a
`budget` with requested and estimated response tokens. It emits an event-compatibility
`decision_point` only when a primary producer has indexed consumers: the requester must
choose preserved compatibility or a versioned rollout before consumer changes can be
planned. When no decision blocks the plan, `change_units` may include a review unit
for a source-proven internal HTTP call only when its method, route and indexed remote
endpoint all resolve. It may also include an `error_mapping` review unit only for a
high-confidence static finding that a known local client/domain error maps to HTTP 5xx;
this is advisory, since middleware and gateways are not proved. `ready` means a
relevant indexed surface exists without a blocking decision; `needs_decision` means the
compatibility choice is required; and `insufficient_evidence` means no indexed service
matched the task. The audit record links to a surface synthesis when one exists and does
not duplicate task text. The token budget is capped at 2,200 estimated response tokens.

A `persistence` migration-review unit is created only for an affected, evidenced SQL
table whose name exactly matches a source-proven migration fact in the same primary
service. Destructive migration facts add deployment, backup and rollback validation;
the unit is advisory and does not claim that any migration must execute.

`refine_change_plan` accepts only the pending plan's declared IDs and options. It
requires one selection per decision, persists the selections, then returns `ready`
with no remaining decisions and a source-bounded producer contract unit. Preserved
compatibility produces a validation unit; a versioned rollout produces a modification
unit, both listing indexed consumers as dependencies. It performs no retrieval or
model call. Retrying the same finalized selections is safe; changing a finalized
choice is rejected, requiring a new plan and an explicit impact reassessment.

`describe_change_unit` returns one persisted change unit, its validation checklist and
the smallest producer/consumer `describe_messages` queries needed to verify an event
contract, or the client `describe_service` and remote `list_entrypoints` queries for a
resolved HTTP boundary, `describe_persistence` for a schema/migration review, or
`describe_feature_flags` for a source-proven feature-flag review, or
`describe_configuration` for a proven code-to-Kubernetes configuration binding, or
`describe_runtime_configuration` for a source-proven Kubernetes key mismatch or
unresolved source-ownership review, including an `envFrom` source. The latter is
grouped by source because its imported keys remain unknown; initialization-container
imports add an ordering validation, and its target identifies proven workloads and
containers when available. Its reading purpose repeats that scope without scanning
source, rerunning retrieval or calling a model, with up to two file/line locations
per workload. The structured workload target uses the same bound and sets
`evidence_truncated` when more evidence exists in the unit.
Truncated scopes also report `evidence_total`.
`describe_change_unit` adds `evidence_follow_up` only in that case, directing the
agent to complete runtime-configuration context and listing the affected
workloads/containers.
Its `recommended_next_step` favors the complete unit evidence for up to five known
locations, and otherwise directs a runtime-configuration query.
That query path includes an initial `pagination` guide; continue only when
`source_import_truncated` is returned, using its `next_offset` with the same
workload selection; stop when all selected workloads are found.
Call `validate_runtime_configuration_follow_up` after a page with its returned
workload identities and `source_import_truncated`; it deterministically reports
whether a further page is useful.
Its `returned_workloads` accepts direct identities or the `source_imports` entries
from `describe_runtime_configuration`. A direct identity that conflicts with a nested
`workload` is rejected rather than used for pagination.
Pass the evaluated page's `current_offset`; `needs_next_page` returns the next query
with the correct offset. Pass `current_limit` from that page so its limit and offset
remain consistent; the evaluated offset must be a multiple of that limit.

`assess_working_change` accepts a ready `plan_id`, repository and Git base commit. It
uses only the Git diff from that base (including local tracked and untracked files)
and persisted source evidence: an evidence-backed unit is covered only when its file
changed; otherwise it is reported as omitted. Units with no source evidence are
explicitly unassessable. The result also lists changed files outside services named by
the plan and every remaining validation obligation. It is an advisory, read-only check,
never an implementation gate.

## Compact change context

`get_change_context` runs one `find_change_surface` inference and composes at most
five service cards from already-indexed facts; it never reads source. The response
contains `impact` (primary/secondary findings, cross-service flow, contracts/data and
external integrations), compact `services` cards, matching `architecture_risks`,
`unknowns`, `recommended_next_queries` and a `budget` object. Each card contains only
interfaces, outbound dependencies, persistence and messages. A card may also include
up to three `static_outbound_dependencies`: source-proven Java/Kotlin static HTTP calls
whose target service resolves unambiguously, including literal method/route and the
resolved service identity. When the remote route is indexed, its symbol and evidence
are included in `resolved_target`. Ambiguous and unindexed targets are omitted to
protect the context budget and avoid inventing scope. Use its recommended detail tools
rather than treating it as a full service dump.

`find_change_surface` may append up to three `secondary` findings with
`origin: static_dependency`. These are direct, unambiguous Java/Kotlin static HTTP targets
of an LLM-selected primary service. The call fact is deterministic; the `0.6`
confidence applies only to whether the task needs to cross that boundary. A matching
flow edge has `origin: static`. No additional LLM call is made.

For an identical rendered change-surface prompt, schema and backend, OrbitKB reuses
the previously validated LLM synthesis and returns `synthesis_cache: {"hit": true}`
with zero cost for that call. Any candidate-context change produces a different digest
and requires a new synthesis. Flow, contracts, persistence, freshness, unknowns and
recommended next queries are still derived from the current KB on every response.

When the optional local semantic embedding backend is available, OrbitKB blends its
bounded semantic candidates with keyword/graph candidates before synthesis. It does
not use an LLM for this step, and the response still exposes at most the configured
candidate budget; without that optional backend, retrieval remains keyword/graph only.

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
supported only where source evidence is deterministic. Cloud/IaC facts (AWS,
Azure and GCP call sites plus Terraform/CloudFormation/Kubernetes declarations)
follow the same posture and, for Go/Java/Kotlin/Node/TypeScript, also produce a
real `FlowEdge` visible in `trace_flow`/`describe_entrypoint` — see "Cloud and IaC
dependencies" for the one language exception (Python) and the one documented
approximation (Azure Service Bus queue-vs-topic). Kafka producers/consumers
follow the same messaging pipeline RabbitMQ already uses (Go/Java/Kotlin/Node;
Python producer only). Dynamic wiring, runtime observations and external depth
enrichment remain explicitly bounded or experimental. The server returns
unknowns rather than elevating heuristics to facts; compact change context
remains capped at five cards.

For Node/TypeScript REST, a literal Express `app` or `router` route is indexed as
an HTTP entrypoint only when that receiver is locally created from an Express import,
the path is literal and the final handler is a named function or arrow handler declared
in the same file, or an inline arrow/function expression. Inline route handlers use a
stable route-derived symbol. The literal `app.route(path).method(handler)` chain uses
the same rules. A router route is an HTTP entrypoint only after one direct literal
`app.use(prefix, router)` mount, which is composed into its path; unmounted or
multiply-mounted routers, dynamic registration, framework wrappers and unresolved
handlers remain unknown rather than becoming route facts.
Fastify uses the same literal method/path and handler rules, including
`app.route({ method, url, handler })` objects whose fields are all static, but only for
an instance created from a locally imported Fastify factory. A static `method` array
becomes one endpoint per supported verb (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`,
`HEAD` and `OPTIONS`); plugin registration, dynamic object fields and dynamic prefixes
remain unknown.
NestJS recognizes a class with a literal `@Controller` prefix and a literal HTTP
method decorator imported from `@nestjs/common`. It combines those paths and exposes
the controller method as the entrypoint symbol; dynamic decorator arguments and
unrecognized decorator imports remain unknown.

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
`origin` is `static`, `codegraph` or `runtime`. The `error_contracts` list contains
source-proven error metadata for symbols reachable from the selected entrypoint:
whether a symbol raises, handles or maps an error, its category/type and any literal
transport/public code. A local timeout handler is recorded only for an explicit typed
`catch`, Reactor `onErrorResume`, or `onErrorReturn`; generic callbacks are omitted.
It never contains exception messages, response bodies or stack traces. Missing handlers
and dynamic mappings are not inferred as failures. Known timeout exception types retain
literal Spring mapping statuses such as 500, 503 and 504 rather than being collapsed
into a generic error category. The
`service_calls` list is similarly bounded to reachable symbols and contains only
source-proven Java/Kotlin Feign calls with literal target service, method and route,
or injected `RestTemplate`/`WebClient` calls with a literal single-label service host.
WebClient requires an explicit verb or literal `.method(HttpMethod.X)` followed by a
literal `.uri(...)`; `RestTemplate.exchange` likewise requires literal `HttpMethod.X`.
An interface-level literal `@RequestMapping` prefix is composed with the Feign method
route. Each call includes `resolved_target`: `endpoint_indexed` with an entrypoint link,
`service_indexed` without a matching route, `not_indexed`, or `ambiguous` with
repository candidates. A unique target in the caller's repository is preferred over
same-named external-repository candidates. Dynamic URLs and placeholder configuration
are omitted. The `resilience_policies` list is likewise bounded to reachable symbols.
It includes only literal Java/Kotlin Spring limits: `@Retryable(maxAttempts = N)`,
Reactor `.retry(N)`, and Reactor
`.timeout(Duration.ofMillis|Seconds|Minutes(N))` on an injected `WebClient` chain.
Each item reports its source, mechanism, numeric `value`, `unit`, and evidence.
It describes declared source limits, never a runtime retry or timeout guarantee;
dynamic values, property-backed policies, `retryWhen`, and external client
configuration are omitted. The optional `smells` list contains explicit hypotheses, never a
conclusive architecture classification; for example, a GraphQL mutation that directly
writes state is flagged
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

When indexed Spring facts prove it, `find_architecture_smells` may also return
`possible_overbroad_exception_handler` for an explicit mapping of `Exception`,
`Throwable`, `Error` or `RuntimeException`, and `possible_error_semantics_lost` when
the same source-proven client/domain exception is raised as a 4xx category and mapped
to HTTP 5xx in one service. Both remain hypotheses: handlers, gateways or proxies
outside the indexed source may intentionally alter the final response.

`possible_unmapped_downstream_error` is a cross-service review signal. It prefers a
source-proven Java/Kotlin static HTTP call with literal service and route mappings. When the
target endpoint is indexed, it scopes contracts to that endpoint's reachable static
flow (`scope: endpoint_flow`, confidence `0.75`); otherwise it uses target-service
contracts (`scope: service_contracts`, confidence `0.6`). A reconciled internal HTTP
call remains the fallback (confidence `0.35`). The signal requires a source-proven 4xx
contract downstream while no same-type 4xx mapping is indexed in the caller. It does
not claim the caller returns 500: the response explicitly preserves uncertainty about
client branches, translated exception types and handlers outside indexed source.

`possible_missing_http_resilience_policy` is another static HTTP review signal. It
requires a source-proven internal call and no literal timeout or retry policy on the
same source symbol. The result identifies the caller symbol and target route, but does
not claim runtime protection is absent: defaults, client factories and configuration
may be outside indexed source. Its remediation explicitly asks for retry safety or an
idempotency guarantee before adding retries.

`possible_retry_on_non_idempotent_http_call` combines a literal retry policy with a
source-proven `POST` or `PATCH` call in the same Java/Kotlin Spring symbol. It includes
the precise retry policies and both policy/call evidence. It is a repeat-safety review,
not proof that an operation lacks an idempotency key or server-side de-duplication;
those controls may be configured or implemented outside indexed source.

`possible_retry_on_downstream_client_error` combines a literal retry, an unambiguous
static HTTP target and a source-proven downstream 4xx contract. It scopes to the target
endpoint's reachable flow when its literal route is indexed (`scope: endpoint_flow`),
or falls back to target-service contracts at lower confidence. It does not claim the
runtime retry predicate accepts that response; rate limits and other documented
transient 4xx contracts can be intentional exceptions.

`possible_timeout_without_local_fallback` combines a literal timeout policy and a
source-proven internal HTTP call when the same source symbol has no typed local timeout
handler. Only a typed `catch`, Reactor `onErrorResume`, or `onErrorReturn` is evidence
of local handling. It does not claim that controllers, gateways, client factories or
global handlers do not provide a fallback outside that symbol.

`possible_timeout_fallback_masks_failure` requires an HTTP entrypoint, a source-proven
internal HTTP call and a typed timeout fallback that explicitly returns HTTP 2xx through
`ResponseEntity.ok` or `ResponseEntity.status(HttpStatus.OK)`. It does not classify
empty or domain fallback values as success. The result remains a review signal: a cached
or partial response may be legitimate, and static facts cannot prove whether clients
receive a degradation indicator.

`possible_timeout_mapped_as_internal_server_error` requires an explicit mapping of a
known timeout exception to HTTP 500. Mappings to 503 and 504 are not flagged, because
they preserve explicit unavailable or gateway-timeout semantics. The finding does not
declare 500 incorrect: a compatibility or gateway contract may intentionally require
that translation.

`possible_broad_handler_swallows_timeout` requires a broad exception mapping to HTTP
500 plus a timeout-protected source-proven internal HTTP call in the same service. It
reports the handler and the client boundary separately. This is coexistence of static
facts, not proof that the handler catches the timeout at runtime; a local translation
or a more specific mapping may run first.

`possible_resilience_policy_on_partial_write_flow` requires a source-proven local
write, internal HTTP call and literal timeout or retry policy in the same symbol. It
returns up to three write targets plus `write_count` to keep the result compact. It
does not infer call order or the absence of transactions, outboxes, idempotency keys or
compensation; those mechanisms may exist outside the indexed facts.

`possible_retry_on_write_publish_flow` requires a literal retry plus source-proven
local write and event publication in the same symbol. It includes up to three write and
publication targets with their totals. The result does not establish ordering,
atomicity, an outbox or producer/consumer de-duplication; it focuses review on their
combined retry boundary.

`possible_retry_write_publish_reaches_consumer` additionally requires a static message
consumer in another service whose `channel` exactly equals the published flow target.
It returns at most three consumers and `consumer_count` to stay compact. The match does
not infer broker exchanges, bindings, routing, delivery, ordering or runtime message
processing; it simply prioritizes a source-proven cross-service review boundary.

`possible_retry_write_publish_reaches_persistent_consumer` further requires that the
matched consumer has a `message`/`CONSUME` entrypoint for the literal channel and
source-proven local writes from that consumer symbol. It includes up to three consumer
write summaries. The result still does not prove delivery, ordering, processing, outbox
coverage or de-duplication; it prioritizes a possible cross-service duplicate-state
boundary for review.

`possible_retry_write_publish_reaches_unrecovered_persistent_consumer` narrows that
boundary to a matching RabbitMQ consumer that has no source-proven retry boundary,
retry delay or dead-letter route. It returns up to three consumer write summaries and
queues. When the consumer handler contains a local idempotency marker, its summary
also returns `idempotency: "declared"`; that marker does not suppress the finding or
prove a key, storage constraint or de-duplication implementation. The signal does not
claim that broker recovery, idempotency or de-duplication is absent: those controls can
be configured outside indexed source, and other brokers are intentionally excluded from
this absence-of-proof check.

`possible_non_atomic_service_publish` extends the transaction/outbox review to a
non-entrypoint service symbol with source-proven local write and event publication but
no source-proven transaction boundary. Entrypoint-owned operations remain covered by
`possible_non_atomic_publish`, avoiding duplicate findings. An outbox, compensation or
other delivery guarantee outside the indexed facts remains an explicit unknown.

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
`idempotency: "detected"` and `timeout: "detected"` are emitted only when that
consumer's own handler declaration contains the respective marker. They are scoped
source hints, not proof that duplicate delivery is safely prevented or that timeout
handling is correct.
For Go, these values require a literal `amqp.Table` supplied to `QueueDeclare` on a
receiver locally declared as `*amqp.Channel`.
Consumer `contract.bindings` is an optional ordered list of `{exchange,
routing_key}` relations. A relation is emitted only when local RabbitMQ declarations
prove the queue, exchange and routing key as literals; multiple bindings are kept.
For Go, this requires a literal `QueueBind` call on a receiver locally declared as
`*amqp.Channel`.

Kafka contracts use the same `transport`/`direction`/`queue`/`payload` shape
(`transport: "kafka"`), with `queue` holding the topic name and no `bindings`/
`dead_letter_routing_key`/`retry_delay_ms` (Kafka has no RabbitMQ-style exchange/
routing-key/redelivery concepts). `KafkaTemplate.send(topic, payload)` and
`send(topic, key, payload)` are both recognized; Node's `producer.send({topic,
messages})` is matched by that literal object shape, not by the `.send` method
name alone (too generic — many unrelated APIs share it).

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

`migration_facts` is a separately paginated list of literal operations from SQL files
(including Prisma migrations) and Liquibase XML `changeSet` entries under conventional
migration/changelog directories, plus Flyway `V...__...sql` names. It reports only
proven create-table, add/drop-column, drop-table and create-index operations, with
file/line evidence. Parameterized Liquibase values and YAML changelogs are not
inferred. `destructive: true` is limited to literal drop column/table operations. It
does not claim that a migration executed, succeeded, or is safe for the data currently
deployed.

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
      "confidence": "high", "attributes": {"redrive_policy": true},
      "evidence": {"file": "infra/main.tf", "start_line": 1, "end_line": 3}
    }
  ],
  "pagination": {"limit": 50, "offset": 0, "static_facts": {"total": 1, "truncated": false}, "iac_resources": {"total": 1, "truncated": false}}
}
```

`static_facts` is deterministic evidence from source code: a locally-declared SDK
client type or a named SDK import actually constructed, resolved against a
vendor-sourced operation table — never a keyword guess. Supported: AWS SQS, SNS,
S3, EventBridge and Kinesis (Go, Java, Kotlin, Node/TypeScript, Python), Azure
Blob Storage, Service Bus and Event Hub (Go, Java, Kotlin, Node/TypeScript — Go
covers Blob and Pub/Sub-style factory chains, not Service Bus/Event Hub), and GCP
Pub/Sub (Java, Go, Node/TypeScript). Python is AWS-only (`boto3`); GCS is IaC-only,
not yet wired into code detection (its real API chains two factory levels —
`bucket(name).blob(name).upload(...)` — beyond the one-level mechanism the other
factory-chained SDKs use). `target_name` is the literal resource name only when the
call site names it as a plain string; an interpolated or otherwise dynamic value is
`null`, never guessed. For every language except Python, the same call also produces
a real `FlowEdge` (`publishes`/`consumes`/`reads`/`writes`/`invokes`, from the
containing function's own symbol), so it appears in `trace_flow`/
`describe_entrypoint`'s flow, not just here.

**Azure Service Bus's one documented approximation:** its SDK client type
(`ServiceBusSenderClient`/`ServiceBusReceiverClient`) is identical for both queues
and topics — only the entity name passed at runtime distinguishes them, which isn't
statically provable — so every Service Bus code fact's `resource_type` defaults to
`'queue'`. A topic operation is mis-tagged as a queue by this default. IaC-side
detection does not have this limitation: Terraform declares
`azurerm_servicebus_queue`/`_topic` as distinct resource types.

`iac_resources` is structurally parsed Terraform (`python-hcl2`), CloudFormation
(`cfn-flip`/JSON) and plain Kubernetes manifests in the same repository — real
grammars, not regex on infrastructure text. A resource's type and logical name are
literal by construction; `physical_name` is `null` whenever the declaring attribute
is an interpolated expression. `service_id` (and therefore whether a resource shows
up in one service's `describe_cloud_dependencies` at all) is set only when the
declaring file structurally falls under exactly one indexed service's root;
otherwise it stays repository-scoped and is visible only via
`find_architecture_smells`' cloud findings, not this tool. Unrendered Helm chart
templates are detected and skipped, never mis-parsed as plain YAML. GCP (Pub/Sub,
GCS), expanded Azure (Service Bus, Event Hub, Event Grid) and AWS Kinesis are all
recognized in Terraform/CloudFormation. Dockerfile is out of scope.

`attributes` is a small, curated set of literal attributes tracked per resource
type — presence-only for ones whose value can't be resolved as a literal in
practice (`redrive_policy` is almost always a `jsonencode(...)` call), literal
value for ones where the value itself matters (`acl`, `container_access_type`).
Never a default-filled guess: an attribute absent from the declaration, or whose
value depends on an interpolated expression, is simply absent from this object.
S3 bucket encryption/versioning is the one case correlated across two Terraform
declarations rather than read from one: AWS provider v4+ split both out of
`aws_s3_bucket` into their own resources (`aws_s3_bucket_versioning`,
`aws_s3_bucket_server_side_encryption_configuration`), referencing the bucket by
`bucket = aws_s3_bucket.foo.id` instead of declaring it inline — the parser
resolves that reference and folds the result into the bucket's own `attributes`
as `versioning_configured`/`encryption_configured`. CloudFormation needs no such
correlation; it declares both inline on the bucket itself.

`find_architecture_smells` adds seven cloud-derived findings computed from the same
facts, no extra cost: `cloud_dependency_without_iac` (code names a cloud resource no
Terraform/CloudFormation in the repository declares), `cloud_iac_resource_unused`
(the inverse), `shared_cloud_resource` (two services whose code names the same
queue/topic/bucket — coupling through shared infrastructure, the cloud analog of
`shared_database`), `possible_missing_dead_letter_queue` (an SQS queue with no
source-proven redrive policy), `possible_public_object_storage` (a literal public
ACL — `severity: critical`), `possible_unencrypted_cloud_resource` and
`possible_missing_bucket_versioning` (S3/SQS only — GCS and Azure Storage encrypt
by default, so an absent declaration isn't informative there). The last four carry
the same "worth checking, not a verdict" posture as the RabbitMQ recovery-policy
finding: a missing declaration in the indexed IaC is not proof the setting is
truly absent.

Kafka is not part of `describe_cloud_dependencies` — it follows the same messaging
pipeline RabbitMQ already uses (`MessageContract`/`EntryPoint`, exposed through
`describe_messages`/`describe_entrypoint`), not `static_cloud_facts`. Supported:
`KafkaTemplate`/`@KafkaListener` (Java, Kotlin), `kafkajs` (Node/TypeScript —
producer via its object-literal `{topic, messages}` shape, consumer only when
exactly one `.subscribe({topic})` pairs unambiguously with one `.run({eachMessage})`
in the file), `segmentio/kafka-go` (Go — a `*kafka.Reader` bound to a literal topic
and read from within the same function becomes that function's own entrypoint,
since kafka-go has no RabbitMQ-style callback consumer). Python gets a producer
`FlowEdge` for free from the existing generic call classifier, no dedicated
`MessageContract`; there is no Python Kafka consumer support.

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
