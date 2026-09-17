# context_insight

## Ideia

Agentes de IA que precisam entender um sistema de microsserviços hoje só têm dois
caminhos: ler o código-fonte inteiro (caro em tokens, lento) ou depender de
documentação manual que fica desatualizada. O `context_insight` "tritura" uma ou
várias árvores de código, usa um LLM (Claude Code ou Codex, via CLI headless, usando
sua assinatura em vez de API paga) para sintetizar uma documentação enxuta e
**semântica** — não só estrutural — por microsserviço e por API, persiste isso em
SQLite como um **System Knowledge Model** consultável, e serve tudo a agentes via
tool calls MCP com drill-down progressivo: lista serviços → descreve um serviço →
lista APIs → detalha uma API.

O foco não é reconstruir um call-graph via AST/LSP (isso já existe em ferramentas como
Serena ou Codebase-Memory MCP), nem virar um code-graph genérico. O foco é a camada
que falta, em duas frentes:

- **Por que**, não só o quê: para cada API, documentar por que ela chama outro
  serviço/fila (motivo de negócio), o que valida, quem pode chamar, e o que persiste —
  informação que hoje só existe na cabeça de quem escreveu o código.
- **System Intelligence para tarefas**: dado um épico em texto livre, apontar quais
  serviços/repositórios provavelmente precisam mudar — sem o agente precisar explorar
  manualmente o sistema inteiro primeiro (`find_change_surface`, ver abaixo).

Toda resposta separa **fatos** (estrutura extraída deterministicamente),
**interpretação semântica** (síntese do LLM a partir de evidência) e **inferência de
tarefa** (uma conclusão específica de um épico, sempre com `reason`, `confidence` e
`evidence` — nunca tratada como verdade absoluta).

## Uso básico

```bash
context-insight index /caminho/do/repositorio --backend claude   # ou --backend codex
context-insight index /outro/repositorio --repository-name outro-repo  # múltiplos repos no mesmo DB
context-insight list
context-insight status [servico]
context-insight export md --out docs/
context-insight serve --backend claude   # servidor MCP (stdio); backend usado só por find_change_surface
```

## Formato de resposta do MCP

As tool calls devolvem sempre JSON estruturado (nunca texto livre), pensado pra um
agente ir refinando o pedido sem carregar tudo de uma vez. Exemplos reais, gerados a
partir da fixture de exemplo do projeto:

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
motivo de negócio (`reason`, `data_needed`, `purpose_kind`) + APIs/persistência/
mensageria só como referência (nome, sem detalhe de campo):
```json
{
  "name": "orders-service",
  "short_desc": "A FastAPI service that handles order creation by charging a customer's payment and reserving product stock.",
  "long_desc": "orders-service is a Python microservice built on FastAPI, exposing a single POST /orders endpoint that creates new orders. Requests must carry a Bearer token in the Authorization header (...) it publishes events to a Kafka topic for downstream consumers, giving it a role as both an orchestrator of a synchronous checkout flow and a producer in an event-driven architecture.",
  "stack": "python",
  "calls": [
    {"to_service_name": "payments-service", "call_kind": "http", "reason": "to charge the customer's payment method for the order amount", "data_needed": ["amount", "currency", "payment_token"], "purpose_kind": "other"},
    {"to_service_name": "inventory-service", "call_kind": "http", "reason": "to check current stock for the requested SKU before confirming the order", "data_needed": ["sku", "qty"], "purpose_kind": "validation"},
    {"to_service_name": "order_created", "call_kind": "queue_publish", "reason": "to notify downstream consumers that a new order was created", "data_needed": ["order_id", "sku", "qty"], "purpose_kind": "notification"}
  ],
  "apis": [
    {"method": "POST", "path": "/orders", "summary": "Creates a new order by charging the customer's payment method and checking stock availability, then publishes an order-created event."}
  ],
  "persists": [{"name": "orders", "kind": "sql_table"}],
  "messages": [{"direction": "publishes", "channel": "order_created", "description": "Published after a payment charge succeeds and inventory stock is confirmed; signals that a new order has been created."}]
}
```

