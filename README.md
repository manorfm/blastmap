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
| Plan an epic | `get_change_context` | `describe_service`, `describe_entrypoint` |
| Find likely impact | `find_change_surface` | `get_relationships`, `describe_api` |
| Understand a request path or its static error mapping | `list_entrypoints` | `describe_entrypoint` |
| Inspect data or events | `describe_persistence`, `describe_messages` | `get_relationships` |
| Inspect cloud/infra dependencies | `describe_cloud_dependencies` | `find_architecture_smells` |
| Find architectural risks | `find_architecture_smells` | evidence and remediation in the finding |
| Compare runtime and static paths | `describe_runtime_divergence` | `describe_entrypoint` |

`get_change_context` returns at most five compact service cards. It includes likely
impact, flow, contracts at risk, persistence, messaging, architectural hypotheses,
unknowns and recommended next queries. It does not reread the source tree while
answering.

All MCP responses are structured JSON and use progressive disclosure. The detailed
tool contract, pagination, response examples and ambiguity rules are in
[INTERFACE.md](INTERFACE.md).

For Java/Kotlin Spring flows, `describe_entrypoint` also returns source-proven error
contracts (raised or explicitly mapped exception types and literal transport status).
Error messages and stack traces are never indexed. Dynamic global handlers, proxies
and gateways remain explicit unknowns rather than inferred behavior.

When an indexed internal HTTP dependency exposes a source-proven 4xx error, OrbitKB
can also flag that the caller has no same-type client-error mapping indexed. This is a
low-confidence review signal, not proof that the caller returns HTTP 500.

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
| Java and Kotlin with Spring | HTTP, injection, repositories, JDBC, Mongo, RabbitMQ, Kafka (`KafkaTemplate`/`@KafkaListener`), scheduled jobs; Java Feign client calls with literal service and route mappings | Only unambiguous local wiring is resolved; Feign properties, dynamic URLs and Kotlin clients are not inferred. |
| Node and TypeScript | GraphQL, Mongoose, Prisma, RabbitMQ, Kafka (`kafkajs`) | Dynamic imports and runtime composition remain unknown. |
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

Current incremental indexing skips unchanged LLM-derived units by file hash. Static
analysis still scans the service on each index/update, so profile a real repository
before adding cache or parallelism.

Architecture rules have fact-mutation tests for cycles, fan-out, shared storage,
read-entrypoint side effects, RabbitMQ recovery-policy hypotheses, cloud
dependencies undeclared in IaC (or declared but unreferenced in code), and cloud
security/misconfiguration smells (missing dead-letter queue, public object
storage, missing encryption or bucket versioning). Static and change-surface
evaluations are deterministic regression checks; they do not claim to measure an
LLM's judgment on arbitrary codebases.

## Further reading

- [MCP interface contract](INTERFACE.md)
