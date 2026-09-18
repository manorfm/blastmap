# blastmap

Task-aware change intelligence for AI coding agents: given an engineering task, what
is the smallest architectural surface an agent needs to understand before touching
code — with evidence, confidence and freshness made explicit, instead of implied.

## O que isto não é

- **Não é um code graph genérico** via AST/LSP — isso já existe (Serena,
  Codebase-Memory MCP e similares), e não é a camada que falta.
- **Não é RAG genérico** sobre o repositório, nem "memória de código".
- **Não é busca semântica com vector DB** — `search`/`find_change_surface` usam
  SQLite FTS5 (bm25); embeddings só entrariam com um benchmark mostrando que FTS5
  não basta, o que ainda não foi feito (ver "Limitações conhecidas").
- **Não tenta reescrever código nem agir sozinho** — é uma camada de conhecimento
  consultada via MCP; quem decide e edita é o agente.

## Ideia

Agentes de IA que precisam entender um sistema de microsserviços hoje só têm dois
caminhos: ler o código-fonte inteiro (caro em tokens, lento) ou depender de
documentação manual que fica desatualizada. O `blastmap` "tritura" uma ou várias
árvores de código — repositório por repositório, ou um monorepo de uma vez, de forma
cumulativa no mesmo banco — usa um LLM (Claude Code ou Codex, via CLI headless,
usando sua assinatura em vez de API paga) para sintetizar uma documentação enxuta e
**semântica** — não só estrutural — por microsserviço e por API, persiste isso em
SQLite como um **System Knowledge Model** consultável, e serve tudo a agentes via
tool calls MCP com drill-down progressivo: lista serviços → descreve um serviço →
lista APIs → detalha uma API.

O foco é a camada que falta entre "ler o código-fonte" e "perguntar a um humano", em
três frentes:

- **Por que**, não só o quê: para cada API, documentar por que ela chama outro
  serviço/fila (motivo de negócio), o que valida, quem pode chamar, e o que persiste —
  informação que hoje só existe na cabeça de quem escreveu o código.
- **Change Intelligence orientada a tarefa**: dado um épico em texto livre, apontar
  qual é a **menor superfície arquitetural** que provavelmente precisa mudar — sem o
  agente precisar explorar manualmente o sistema inteiro primeiro
  (`find_change_surface`, ver abaixo). O produto final não é "mais contexto", é
  **contexto mais relevante**: sempre que uma decisão for entre devolver mais
  informação ou informação mais relevante, a resposta certa é a segunda.
- **Saúde arquitetural do sistema como um todo**: além de "como um serviço funciona",
  responder "esse sistema tem ciclo de dependência?", "algum serviço virou um
  bottleneck?", "dois serviços estão sem querer compartilhando o mesmo banco?"
  (`find_architecture_smells`, ver abaixo) — sinais estruturais, não julgamento.

Toda resposta separa três camadas explicitamente, e nunca as confunde:

- **Fato**: estrutura extraída deterministicamente (ex.: `orders-service` chama
  `payments-service`; `orders-service` existe).
- **Interpretação semântica**: síntese do LLM a partir de evidência real (ex.:
  "`payments-service` é dono da autorização de pagamento").
- **Inferência de tarefa**: uma conclusão específica de um épico, sempre com
  `reason`, `confidence` e `evidence`, e nunca tratada como verdade absoluta (ex.:
  "adicionar Pix provavelmente exige mudar `payments-service`"). Junto disso, o
  sistema também é explícito sobre o que **não sabe** (`unknowns`) e sobre se o
  conhecimento indexado pode estar **desatualizado** (`freshness`) — ausência de
  informação nunca vira silenciosamente "não existe" ou "não é afetado".

### Geração hierárquica: endpoint → componente → serviço

A geração de cada serviço é composta de baixo pra cima, não gerada em paralelo a
partir de fragmentos isolados:

1. **Endpoint** (unidade mais fina) — detalhado a partir do seu próprio recorte de
   código, mais um salto de navegação local (funções chamadas dentro do handler,
   resolvidas no mesmo arquivo) quando existe.
2. **Componente** (classe/controller/módulo) — agrupa os endpoints que pertencem à
   mesma classe (ou ao mesmo arquivo, quando o roteamento é por função solta, como é
   comum em FastAPI/Flask) e sintetiza um resumo **a partir dos resumos de endpoint já
   gerados**, nunca relendo código bruto de novo.
3. **Serviço** (overview) — composto por último, a partir dos resumos de componente,
   não mais a partir só da árvore de pastas lida antes de qualquer endpoint ser
   analisado.

Isso resolve dois problemas do modelo anterior (achatado, tudo gerado em paralelo):
atribuição errada de chamada (um endpoint via chamadas de outro só porque estavam no
mesmo serviço) e overview superficial (escrito antes de qualquer detalhe existir).

## Uso básico

```bash
blastmap index /caminho/do/repositorio --backend claude   # ou --backend codex
blastmap index /outro/repositorio --repository-name outro-repo  # múltiplos repos no mesmo DB, cumulativo
blastmap list
blastmap status [servico]
blastmap export md --out docs/
blastmap export mermaid --out docs/   # diagrama de topologia + ER por serviço
blastmap serve --backend claude   # servidor MCP (stdio); backend usado só por find_change_surface
blastmap analyze "Adicionar suporte a Pix no checkout" --backend claude   # roda find_change_surface direto, sem sessão MCP
blastmap verify <run_id> --repository <nome> --since <commit>   # confere uma predição contra o git diff real
```

Cada subcomando é autoexplicativo via `--help` (ex. `blastmap index --help`), com um
exemplo pronto para copiar. `blastmap --help` explica o fluxo completo: **index → ask
→ verify**.