**`describe_api("orders-service", "POST", "/orders")`** — o nível mais detalhado:
formato da resposta campo a campo, as mesmas chamadas de dependência (agora só as
desta API) e as regras de validação/autorização:
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
  "calls": [
    {"to_service_name": "payments-service", "call_kind": "http", "reason": "to charge the customer's payment method for the order amount", "data_needed": ["amount", "currency", "payment_token"], "purpose_kind": "other"},
    {"to_service_name": "inventory-service", "call_kind": "http", "reason": "to check current stock for the requested SKU before confirming the order", "data_needed": ["sku", "qty"], "purpose_kind": "validation"}
  ],
  "validations": [
    {"kind": "authorization", "description": "Requires an Authorization header starting with \"Bearer \"; otherwise returns 401 with detail \"missing bearer token\"."},
    {"kind": "input_validation", "description": "Requires payload fields amount, currency, payment_token, sku, and qty (accessed directly from payload dict, so missing fields would raise an error)."}
  ]
}
```

`describe_persistence` e `describe_messages` seguem o mesmo padrão, mas devolvem o
schema completo campo a campo (o que `describe_service` só referencia pelo nome) —
são chamados à parte de propósito, pra manter `describe_service` enxuto.

## System Intelligence: relacionamentos e change surface

Duas tools adicionais vão além de "como um serviço funciona" e respondem "o que está
conectado a quê" e "o que essa tarefa provavelmente afeta":

**`get_relationships("payments-service", direction="both")`** — grafo de 1 salto ao
redor de um serviço: chamadas que ele faz (outbound), chamadas que outros serviços
fazem nele (inbound — "quem depende de mim", hoje só possível via este tool), e
vínculos de fila/tópico inferidos por nome de canal compartilhado
(`MESSAGE_LINK`). Cada aresta carrega `reason`, `confidence` (quando aplicável) e
`evidence` (arquivo/linha):
```json
{
  "service": "payments-service",
  "relationships": [
    {"type": "HTTP", "direction": "inbound", "source_service": "checkout-service",
     "reason": "authorize the payment for the order", "confidence": 0.9,
     "evidence": [{"file": "checkout.py", "start_line": 1, "end_line": 20}]},
    {"type": "MESSAGE_LINK", "direction": "outbound", "channel": "payment_authorized",
     "target_service": "notification-service", "reason": null, "confidence": null, "evidence": []}
  ]
}
```

**`find_change_surface("Adicionar suporte a Pix no checkout")`** — a primeira tool que
um agente deveria chamar ao receber um épico, antes de abrir qualquer arquivo. Usa
apenas o conhecimento já indexado (busca por palavra-chave sobre serviços/APIs/
relações + expansão de grafo via `get_relationships` + uma única síntese LLM) —
**nunca relê código-fonte**. A lista de serviços candidatos passada ao LLM é fechada;
qualquer nome que ele inventar fora dela é descartado antes de responder. Resultado é
uma **inferência de tarefa**, não fato — sempre com `reason`, `confidence` e
`evidence` por item:
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
  "flow": [{"from": "checkout-service", "to": "payments-service", "type": "HTTP"}]
}
```
Aceita um `hint_services` opcional para ancorar a busca quando o agente já suspeita de
serviços específicos. Se a tarefa não bater com nada indexado, retorna listas vazias
com uma `note` explicando — sem chamar o LLM.

## O que já está implementado

- **Descoberta por heurística** (regex/assinatura de arquivo, sem parser AST completo)
  para 4 stacks: Node.js/TypeScript (Express/NestJS), Python (FastAPI/Flask/Django),
  JVM (Java/Kotlin + Spring Boot) e Go.
- **Geração plugável**: backend `claude` ou `codex`, ambos headless via CLI, usando a
  assinatura do usuário (não API paga) por padrão. Validação do JSON retornado contra
  schema, com 1 retry e isolamento de falha por unidade (uma falha não aborta o run).
  Harness de invocação (prompt/schema loading + retry) compartilhado entre indexação e
  `find_change_surface` (`generation/llm_harness.py`).
- **SQLite como fonte da verdade** — um System Knowledge Model único, com
  `repositories` (multi-repositório explícito) e `services.repository_id`, e
  atualização incremental por hash de arquivo: só regenera a unidade (API/
  persistência/mensageria/overview) cujo arquivo mudou.
- **Evidência persistida**: `apis`, `service_calls`, `persistence_entities` e
  `messages` carregam `evidence_json` (arquivo + linha) — o mesmo trecho que o LLM viu
  ao gerar aquela informação, não uma linha inventada depois. `service_calls` também
  carrega `confidence` (0-1, avaliada pelo próprio LLM).
