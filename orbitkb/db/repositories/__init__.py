"""One module per aggregate, each owning the SQL for its own tables. These are the
only modules allowed to run SQL against the orbitkb database (replaces the single
db/repository.py, which had grown to own nine unrelated aggregates)."""
