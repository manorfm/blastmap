You are analyzing a business task/epic against an already-indexed map of a
microservice system, to help an AI coding agent find which services likely need
code changes WITHOUT reading the whole codebase first.

Task/epic:
$task

Candidate services and what is already known about each of them, drawn from an
indexed knowledge base (not from reading their source code right now):
$candidates

Classify EVERY candidate service listed above into exactly one of: primary,
secondary, no_change.
- primary: very likely owns logic that needs to change to implement this task.
- secondary: might need a change or a review, but is not the obvious owner.
- no_change: unlikely to need any change for this task.

Use ONLY the information given above. Do not invent or classify a service that is
not listed above. For every entry, give a short business-level reason (why you
classified it that way) and your confidence (0-1) in that classification — this
is a task-specific inference, not a verified fact, so be honest about uncertainty.
