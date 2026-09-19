# verify/sample_project — the "ShopFlow" fixture

This is ImpactMesh's own test fixture, not a product you run. It is a small,
intentionally realistic e-commerce checkout system ("ShopFlow") made of three
microservices, each built with a **different architecture style**, a
**different database**, and at least one **deliberate, documented
architecture smell** — so that indexing this fixture exercises far more of
ImpactMesh's discovery/generation/analysis surface than a handful of one-file
toy services ever could.

Every automated `pytest` assertion that depends on this fixture's exact shape
(endpoint counts, persistence names, messaging directions, component
groupings — see `tests/test_discovery.py`, `tests/test_orchestrator.py`,
`tests/test_self_index_e2e.py`, `tests/test_cli.py`) already runs in CI with a
fake LLM backend and needs no agent, no real `claude`/`codex` CLI, and no
external services (Postgres/MongoDB/Cassandra/Kafka are never actually
started — they only need to *exist in the code* for the regex-based
discovery heuristics to find, and are irrelevant to whether `pytest` passes).
That part of the story is fully deterministic and CI-safe by construction;
nothing here changes that.

**What this README is for** is the other half: when someone runs the real
pipeline against this fixture with a real LLM backend —
```bash
impactmesh index verify/sample_project --backend claude --db verify/sample_project.db
```
— the result is not something you can byte-diff against a golden file (a
different model, prompt revision, or temperature will phrase things
differently every time). What *can* be checked is whether the resulting
knowledge — service descriptions, dependency graph, persistence/messaging
facts, and `find_architecture_smells` findings — is **consistent with the
system this README describes**. If you're an agent asked to "run the e2e
tests and check the result," that's the comparison to make: not "does the
output match X literally," but "does the output correctly reflect the facts
below" — the sync/async call graph, which service owns which database, and
whether the three intentional smells got surfaced. A run that fails to notice
the fat `PaymentService` class, or that claims inventory-service makes
outbound HTTP calls it doesn't make, is a real finding about the pipeline,
not noise.

## The three services

| Service | Language / framework | Architecture style | Database | Role in the call graph |
|---|---|---|---|---|
| `orders-service` | Python 3.11 / FastAPI | Hexagonal (ports & adapters) | PostgreSQL | Orchestrator: the only service that calls the other two synchronously |
| `payments-service` | Node.js / Express (plain JS) | Layered, with an intentional "fat service" smell | MongoDB | Sync leaf for `/charge`; also calls two external vendors |
| `inventory-service` | Java 17 / Spring Boot 3 | Clean Architecture | Cassandra | Pure leaf: never makes an outbound call, only reacts to/emits events |

Each service has its own multi-layer directory tree (domain/application/
adapters for orders-service; routes/services/models/clients/events for
payments-service; domain/usecase/infrastructure/legacy for inventory-service)
— see each service's own source for the exact file layout. None of the three
is a single file.

## Integration: synchronous checkout, asynchronous saga compensation

The checkout flow is intentionally a mix of synchronous request/response and
asynchronous choreography (a saga with compensating actions), so a single
`POST /orders` touches both integration styles:

```
Client
  │  POST /orders  (Bearer token)
  ▼
orders-service (Postgres)
  │
  ├─ sync HTTP  GET  http://inventory-service:8080/stock/{sku}     (availability pre-check)
  ├─ sync HTTP  POST http://payments-service:3000/charge           (charge the card)
  ├─ saves Order(status=confirmed) to Postgres
  ├─ async Kafka  publish  order.created        ───────────────────────────┐
  └─ sync HTTP  POST https://notify-hub.vendor.io/v1/send  (external)      │
                                                                            ▼
                                                          inventory-service (Cassandra)
                                                            consumes order.created
                                                            reserves stock
                                                            async Kafka publish:
                                                              stock.reserved              (success)
                                                              stock.reservation_failed     (failure)
                                                                            │
                        ┌───────────────────────────────────────────────────┘
                        ▼
        orders-service consumes stock.reservation_failed (and payment.failed, below)
        → CancelOrderUseCase → order.cancelled (async Kafka publish)
                        │
        ┌───────────────┴────────────────────┐
        ▼                                     ▼
payments-service consumes            inventory-service consumes
order.cancelled → refunds            order.cancelled → releases the reservation
  via https://card-gateway.vendor.io/v1/refund (external)
```

payments-service's `/charge` call is itself synchronous end-to-end (it calls
the external `card-gateway.vendor.io` vendor before responding), and it also
publishes `payment.completed` / `payment.failed` asynchronously afterward —
`payment.failed` is the other trigger (besides `stock.reservation_failed`)
that orders-service's saga-compensation consumer reacts to.

### Kafka topics

| Topic | Publisher | Consumer(s) |
|---|---|---|
| `order.created` | orders-service | inventory-service |
| `order.cancelled` | orders-service | payments-service, inventory-service |
| `payment.completed` | payments-service | — |
| `payment.failed` | payments-service | orders-service |
| `stock.reserved` | inventory-service | — |
| `stock.reservation_failed` | inventory-service | orders-service |

### External vendors (fictitious — never implemented, only called by URL)

- `https://card-gateway.vendor.io` — called only by payments-service (charge + refund).
- `https://notify-hub.vendor.io` — called **independently by both orders-service and
  payments-service**. This duplication is intentional (see smells below), not a
  bug to fix in the fixture.

## The three deliberate architecture smells

Each service carries exactly one clearly-flagged, findable smell — a good
indexing + `find_architecture_smells` pass should be able to characterize
each of these, not just miss them silently:

