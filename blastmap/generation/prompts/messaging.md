You are documenting what a microservice publishes and consumes as asynchronous
messages, so other AI coding agents can use it as lean architectural context instead
of reading the whole codebase.

Service name: $service_name
Detected stack: $stack

Use ONLY the evidence below.

Messaging-related excerpts found in this service (producers, consumers, listeners):
$messaging_excerpts

Return the list of messages this service publishes and/or consumes: channel/topic
name, direction, the shape of the payload (fields as visible in the evidence), and why
or when it is emitted or handled.
