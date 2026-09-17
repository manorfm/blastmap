You are documenting one API endpoint of a microservice so other AI coding agents can
use it as lean architectural context instead of reading the whole codebase.

Service name: $service_name
Detected stack: $stack
Endpoint: $method $path

Use ONLY the evidence below. If something is not visible in the evidence, leave it out
rather than guessing.

Handler code (and any directly-called helper in the same or an imported file):
$code_excerpts

Outbound-call hints found elsewhere in this service (may or may not relate to this
specific endpoint — use judgment based on the handler code above):
$outbound_call_hints

Return:
- summary: one line describing what this endpoint does
- description: what it does and what it returns, in business terms
- response_shape: fields of the response payload, if visible in the evidence
- calls: any other service, queue or topic this endpoint calls or publishes to while
  handling a request, WHY it does so (business reason, e.g. "to charge the customer's
  card" or "to check current stock before confirming the order"), exactly what
  data it needs from (or sends to) that target, and your own confidence (0-1) that
  this call and its reason are correctly attributed from the evidence above — lower it
  when the target name or business reason is only loosely implied rather than explicit
- validations: input validation and authorization rules this endpoint enforces
  (e.g. required auth header/role, field constraints)