1. **Hexagonal boundary leak** —
   `orders-service/application/get_order_use_case.py`. Every other use case
   (`create_order_use_case.py`, `cancel_order_use_case.py`) receives its
   `OrderRepositoryPort` through its constructor, per the hexagonal
   dependency rule (use cases depend on abstract ports, never on concrete
   adapters). `GetOrderUseCase` is the one exception: it imports and
   instantiates `PostgresOrderRepository` directly. The file has an explicit
   `NOTE` comment marking this as a known shortcut.

2. **Fat service / God object** —
   `payments-service/services/payment.service.js`. `PaymentService`
   concentrates seven responsibilities in one class that a layered
   architecture would normally split across collaborators: input validation,
   a naive inline fraud-scoring heuristic, the external card-gateway call,
   direct MongoDB ledger writes (no repository abstraction), Kafka event
   publishing, refund orchestration, and external customer-notification
   calls. Flagged with a `TODO` comment at the top of the class.

3. **Clean Architecture dependency-rule violation** —
   `inventory-service/.../legacy/QuickStockPatchController.java`. Every
   other endpoint in this service goes through
   `domain → usecase → infrastructure/persistence` layering. This one
   ("a rushed hotfix left in prod," per its own comment) is a separate
   `@RestController` that autowires `CassandraTemplate` directly and runs a
   raw, string-concatenated CQL `UPDATE` against the `stock` table — skipping
   every use case and port. It's also, not incidentally, a CQL-injection
   smell on top of the layering violation: a static-analysis-minded pass
   over this fixture has two independent reasons to flag this one file.

4. **Cross-service duplicate integration** (not tied to one service): both
   `orders-service/adapters/notification_client.py` and
   `payments-service/clients/notify_hub.client.js` call
   `notify-hub.vendor.io` independently, with no shared client — exactly the
   shape `impactmesh.generation.architecture.find_duplicate_external_integrations`
   looks for once both services are indexed into the same run.

None of these are bugs in ImpactMesh itself — they're planted in the fixture
on purpose, each in exactly one place, so a validation pass has something
concrete and unambiguous to either catch or miss.

## Known, honest detector limitations this fixture also exercises

Not everything here is a "smell" — some of it is a real, acceptable gap in
ImpactMesh's own regex-based discovery heuristics (see
`impactmesh/discovery/*.py`), worth knowing about rather than mistaking for a
fixture bug:

- **Mongoose model names aren't captured.** `payments-service`'s two
  persistence entities (`transaction.model.js`, `ledger_entry.model.js`) are
  correctly recognized as MongoDB documents (`kind: "document"`,
  `engine_hint: "mongodb"`), but the discovery hint's `name_hint` comes back
  as `"?"` for both — the regex only recognizes the `new mongoose.Schema(...)`
  constructor call itself, not the separate `mongoose.model("Transaction",
  schema)` call that actually names the model. A real LLM backend, reading
  the surrounding file, should still resolve the real names in the final
  generated output; the discovery *hint* just can't offer them for free.
- **Cassandra engine recognition.** Before this fixture existed, none of
  ImpactMesh's three JVM/Python/Node engine-keyword maps recognized Cassandra
  at all — a service using it would have silently produced `engine: unknown`
  everywhere, despite clear evidence in the manifest. Building this fixture
  is what surfaced that gap, so it's now fixed in
  `impactmesh/discovery/jvm_stack.py`'s `_ENGINE_DRIVER_KEYWORDS` (recognizing
  `spring-boot-starter-data-cassandra`, `cassandra-driver`, `datastax`) and
  in the `cassandra` enum value now accepted by
  `impactmesh/generation/schemas/persistence.schema.json`,
  `impactmesh/db/schema.sql`'s comment, and the relevant MCP tool docstrings.
  `inventory-service`'s persistence hint should now resolve to
  `engine_hint: "cassandra"` end to end.
- **Only one Kafka publish call per file is guessable when several look
  identical.** `inventory-service/.../StockEventsPublisher.java` calls
  `kafkaTemplate.send(...)` twice (`stock.reserved` and
  `stock.reservation_failed`), but the JVM detector's publish regex is a
  single-shot, non-overlapping match anchored on the literal word
  `KafkaTemplate` — so discovery hints will reliably surface the first call
  and may miss the second. Again, a real LLM reading the whole file should
  still recover both; this is a discovery-hint limitation, not a fact the
  final knowledge base is expected to get wrong.

## What a real indexing run should conclude (qualitative, not exact-match)

If you're comparing a real `impactmesh index` + `describe_service` /
`find_architecture_smells` run against this document, look for whether it
correctly captures the *substance* below — exact wording, confidence scores,
and phrasing will differ every run and should never be compared literally:

- Three services, three different stacks (`python`, `node-ts`, `jvm-spring`),
  three different persistence engines (`postgres`, `mongodb`, `cassandra`).
- `orders-service` is the only service with outbound synchronous calls to the
  other two; `inventory-service` has none at all.
- The checkout flow is a saga: a synchronous charge+stock-check followed by
  asynchronous compensation (`payment.failed` / `stock.reservation_failed` →
  `order.cancelled` → refund + release).
- All three planted smells (§ above) are things a sufficiently careful
  read of the code — or a good LLM-backed summary of it — should be able to
  name, with the right file pointed at.
- `notify-hub.vendor.io` is called from two unrelated services independently
  — a `duplicate_external_integration` finding once both are indexed.

This document is itself the test oracle for that kind of run: it is not
asserted against by `pytest` (nothing here is machine-checked automatically),
and it isn't meant to be. It's the context a human, or an agent asked to
review a real e2e run's output, should hold the result up against.