O conhecimento é cumulativo por natureza: dá pra indexar um repositório de cada vez
(`blastmap index <repo1>`, depois `blastmap index <repo2> --repository-name <repo2>`,
...) conforme eles forem ficando disponíveis, ou apontar para uma raiz de monorepo de
uma vez só — o mesmo banco SQLite acumula os dois casos sem colisão de nomes, e
`find_change_surface`/`search` sempre enxergam tudo que já foi indexado até aquele
momento, não só o último repositório indexado.

## Formato de resposta do MCP

As tool calls devolvem sempre JSON estruturado (nunca texto livre), pensado pra um
agente ir refinando o pedido sem carregar tudo de uma vez. Exemplos abaixo, gerados a
partir das fixtures de exemplo do projeto (`verify/sample_project` para as tools de
descrição; a fixture de testes "checkout/payments/Pix" para `find_change_surface`).

**`list_services()`** — visão geral, uma linha por serviço:
```json
{
  "services": [
    {"name": "orders-service", "short_desc": "A FastAPI service that handles order creation by charging a customer's payment and reserving product stock.", "stack": "python", "api_count": 1},
    {"name": "payments-service", "short_desc": "A Node.js/TypeScript backend service that processes payment charges and publishes payment-related events to Kafka.", "stack": "node-ts", "api_count": 1}
  ]
}
```

**`describe_service("orders-service")`** — descrição completa + dependências com o
motivo de negócio (`reason`, `data_needed`, `purpose_kind`) + os componentes que
compõem o serviço + APIs/persistência/mensageria só como referência (nome, sem
detalhe de campo) + `freshness`:
```json
{
  "name": "orders-service",
  "short_desc": "A FastAPI service that handles order creation by charging a customer's payment and reserving product stock.",
  "long_desc": "orders-service is a Python microservice built on FastAPI, exposing a single POST /orders endpoint that creates new orders. Requests must carry a Bearer token in the Authorization header (...) it publishes events to a Kafka topic for downstream consumers, giving it a role as both an orchestrator of a synchronous checkout flow and a producer in an event-driven architecture.",
  "stack": "python",
  "calls": [
    {"to_service_name": "payments-service", "call_kind": "http", "reason": "to charge the customer's payment method for the order amount", "data_needed": ["amount", "currency", "payment_token"], "purpose_kind": "other", "target_kind": "internal", "resource_type": "not_applicable"},
    {"to_service_name": "inventory-service", "call_kind": "http", "reason": "to check current stock for the requested SKU before confirming the order", "data_needed": ["sku", "qty"], "purpose_kind": "validation", "target_kind": "internal", "resource_type": "not_applicable"},
    {"to_service_name": "order_created", "call_kind": "queue_publish", "reason": "to notify downstream consumers that a new order was created", "data_needed": ["order_id", "sku", "qty"], "purpose_kind": "notification", "target_kind": "unknown", "resource_type": "not_applicable"}
  ],
  "apis": [
    {"method": "POST", "path": "/orders", "summary": "Creates a new order by charging the customer's payment method and checking stock availability, then publishes an order-created event."}
  ],
  "components": [
    {"name": "main", "file_path": "main.py", "summary": "Function-based FastAPI routing with a single order-creation endpoint; no class wraps it."}
  ],
  "persists": [{"name": "orders", "kind": "sql_table", "engine": "postgres"}],
  "messages": [{"direction": "publishes", "channel": "order_created", "provider": "kafka", "description": "Published after a payment charge succeeds and inventory stock is confirmed; signals that a new order has been created."}],
  "freshness": {"indexed_at": "2026-03-01T12:00:00+00:00", "source_commit": "a1b2c3d", "current_commit": "a1b2c3d", "stale": false}
}
```
`stale: true` significa que o repositório teve commits novos desde a indexação — o
agente deveria considerar reindexar antes de confiar demais no conteúdo. `stale: null`
significa que não dá pra saber (repositório não é git, ou nunca foi indexado com um
commit associado) — nunca tratado como "está tudo bem", nem como "está desatualizado".

`components` reflete a geração hierárquica descrita acima: quando o roteamento é por
função solta (sem classe), o componente é o próprio arquivo (`"name": "main"`); quando
há uma classe/controller, o componente leva o nome dela.

**`describe_api("orders-service", "POST", "/orders")`** — o nível mais detalhado:
formato do request e da resposta campo a campo (`request_shape` inclui `required`
por campo — o começo de Contract Intelligence estruturada, hoje só nos campos, sem
ainda comparar entre indexações pra detectar quebra de contrato de verdade), as
mesmas chamadas de dependência (agora só as desta API) e as regras de validação/
autorização:
```json
{
  "method": "POST",
  "path": "/orders",
  "summary": "Creates a new order by charging the customer's payment method and checking stock availability, then publishes an order-created event.",
  "description": "Accepts an order request (amount, currency, payment token, SKU, quantity), charges the customer via the payments service, checks stock availability via the inventory service, then publishes an order_created event to Kafka. Returns the newly created order's ID and confirmation status.",
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
    {"to_service_name": "inventory-service", "call_kind": "http", "reason": "to check current stock for the requested SKU before confirming the order", "data_needed": ["sku", "qty"], "purpose_kind": "validation", "target_kind": "internal", "resource_type": "not_applicable"}
  ],
  "validations": [
    {"kind": "authorization", "description": "Requires an Authorization header starting with \"Bearer \"; otherwise returns 401 with detail \"missing bearer token\"."},
    {"kind": "input_validation", "description": "Requires payload fields amount, currency, payment_token, sku, and qty (accessed directly from payload dict, so missing fields would raise an error)."}
  ]
}
```
`resource_type` só é significativo quando `target_kind` é `"external"` (ver seção
"Interno vs. externo" abaixo) — vale `"not_applicable"` nos demais casos, nunca vazio.