- **Servidor MCP** com 9 tools somente leitura: as 7 originais
  (`list_services`, `describe_service`, `list_apis`, `describe_api`,
  `describe_persistence`, `describe_messages`, `search`) mais duas novas de
  navegação/inferência: `get_relationships` (grafo de 1 salto, outbound/inbound/
  filas) e `find_change_surface` (tarefa → serviços afetados). Registrável em
  qualquer cliente MCP (Claude Code, Codex, etc.).
- **Export para Markdown** legível por humano, gerado a partir do SQLite.
- **CLI** (`index`, `update`, `list`, `status`, `export`, `serve`) instalável
  globalmente via `pipx install -e .`, com barra de progresso no terminal (spinner,
  percentual, status colorido por unidade) durante o `index`. `index` aceita
  `--repository-name` para indexar vários repositórios distintos no mesmo DB sem
  colisão de nomes; `serve` aceita `--backend`/`--model` (usados só por
  `find_change_surface`).
- **Testes automatizados** (pytest, ciclo TDD): descoberta (3 das 4 stacks), camada de
  banco/SQLite (incluindo `repositories`, evidência, chamadas inbound e vínculos de
  mensageria), `generation/change_surface.py` com backend LLM fake (incluindo o
  filtro anti-alucinação e um cenário fim-a-fim "Pix no checkout"), e testes de
  integração reais via protocolo MCP (stdio) para `get_relationships` e
  `find_change_surface`.
- **Hook de versionamento semântico** (`scripts/git-hooks/commit-msg`): bump
  automático de `major`/`minor`/`patch` a partir da mensagem de commit (Conventional
  Commits).

## Limitações conhecidas

- **Sem retrocompatibilidade de schema**: o banco não tem framework de migração — o
  schema em `db/schema.sql` é a única forma esperada. Um `~/.context_insight/context_insight.db`
  criado antes desta versão não ganha as colunas/tabelas novas automaticamente
  (SQLite não altera uma tabela já existente via `CREATE TABLE IF NOT EXISTS`); apague
  o arquivo e rode `context-insight index` de novo.
- O hook de versionamento tem um problema real em aberto: o Git fixa a árvore do
  commit **antes** de rodar o hook `commit-msg`, então o bump de versão feito pelo
  hook não entra no commit atual — ele fica staged e só é absorvido (e re-bumpado) no
  commit seguinte. Precisa de uma estratégia diferente (`pre-commit` ou um passo
  separado de release).
- Sem teste automatizado para o fluxo completo de geração (`generation/orchestrator.py`)
  nem para `cli.py`/`export/markdown.py` — hoje validados manualmente com chamadas
  reais aos backends.
- Heurísticas de descoberta são propositalmente simples (regex): apontam o LLM para o
  trecho certo, mas podem perder padrões incomuns (ex.: cliente HTTP instanciado numa
  variável com nome não convencional).
- `find_change_surface` usa `LIKE` por palavra-chave para achar os serviços iniciais
  (mesma limitação de `search`) e expande o grafo até 2 saltos — uma tarefa cujo
  vocabulário não aparece em nenhuma descrição/razão indexada, e sem `hint_services`,
  não encontra candidatos (retorna listas vazias com uma `note`, sem chamar o LLM).

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest tests/                                    # suíte determinística (sem LLM), TDD
pytest tests/ --cov=context_insight --cov-report=term-missing   # cobertura
```

`get_relationships`/`find_change_surface` são exercitados via sessão MCP real (stdio),
que sobe `context_insight.mcp.server` num **subprocesso** — para a cobertura enxergar
esse subprocesso (em vez de reportar `mcp/server.py`/`mcp/queries.py` como 0% mesmo
sendo testados), é preciso um hook de `coverage` no `site-packages` do venv mais a
variável `COVERAGE_PROCESS_START`:
```bash
echo "import coverage; coverage.process_startup()" > $(python -c "import site; print(site.getsitepackages()[0])")/coverage_subprocess.pth
COVERAGE_PROCESS_START=pyproject.toml python -m coverage run -m pytest tests/
python -m coverage combine && python -m coverage report -m
```

A suíte automatizada nunca chama um LLM de verdade (backends fake/determinísticos,
DBs seedadas direto via `db.repository`). Para validar o pipeline real fim-a-fim —
discovery → geração LLM real → SQLite → MCP — use a própria fixture do projeto como
teste e2e manual:
```bash
context-insight index verify/sample_project --backend claude --db verify/sample_project.db
pytest tests/test_mcp_tools.py   # antes fica "skipped"; roda de verdade com esse DB
```
Isso também é o que popula `verify/sample_project.db` (gitignored, não versionado —
cada dev/CI gera o seu).
