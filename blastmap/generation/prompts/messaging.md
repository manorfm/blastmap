You are documenting what a microservice publishes and consumes as asynchronous
messages, so other AI coding agents can use it as lean architectural context instead
of reading the whole codebase.

Service name: $service_name
Detected stack: $stack

Use ONLY the evidence below.

Messaging-related excerpts found in this service (producers, consumers, listeners):
$messaging_excerpts

Best-effort provider guesses from the code pattern each excerpt matched (a starting
point only — verify against the actual evidence, it can be wrong):
$provider_hints

Configuration files found in this service, which may reveal the concrete broker behind
a transport-agnostic abstraction (JMS, Celery, NestJS microservices) that the code
alone does not name:
$config_evidence

Return the list of messages this service publishes and/or consumes: channel/topic
name, direction, the concrete provider (kafka, rabbitmq, sqs, sns, service_bus,
activemq, nats, or "unknown" if the code only shows an abstraction and no
configuration evidence resolves it — never guess), the shape of the payload (fields as
visible in the evidence), and why or when it is emitted or handled.