`describe_persistence` e `describe_messages` seguem o mesmo padrão, mas devolvem o
schema completo campo a campo (o que `describe_service` só referencia pelo nome) —
são chamados à parte de propósito, pra manter `describe_service` enxuto. Ambos também
carregam `engine`/`provider` (ver "Banco de dados e mensageria: engine e provider",
abaixo).

## System Intelligence: relacionamentos, smells e change surface

Tools adicionais vão além de "como um serviço funciona" e respondem "o que está
conectado a quê", "como A chega em B", "esse sistema tem algum problema estrutural?",
"o que essa tarefa provavelmente afeta" e "a predição de ontem se confirmou de
verdade?":

**`get_relationships("payments-service", direction="both")`** — grafo de 1 salto ao
redor de um serviço: chamadas que ele faz (outbound), chamadas que outros serviços
fazem nele (inbound — "quem depende de mim", hoje só possível via este tool), e
vínculos de fila/tópico inferidos por nome de canal compartilhado
(`MESSAGE_LINK`). Cada aresta carrega `reason`, `confidence` (quando aplicável),
`target_kind` (`internal`/`external`/`unknown` — ver seção seguinte), `provenance`
(`llm` quando veio de síntese sobre código real, `deterministic` quando é um fato
estrutural puro, como o `MESSAGE_LINK` por nome de canal) e `evidence` (arquivo/linha):
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

**`trace_flow("orders-service", "ledger-service")`** — caminho mais curto entre dois
serviços, andando por `service_calls` (outbound) e vínculos de fila (publish→consume)
— o complemento multi-salto do `get_relationships` (que só anda 1 salto por chamada).
Útil quando você sabe que dois serviços estão relacionados mas não como:
```json
{"path": [{"from": "orders-service", "to": "payments-service", "type": "HTTP", "reason": "...", "confidence": 0.9, "evidence": [...]},
          {"from": "payments-service", "to": "ledger-service", "type": "QUEUE_PUBLISH", "reason": "...", "confidence": 0.85, "evidence": []}],
 "reachable": true, "hops": 2}
```
Se não houver caminho dentro de `max_hops` (padrão 6), devolve `{"path": [], "reachable": false, "note": "..."}`.

