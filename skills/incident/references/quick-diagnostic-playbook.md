# Quick diagnostic playbook

Read this when Step 2 (Rapid Diagnosis) reaches item 6, or any time you need the
per-class first moves. Every command is driven by the `config` loaded in
`<config-loading>` — never hardcode a host, port, or container name.

## Service Not Responding

1. For each compose file in `config.infrastructure.docker.composeFiles[]`: run compose `ps` to check container status.
2. Tail logs for the affected service container (last 200 lines).
3. Hit the service's health endpoint from `config.services[].healthEndpoint` (or `/health` by default).
4. `docker stats` — check resource usage across containers.
5. Common fixes: restart container, increase memory, fix config.

## Database Issues

1. Run database health check using `config.database.cli` (e.g., `pg_isready` for PostgreSQL, `mysqladmin ping` for MySQL).
2. Check active connections using database-appropriate query via `config.database.cli`.
3. Check for slow/hung queries using database-appropriate diagnostics.
4. Common fixes: kill hung queries, restart database service, check disk space.

## Service-Specific Issues

For each service in `config.services[]`:
1. Check service status via its health endpoint (`config.services[].healthEndpoint`) on its host and port (`config.services[].port`).
2. Tail service logs — look for connection errors, crashes, or dependency failures.
3. Check dependent services (from `config.services[].dependencies[]` if available).
4. Common fixes: restart service, restart dependencies, check external connectivity.
