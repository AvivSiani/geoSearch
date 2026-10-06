"""External services that handlers call (Stage 5+), each behind a protocol so
tests and evals replay fixtures instead of touching the network. Providers are
not handlers: they know nothing about the registry, the agent or `area_id`."""