**`find_architecture_smells()`** — achados estruturais do sistema inteiro, recomputados
a cada `index`/`update` a partir só do que já foi indexado (`service_calls`,
`persistence_entities`) — **sem chamar LLM**, puro SQL + uma passada de Tarjan
(componentes fortemente conexos) para detectar ciclo. Sempre em linguagem de risco,
nunca um veredito:
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
  ]
}
```
4 detectores hoje: `cycle` (dependência circular entre serviços internos),
`fan_in`/`fan_out` (serviço com número desproporcional de dependentes/dependências
diretas — limiar inicial de 4, não um valor treinado), `shared_database` (mesma
entidade nomeada, mesmo engine, serviços diferentes) e
`duplicate_external_integration` (dois+ serviços integrando com o mesmo vendor de
forma independente). Deliberadamente fora de escopo ainda: marcação de
legado/strangler-fig, severidade direcional de ciclo (ciclo envolvendo um serviço
legado é mais grave que entre dois pares) e tendência entre execuções sucessivas —
ver "Limitações conhecidas".

### Interno vs. externo (`target_kind`, `resource_type`)

Toda chamada (`service_calls`) carrega `target_kind`: `internal` (outro serviço deste
mesmo sistema), `external` (uma integração de terceiro/vendor) ou `unknown`. A
classificação usa dois sinais, nessa ordem de precedência:
1. **Ground truth**: se o nome resolve pra um serviço já indexado (`to_service_id`),
   é `internal`, ponto — sobrescreve qualquer palpite anterior.
2. **LLM, no momento da geração**: a API já vê o código real (imports, cliente HTTP,
   URL) e classifica com base nisso — sinal mais forte que qualquer heurística de
   nome, porque enxerga o código de verdade.
3. **Heurística determinística, só pra quem ficou `unknown`** (`discovery/integration_heuristics.py`,
   sem LLM): lista curta de vendors conhecidos (Stripe, Twilio, AWS, ...) → `external`;
   nome que segue o mesmo padrão de nomenclatura dos serviços já indexados (ex. sufixo
   `-service`) → `internal` (não mapeado ainda).

Quando `target_kind` é `external`, a chamada também carrega `resource_type`
(`queue`/`storage`/`compute`/`saas`/`db_managed`/`other`/`not_applicable`) — mesma
precedência: LLM primeiro, a mesma lista de vendors (agora também mapeada pra um
tipo de recurso) só preenche a lacuna quando o LLM devolveu `unknown`/ausente.

Essa distinção alimenta dois buckets em `find_change_surface`:
- **`external_integrations`**: integrações de terceiro alcançáveis pelos serviços
  `primary`/`secondary` — o agente pode precisar mexer nessa integração também.
- **`unmapped_internal_hint`**: dependências que parecem internas mas ainda não foram
  indexadas — sinal de "indexe mais do sistema pra ter o quadro completo". Cada uma
  também aparece, restated, em `unknowns` (ver abaixo).

### Banco de dados e mensageria: `engine` e `provider`

Um modelo/entidade ORM (SQLAlchemy, JPA, GORM) raramente revela sozinho qual banco
está por trás — isso normalmente só existe numa connection string ou no manifesto de
dependências. `persistence_entities.engine`
(`postgres`/`mysql`/`mongodb`/`dynamodb`/`redis`/`elasticsearch`/`sqlite`/`unknown`) e
`messages.provider`
(`kafka`/`rabbitmq`/`sqs`/`sns`/`service_bus`/`activemq`/`nats`/`unknown`) seguem a
mesma precedência de `target_kind`: o LLM decide a partir da evidência real, com dois
tipos de pista best-effort quando o código sozinho não basta:
- **Manifesto de dependências** (`discovery/scan_helpers.engine_hint_from_manifest`):
  `psycopg2` no `requirements.txt` sugere Postgres, `mongoose` no `package.json`
  sugere MongoDB, etc. — presença de dependência é um sinal mais barato e mais
  confiável que ler texto de código à procura do nome do driver.
- **Arquivos de configuração** (`application.properties`/`.yml`, `.env`,
  `docker-compose.yml`) — usados especialmente quando o código só mostra uma
  abstração transport-agnostic (JMS, Celery, NestJS microservices, Spring Cloud
  Stream) cujo broker concreto só existe fora do código.

Quando nada resolve, o valor fica honestamente `unknown` — nunca um chute.

**`find_change_surface("Adicionar suporte a Pix no checkout")`** — a primeira tool que
um agente deveria chamar ao receber um épico, antes de abrir qualquer arquivo. Usa
apenas o conhecimento já indexado (busca por palavra-chave sobre serviços/APIs/
relações + expansão de grafo + uma única síntese LLM) — **nunca relê código-fonte**. A
lista de serviços candidatos passada ao LLM é fechada; qualquer nome que ele inventar
fora dela é descartado antes de responder. Resultado é uma **inferência de tarefa**,
não fato:
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
  "run_id": 1
}
```
- **`contracts_at_risk`**: eventos publicados por um serviço `primary`/`secondary` e
  quem mais consome aquele canal — sempre em linguagem de risco ("potentially
  affects", "requires verification"), nunca declarando uma quebra confirmada.
- **`persistence_affected`**: o que cada serviço `primary`/`secondary` persiste,
  lido direto do índice (sem custo extra de LLM) — resolve o "e os dados, o que é
  afetado?" sem precisar de uma chamada separada a `describe_persistence` pros casos
  óbvios.
- **`unknowns`**: toda lacuna explícita da resposta — serviço não mapeado, a tarefa
  inteira não bateu com nada indexado, **ou um serviço relevante cujo `freshness`
  veio `stale`** (o que significa que os motivos de dependência guardados sobre ele
  também podem estar desatualizados) — com `status`, `reason` e `suggestion` — pra um
  agente poder ramificar em cima disso em vez de inferir "não existe" de uma lista
  vazia.
- **`freshness`**: por serviço relevante, se o conhecimento indexado pode estar
  desatualizado (commit indexado vs. commit atual do repositório). Um serviço `stale`
  também gera uma entrada correspondente em `unknowns`.
- **`recommended_next_queries`**: lista ranqueada de `{tool, arguments, reason}` — as
  próximas chamadas MCP de maior valor dado o que já se sabe (ex.: `describe_api` num
  serviço `primary` sem detalhe carregado, ou `index` numa dependência de
  `unmapped_internal_hint`), computada de graça a partir do que já foi lido — **zero
  custo extra de LLM**. É a peça de maior retorno por token de todo o change surface:
  em vez de só entregar contexto, ela diz **onde olhar em seguida**.

Aceita um `hint_services` opcional para ancorar a busca quando o agente já suspeita de
serviços específicos. Se a tarefa não bater com nada indexado, retorna listas vazias
com uma `note` (para humanos) e um `unknowns` (estruturado, para o agente) explicando
— sem chamar o LLM.

### Auditoria e feedback

Toda chamada de `find_change_surface` que efetivamente rodou o LLM é registrada
(`change_surface_runs`/`change_surface_findings`) e o `run_id` volta na resposta.
Depois de agir sobre o resultado, o agente pode fechar o loop manualmente:
```
record_change_surface_feedback(run_id=1, service="payments-service", outcome="confirmed")
```
`outcome` é `"confirmed"` (o serviço realmente precisou mudar) ou `"rejected"` (não
precisou). Chamadas futuras de `find_change_surface` para esse mesmo serviço têm a
`confidence` recalibrada com base nesse histórico — só depois de um mínimo de 3
feedbacks acumulados, e como um ajuste leve (30%) sobre o palpite fresco do LLM, nunca
substituindo o julgamento feito com a evidência da tarefa atual.

### Ground truth via git: `verify_change_surface` / `blastmap verify`

Em vez de depender só do agente lembrar de reportar o resultado, dá pra confrontar
uma predição passada contra o `git diff` real de um repositório desde um commit:
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
A versão MCP é só leitura (não grava feedback sozinha); a CLI tem um
`--record-feedback` que, opcionalmente, grava `confirmed`/`rejected` automaticamente
a partir do resultado:
```bash
blastmap verify 1 --repository my-monorepo --since a1b2c3d --record-feedback
```
Cada verificação fica salva (`change_surface_verifications`) e aparece em
`blastmap status`. É intencionalmente escopada a **um repositório por vez** —
comparar vários históricos de git não relacionados sob um único `--since` não faria
sentido; num setup cumulativo com vários repositórios, roda-se um `verify` por
repositório, do mesmo jeito que a indexação também é feita um repositório de cada vez.

## Diagramas (Mermaid)

`blastmap export mermaid --out docs/` gera:
- **`topology.mmd`** — um `graph TD` do sistema inteiro: cada serviço indexado é um
  nó, cada vendor externo alcançado é um nó arredondado, arestas de `service_calls` e
  de `MESSAGE_LINK` conectam tudo. Serviços envolvidos num ciclo
  (`find_architecture_smells`) saem destacados com um estilo próprio.
- **`<serviço>/er.mmd`** — um `erDiagram` por serviço, com as entidades persistidas e
  seus campos. Sem linhas de relação entre entidades — o índice ainda não rastreia
  chave estrangeira (ver "Limitações conhecidas") — o próprio arquivo diz isso
  explicitamente em vez de inventar uma relação.

Texto, não imagem, de propósito: um `.mmd` é versionável, aparece no diff de um PR, e
renderiza nativo no GitHub/GitLab/na maioria dos editores — uma imagem renderizada
seria uma segunda fonte de verdade que pode ficar desatualizada sem ninguém notar.
Ambos gerados 100% a partir do SQLite, sem custo de LLM.

## O que já está implementado

- **Descoberta por heurística** (regex/assinatura de arquivo, sem parser AST completo)
  para 4 stacks: Node.js/TypeScript (Express/NestJS), Python (FastAPI/Flask/Django),
  JVM (Java/Kotlin + Spring Boot) e Go. Cobre HTTP/gRPC, Kafka/RabbitMQ/SQS/SNS e
  abstrações transport-agnostic (JMS, Celery, NestJS microservices, Spring Cloud
  Stream) nos 4 stacks.
- **Geração hierárquica** (endpoint → componente → serviço, ver "Ideia" acima):
  atribuição de chamada escopada aos arquivos do próprio endpoint
  (`EndpointHint.dependency_files()`), navegação de 1 salto pra função local chamada
  dentro do handler (`extra_excerpts`, resolvida via `discovery.scan_helpers.resolve_local_calls`),
  camada de componente (`components`) sintetizada a partir dos resumos de endpoint já
  gerados, overview composto por último a partir dos resumos de componente.
- **Geração plugável**: backend `claude` ou `codex`, ambos headless via CLI, usando a
  assinatura do usuário (não API paga) por padrão. Validação do JSON retornado contra
  schema, com 1 retry e isolamento de falha por unidade (uma falha não aborta o run).
  Harness de invocação (prompt/schema loading + retry) compartilhado entre indexação e
  `find_change_surface` (`generation/llm_harness.py`).
- **SQLite como fonte da verdade** — um System Knowledge Model único, com
  `repositories` (multi-repositório explícito, cumulativo) e `services.repository_id`,
  e atualização incremental por hash de arquivo: só regenera a unidade (endpoint/
  componente/persistência/mensageria/overview) cujo arquivo mudou. A reconciliação de
  `target_kind`/`resource_type`/`to_service_id` (`reconcile_service_call_targets`) é
  escopada ao serviço recém-escrito durante a indexação — não uma varredura da tabela
  inteira a cada API — e só roda sem escopo (tabela inteira) uma vez por serviço novo,
  pra resolver chamadas de outros serviços que apontavam pra ele antes dele existir.
- **Camada de repositório dividida por agregado** (`db/repositories/`: `services`,
  `apis`, `components`, `service_calls`, `persistence`, `messages`, `architecture`,
  `indexed_files`, `change_surface`, `verification`, `index_runs`, `search`,
  `repositories`) — cada módulo só conhece suas próprias tabelas; nenhum outro módulo
  roda SQL diretamente.
- **Evidência persistida**: `apis`, `components`, `service_calls`,
  `persistence_entities` e `messages` carregam `evidence_json` (arquivo + linha) — o
  mesmo trecho que o LLM viu ao gerar aquela informação, não uma linha inventada
  depois. `service_calls` também carrega `confidence` (0-1, avaliada pelo próprio
  LLM), `target_kind` e `resource_type` (LLM com o código real como sinal primário,
  heurística determinística de vendor/nomenclatura como fallback só para o que ficou
  sem resolver). `persistence_entities` carrega `engine` e `messages` carrega
  `provider`, mesma precedência. `apis` também carrega `request_shape` estruturado
  (campo, tipo, `required`), espelhando o `response_shape` que já existia — a base de
  dados pra Contract Intelligence mais profunda (comparar contratos entre indexações
  pra achar quebra de verdade ainda não está implementado, ver "Limitações
  conhecidas").
- **Provenance e freshness explícitos**: `provenance` (`llm` vs. `deterministic`)
  formaliza a distinção fato/interpretação onde ela já era implícita; `freshness`
  (`generation/freshness.py`) compara o commit indexado com o commit atual do
  repositório sob demanda — nunca persistido, então nunca fica ele mesmo desatualizado.
- **Smells arquiteturais determinísticos** (`generation/architecture.py`,
  `find_architecture_smells`): ciclo (Tarjan/SCC), fan-in/fan-out desproporcional,
  shared database, integração externa duplicada — recomputados a cada
  `index`/`update`, sem LLM, versionados em `architecture_runs`/`architecture_findings`
  do mesmo jeito que `change_surface_runs` já é.
- **Export para Markdown** legível por humano e **Mermaid** (topologia + ER),
  ambos gerados 100% a partir do SQLite.
- **`find_change_surface` como Builder** (`generation/change_surface.ChangeSurfaceBuilder`):
  cada peça da resposta (achados, fluxo, integrações externas, contratos em risco,
  unknowns, freshness, próximas consultas recomendadas) é montada por um método
  próprio, em vez de um dict crescendo ad hoc. Retrieval de candidatos é uma estratégia
  plugável (`generation/retrieval.KeywordGraphRetrieval`).
- **`recommended_next_queries`** (`generation/next_queries.py`): próxima consulta MCP
  de maior valor dado o que já foi computado, sem custo extra de LLM.
- **`contracts_at_risk`**: consumidores de um evento publicado por um serviço
  relevante, reaproveitando o mesmo join de canal usado por `get_relationships`.
- **Ground truth via git** (`generation/verification.py`, `blastmap verify`): compara
  uma predição passada contra o `git diff` real de um repositório, calcula
  precisão/recall e pode gravar feedback automaticamente.
- **Servidor MCP** com 14 tools (uma escreve feedback; `verify_change_surface` grava um
  registro de auditoria mas não grava feedback sozinha): as 7 originais
  (`list_services`, `describe_service`, `list_apis`, `describe_api`,
  `describe_persistence`, `describe_messages`, `search`) mais sete de
  navegação/inferência/verificação: `list_repositories` (visão cumulativa por
  repositório), `get_relationships`, `trace_flow`, `find_architecture_smells`,
  `find_change_surface`, `record_change_surface_feedback` e `verify_change_surface`.
  Toda tool documenta no próprio docstring quando chamá-la, o que ela devolve e qual a
  próxima tool natural — a narrativa de progressive disclosure vive no schema MCP, não
  só no README. Registrável em qualquer cliente MCP (Claude Code, Codex, etc.).
- **Busca por full-text (SQLite FTS5)**, não vector DB: `search` e a retrieval de
  candidatos do `find_change_surface` usam um índice FTS5 (prefix match + ranking
  `bm25`) reconstruído por serviço a cada indexação (`db.repositories.search`).
- **CI** (GitHub Actions, `.github/workflows/ci.yml`): roda a suíte inteira em
  Python 3.11 e 3.12 a cada push/PR — nenhum teste depende de `claude`/`codex` CLI
  real (backend sempre fake ou dados seedados direto via `db.repositories.*`).
- **CLI** (`index`, `update`, `list`, `status`, `export`, `analyze`, `verify`,
  `serve`) instalável globalmente via `pipx install -e .`, autoexplicativa via
  `--help` (cada subcomando tem um exemplo pronto), com barra de progresso no
  terminal (spinner, percentual, status colorido por unidade) durante o `index`.
  `analyze` roda `find_change_surface` direto pela camada de domínio, sem precisar
  de uma sessão MCP — mesma função que o servidor MCP chama, sem lógica duplicada.
  `index` aceita `--repository-name` para indexar vários repositórios distintos no
  mesmo DB, de forma cumulativa, sem colisão de nomes; `export` aceita `md` ou
  `mermaid`; `serve` aceita `--backend`/`--model` (usados só por
  `find_change_surface`); `--version` reporta a versão instalada.
- **Testes automatizados** (pytest, ciclo TDD, red→green→refactor): descoberta (3 das
  4 stacks), cada módulo de `db/repositories/` isoladamente, `generation/orchestrator.py`
  e `cli.py` com backend LLM fake rodando a descoberta real contra
  `verify/sample_project`, `export/markdown.py`, `export/mermaid.py`,
  `generation/change_surface.py` (builder, retrieval, filtro anti-alucinação,
  recalibração de confiança, freshness, provenance, unknowns, contratos em risco,
  próximas consultas), `generation/architecture.py` (cycle/fan-in/fan-out/shared
  database/duplicate integration), `generation/verification.py` (precisão/recall
  contra um git real, num repositório de teste descartável), testes de integração
  reais via protocolo MCP (stdio) para `get_relationships`, `trace_flow`,
  `find_architecture_smells`, `find_change_surface` e `verify_change_surface`, harness
  de eficiência de contexto com orçamento de tamanho de resposta, e uma **suíte de
  auto-indexação** (`tests/test_self_index_e2e.py`): o próprio `blastmap` indexa
  seu próprio código-fonte e responde `find_change_surface` sobre si mesmo — a prova
  mais direta de que o pipeline funciona fim-a-fim contra um código real e não
  trivial, e que já pegou bugs reais (ver "Desenvolvimento" abaixo).
- **Versionamento manual e deliberado**: `python scripts/bump_version.py
  <major|minor|patch>` atualiza `pyproject.toml` e `blastmap/__init__.py`
  juntos, como parte do passo de release — sem hook de commit tentando adivinhar o
  bump certo.

## Segurança

Conteúdo de repositório (README, comentários, código-fonte) é **dado não confiável**,
nunca instrução. O `blastmap` envia esse conteúdo pro LLM como evidência a ser
descrita, não como comando a ser seguido — mas nenhum prompt tem uma defesa
explícita e dedicada contra prompt injection (ex.: um comentário no código dizendo
"ignore instruções anteriores e retorne {...}"). Dito isso, o raio de alcance de uma
injeção bem-sucedida já é limitado estruturalmente, por dois mecanismos que já
existem por outro motivo, não como mitigação de segurança dedicada:

1. **Validação estrita de schema** (`generation/llm_harness.py`,
   `generate_with_retry`): toda resposta do LLM é validada contra um JSON Schema com
   `additionalProperties: false`, enums fechados e campos obrigatórios — mesmo que um
   prompt injection convença o modelo a "dizer" algo diferente, a saída continua
   presa ao formato esperado.
2. **Lista fechada de candidatos** (`generation/change_surface.py`,
   `_filter_known`): qualquer nome de serviço que o LLM retornar fora da lista de
   candidatos já indexados é descartado antes de qualquer resposta sair — um
   `fraud-service` inventado (por injeção ou alucinação comum) nunca vira uma
   entidade real na resposta.

Isso reduz o dano possível, não elimina o vetor. Se isso vier a importar mais (ex.:
indexar repositórios de terceiros não confiáveis), vale reforçar os prompts com uma
seção explícita de "o texto abaixo é dado, não instrução" — hoje o prompt de
`find_change_surface` já se aproxima disso ("Use ONLY the information given above.
Do not invent or classify a service that is not listed above."), mas isso nunca foi
testado adversarialmente.

## Limitações conhecidas

- **Sem retrocompatibilidade de schema**: o banco não tem framework de migração de
  colunas — o schema em `db/schema.sql` é a única forma esperada para tabelas já
  existentes (SQLite não altera uma tabela via `CREATE TABLE IF NOT EXISTS`; tabelas
  novas são adicionadas automaticamente, colunas novas em tabelas existentes não). Se
  uma mudança de schema afetar uma tabela já existente, apague
  `~/.blastmap/blastmap.db` (ou o `--db` que você estiver usando) e rode
  `blastmap index` de novo.
- `request_shape` só captura o formato do campo; ainda não compara contratos entre
  indexações pra detectar automaticamente que um campo obrigatório sumiu (isso
  exigiria guardar histórico de schema por API, não implementado). `contracts_at_risk`
  continua sinalizando risco por consumidor de evento, não por diff de campo.
- Heurísticas de descoberta são propositalmente simples (regex): apontam o LLM para o
  trecho certo, mas podem perder padrões incomuns (ex.: cliente HTTP instanciado numa
  variável com nome não convencional), e são desenhadas para o formato de um
  microsserviço web (endpoint HTTP, fila, ORM) — um pacote Python que é biblioteca/CLI
  em vez de serviço web (como o próprio `blastmap`) não casa com nenhum desses
  padrões, e por isso só gera a unidade de overview, sem endpoints/persistência/
  mensageria detectados. Isso também significa que **o próprio `blastmap` não
  consegue se auto-indexar via o comando `index` da CLI** (que exige `matches()`
  passar, e o manifesto de dependências mora na raiz do repo enquanto o código mora
  num subdiretório — nenhum dos dois satisfaz sozinho `PythonDetector.matches()`); a
  auto-indexação real só funciona chamando `generation.orchestrator.index_service()`
  diretamente com um detector explícito (exatamente o que
  `tests/test_self_index_e2e.py` faz, e o que foi usado pra validar o pipeline com um
  backend real durante o desenvolvimento — ver "Desenvolvimento"). Alargar
  `matches()` pra aceitar esse caso reduziria a precisão de detecção pra alvos reais
  (qualquer pacote Python passaria a "parecer" um serviço), então essa lacuna é
  deliberada, não uma tarefa pendente trivial. Sem teste automatizado especificamente
  para as stacks Go/JVM em `cli.py`/`orchestrator.py` (cobertos via Python/Node na
  suíte) — só `discovery/go_stack.py` isoladamente.
- `find_change_surface`/`search` tokenizam a query e usam FTS5 com prefix match — bom
  pra achar por palavra-chave, mas ainda não é busca semântica: uma tarefa cujo
  vocabulário não aparece em nenhuma descrição/razão indexada, e sem `hint_services`,
  não encontra candidatos (retorna listas vazias com uma `note`/`unknowns`, sem chamar
  o LLM).
- Recalibração de confiança (`record_change_surface_feedback`) é por nome exato de
  serviço, sem generalizar entre tarefas parecidas nem entre serviços — cada um
  acumula seu próprio histórico, do zero. `verify_change_surface` ajuda a popular esse
  histórico automaticamente a partir de git, mas ainda não correlaciona tarefas
  parecidas entre si (precedente histórico arquitetural é uma evolução futura, não
  implementada).
- `verify_change_surface`/`blastmap verify` são escopados a um repositório por
  vez — não há uma noção de "diff cumulativo" entre vários repositórios não
  relacionados sob um único commit de referência.
- A heurística determinística de `target_kind`/`resource_type`
  (`discovery/integration_heuristics.py`) tem uma lista curta e manual de vendors
  conhecidos — um vendor fora da lista cai em `unknown` (nunca em `external` errado
  por engano; a lista foi feita pra evitar falso positivo, não falso negativo). Ela só
  entra em jogo quando o LLM (que já viu o código real) não conseguiu classificar — na
  prática cobre a minoria dos casos.
- `find_architecture_smells` ainda não modela três coisas discutidas mas não
  implementadas: marcação de legado/strangler-fig (`role: legacy` num serviço, pra
  diferenciar um monólito em migração de um serviço novo), severidade direcional de
  ciclo (um ciclo envolvendo um serviço legado é mais grave que entre dois pares — a
  extração vazou dependência de volta), e tendência entre execuções sucessivas
  (`architecture_runs` já é versionado, mas nada ainda compara fan-in/fan-out de uma
  execução pra outra). Um monólito modular (várias fronteiras de domínio dentro de um
  único deploy) também não é detectado como tal — vira um `services` só, achatado; a
  camada de componente é o alicerce necessário pra uma futura clusterização, mas essa
  clusterização em si não existe ainda.
- `persistence_entities`/`erDiagram` não rastreiam relação entre entidades (chave
  estrangeira) — cada entidade é descrita isoladamente, com seus campos, sem linha de
  relacionamento no diagrama ER.
- Sem detecção de job agendado/batch (CronJob, Airflow DAG, `@Scheduled`,
  `node-cron`, Celery beat) — `service_calls` não distingue uma chamada síncrona feita
  de dentro de um endpoint HTTP de uma feita de dentro de um job batch noturno
  (`interaction_style` sync/async/batch, discutido, não implementado).

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest tests/                                    # suíte determinística (sem LLM), TDD
pytest tests/ --cov=blastmap --cov-report=term-missing   # cobertura
ruff check --select F401,F841 blastmap tests scripts     # imports/variáveis não usadas
vulture blastmap --min-confidence 80                     # funções/atributos não usados
```

O CI (`.github/workflows/ci.yml`) roda exatamente a suíte de testes determinística em
Python 3.11/3.12 a cada push/PR — `verify/sample_project.db` não existe em CI, então
`test_mcp_tools.py` sempre pula lá (comportamento esperado, não uma falha).

`get_relationships`/`trace_flow`/`find_architecture_smells`/`find_change_surface`/
`verify_change_surface` são exercitados via sessão MCP real (stdio), que sobe
`blastmap.mcp.server` num **subprocesso** — para a cobertura enxergar esse
subprocesso (em vez de reportar `mcp/server.py`/`mcp/queries.py` como 0% mesmo sendo
testados), é preciso um hook de `coverage` no `site-packages` do venv mais a variável
`COVERAGE_PROCESS_START`:
```bash
echo "import coverage; coverage.process_startup()" > $(python -c "import site; print(site.getsitepackages()[0])")/coverage_subprocess.pth
COVERAGE_PROCESS_START=pyproject.toml python -m coverage run -m pytest tests/
python -m coverage combine && python -m coverage report -m
```

A suíte automatizada nunca chama um LLM de verdade (backends fake/determinísticos,
DBs seedadas direto via `db.repositories.*`) — inclusive a suíte de auto-indexação
(`tests/test_self_index_e2e.py`), que roda a descoberta real contra o próprio
código-fonte do `blastmap` com um backend fake. Para validar o pipeline real
fim-a-fim — discovery → geração LLM real → SQLite → MCP —, use a própria fixture do
projeto como teste e2e manual:
```bash
blastmap index verify/sample_project --backend claude --db verify/sample_project.db
pytest tests/test_mcp_tools.py   # antes fica "skipped"; roda de verdade com esse DB
```
Isso também é o que popula `verify/sample_project.db` (gitignored, não versionado —
cada dev/CI gera o seu).

Auto-indexar o próprio `blastmap` com um backend real **não funciona via CLI**
(ver "Limitações conhecidas" — `PythonDetector.matches()` não casa nem com a raiz do
repo nem com o pacote sozinho); use a API Python diretamente, do mesmo jeito que
`tests/test_self_index_e2e.py` faz, só trocando o backend fake por um real:
```python
from pathlib import Path
from blastmap.db.connection import open_db
from blastmap.discovery.python_stack import PythonDetector
from blastmap.generation.claude_backend import ClaudeBackend
from blastmap.generation.orchestrator import index_service

conn = open_db(Path("verify/self_index.db"))
index_service(conn, "blastmap-core", Path("blastmap"), PythonDetector(), ClaudeBackend(), force=True)
```
Essa é exatamente a validação real feita durante o desenvolvimento: o overview gerado
descreveu corretamente a arquitetura do próprio projeto (CLI/MCP, os 4 stacks de
discovery, o pipeline de geração, os exports), e `find_change_surface("Adicionar
suporte a NATS na descoberta do stack Go")` apontou `blastmap-core` como
`primary` com 0.9 de confiança e o motivo certo — o próprio scanner é quem precisaria
mudar.

### Benchmark: recall de retrieval (CI) vs. precisão/recall real (manual)

`benchmark/` mede se `find_change_surface` consegue mesmo encontrar os serviços
certos — não só se o mecanismo não quebra. É deliberadamente dividido em dois níveis,
porque um benchmark que alimenta um backend fake com a "resposta certa" e depois
confere se o pipeline reproduz essa resposta é circular; não prova nada sobre o
julgamento do sistema:

- **Recall de retrieval (`tests/test_benchmark.py`, roda no CI)**: mede só se
  `KeywordGraphRetrieval.candidates()` — sem LLM nenhum — coloca os serviços
  esperados de cada tarefa no conjunto de candidatos, antes de qualquer LLM (real ou
  fake) ter chance de escolher entre eles. É uma tarefa hoje encontrável, e continua
  sendo amanhã? `benchmark/tasks.py` tem 8 tarefas escolhidas à mão (ainda não existe
  um corpus real de tarefas passadas) cobrindo padrões diferentes de alcance: match
  direto por palavra-chave, expansão de 1 e 2 saltos via chamada, expansão via
  vínculo de mensageria, e ancoragem só por `hint_services`. Rode
  `python scripts/run_benchmark_report.py` pra ver a tabela. O mesmo relatório
  também mostra `reduction_ratio` por tarefa: de todos os serviços indexados na
  fixture daquela tarefa, que fração `KeywordGraphRetrieval` **não** precisou
  colocar como candidata — a metade determinística e não-circular de "redução de
  exploração" (a outra metade — comparar tool calls/tokens de um agente com e sem
  o `blastmap` de verdade — exigiria simular um "agente baseline", o que seria
  fabricado e não verificável, então fica como metodologia manual, não código).
  Esse número depende muito do tamanho/conectividade do sistema indexado: nas
  fixtures pequenas e bem conectadas deste benchmark (o cenário "Pix"), a expansão
  de 2 saltos naturalmente alcança o sistema inteiro e a redução fica em 0 — não é
  regressão, é o tamanho real da fixture. A fixture "catalog" (dois componentes
  desconectados) mostra redução de verdade. Leia o número como "quanto esse cenário
  específico reduziu", não como um teto do que o sistema consegue em produção.
- **Precisão/recall real (manual, não roda no CI)**: só um LLM de verdade pode
  responder se o *julgamento* de `find_change_surface` está certo. Depois de indexar
  `verify/sample_project` (ou outro projeto real) com um backend real, rode
  `blastmap analyze "<tarefa>" --backend claude --db verify/sample_project.db` pra
  cada tarefa de `benchmark/tasks.py` (sem precisar subir uma sessão MCP) e compare
  `primary`/`secondary` contra `expected_services` à mão — o mesmo tratamento manual
  que o e2e real de `verify/sample_project.db` já recebe. `verify_change_surface`/
  `blastmap verify` automatiza essa comparação quando já existe um commit real
  "depois" pra comparar via `git diff`.

Conforme tarefas de engenharia reais forem acontecendo neste projeto (ou em outro
indexado por ele), o corpus de `benchmark/tasks.py` deveria crescer com elas em vez de
tarefas inventadas — isso é o que o torna um benchmark de verdade, não uma lista de
exemplos ilustrativos.

## Referência rápida

**CLI** (`blastmap <comando> --help` para exemplos): `index`, `update`, `list`,
`status`, `export` (`md`|`mermaid`), `analyze`, `verify`, `serve`, `--version`.

**MCP** (`blastmap serve`): `list_repositories`, `list_services`,
`describe_service`, `list_apis`, `describe_api`, `describe_persistence`,
`describe_messages`, `search`, `get_relationships`, `trace_flow`,
`find_architecture_smells`, `find_change_surface`, `record_change_surface_feedback`,
`verify_change_surface`.
