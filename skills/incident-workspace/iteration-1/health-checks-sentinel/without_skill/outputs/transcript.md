# Incident Diagnosis: sentinel-api returning 502 errors

**Date:** 2026-03-06
**Reported symptom:** sentinel-api returning 502 Bad Gateway errors
**Workspace:** /tmp/rawgentic-incident-test/health-checks-sentinel/.rawgentic_workspace.json
**Project:** sentinel-infra (infrastructure type)

---

## 1. Workspace & Configuration Review

### Workspace Config
- Single project registered: `sentinel-infra`, active since 2026-02-15.
- Project type: infrastructure
- Tech stack: Docker 24.0.0, Nginx 1.25.0

### Services Defined
| Service | Host | Port | Health Endpoint |
|---|---|---|---|
| sentinel-api | sentinel-api.internal | 8443 | /health/sentinel-check |
| sentinel-worker | sentinel-worker.internal | 9090 | /status/alive |

### Infrastructure
- **Docker compose files:** `docker-compose.prod.yml`, `docker-compose.monitoring.yml`
- **Production hosts:**
  - prod-sentinel-1 (10.0.42.1) -- role: primary
  - prod-sentinel-2 (10.0.42.2) -- role: replica
- **Database:** CockroachDB, migrations via dbmate in `db/migrations`
- **CI:** CircleCI (`.circleci/config.yml`)
- **Deploy method:** SSH to prod-sentinel-1, running `docker compose -f docker-compose.prod.yml up -d`

---

## 2. Diagnostic Steps Taken

### Step 1: Read workspace and project configuration
- Workspace JSON and `.rawgentic.json` were successfully read and parsed.
- The project is correctly registered and marked active.

### Step 2: Check for actual infrastructure files
- **Finding:** The project directory contains ONLY `.rawgentic.json`. None of the referenced infrastructure files exist:
  - `docker-compose.prod.yml` -- MISSING
  - `docker-compose.monitoring.yml` -- MISSING
  - `.circleci/config.yml` -- MISSING
  - `db/migrations/` directory -- MISSING
- This means the project metadata references infrastructure that either was never committed to this location, exists elsewhere, or has been deleted.

### Step 3: Attempted health checks (blocked)
- Attempted to curl `sentinel-api.internal:8443/health/sentinel-check` -- could not execute (network commands blocked in this environment).
- Attempted to curl `sentinel-worker.internal:9090/status/alive` -- could not execute.
- Attempted to ping production hosts 10.0.42.1 and 10.0.42.2 -- could not execute.
- Attempted DNS resolution for service hostnames -- could not execute.

---

## 3. Analysis: Likely Causes of 502 Errors

A 502 Bad Gateway error means the reverse proxy (Nginx 1.25.0 in this stack) received an invalid response from the upstream sentinel-api service. Based on the configuration, the following hypotheses are ordered from most to least likely:

### Hypothesis A: sentinel-api container is down or crashing
- **Evidence:** The 502 indicates Nginx is running but cannot reach the upstream. The sentinel-api service runs on port 8443, and Nginx is proxying to it.
- **Recommended check:** `docker ps -a` on prod-sentinel-1 to see if the sentinel-api container is running, restarting, or exited. Then `docker logs sentinel-api` for crash output.

### Hypothesis B: Port mismatch or TLS misconfiguration
- **Evidence:** Port 8443 is a conventional HTTPS port. If Nginx is configured to proxy via HTTP to an upstream expecting TLS (or vice versa), 502 errors would result.
- **Recommended check:** Inspect the Nginx config (likely in the docker-compose or a mounted config file) to verify the upstream protocol matches what sentinel-api expects on port 8443.

### Hypothesis C: CockroachDB connectivity issue
- **Evidence:** If sentinel-api depends on CockroachDB and the database is unreachable or in a degraded state, the API may fail health checks and Nginx may return 502.
- **Recommended check:** `docker logs` for the CockroachDB container, check CockroachDB admin UI, verify connection strings in sentinel-api environment variables.

### Hypothesis D: Resource exhaustion on prod-sentinel-1
- **Evidence:** Docker containers on a single host with both API and worker services could exhaust memory/CPU, causing the API to become unresponsive.
- **Recommended check:** `docker stats` on the host, check `dmesg` for OOM killer events, check disk space with `df -h`.

### Hypothesis E: sentinel-worker consuming shared resources
- **Evidence:** sentinel-worker runs on port 9090. If both services share CockroachDB and the worker is consuming excessive connections or CPU, the API could become unresponsive.
- **Recommended check:** Verify sentinel-worker health at `/status/alive`, check worker logs for runaway processes.

---

## 4. Recommended Immediate Actions

1. **SSH to prod-sentinel-1 (10.0.42.1)** and run:
   ```bash
   docker ps -a                          # Check container status
   docker logs sentinel-api --tail 200   # Recent API logs
   docker logs sentinel-worker --tail 50 # Worker logs for correlation
   docker stats --no-stream              # Resource usage snapshot
   ```

2. **Check Nginx logs:**
   ```bash
   docker logs nginx --tail 200          # Or check /var/log/nginx/error.log in the container
   ```

3. **Verify health endpoints directly from the host:**
   ```bash
   curl -k https://localhost:8443/health/sentinel-check   # Direct to API (bypass Nginx)
   curl http://localhost:9090/status/alive                 # Worker health
   ```

4. **If sentinel-api container is down, restart it:**
   ```bash
   cd /opt/sentinel && docker compose -f docker-compose.prod.yml up -d sentinel-api
   ```

5. **Check CockroachDB status** if the API logs show database connection errors.

6. **Check prod-sentinel-2 (10.0.42.2, replica):** Determine if traffic can be shifted to the replica while diagnosing the primary.

---

## 5. Limitations of This Diagnosis

- **No access to actual infrastructure:** This environment does not have network access to the production hosts, so all health checks, log reviews, and container inspections could not be performed.
- **No infrastructure-as-code files present:** The docker-compose files, Nginx configs, CI configs, and migration scripts referenced in `.rawgentic.json` do not exist in the project directory. Without these, the exact Nginx upstream configuration, container definitions, and environment variables cannot be reviewed.
- **No monitoring/alerting data:** No access to monitoring dashboards (the `docker-compose.monitoring.yml` references a monitoring stack that could provide metrics).

---

## 6. Summary

The 502 error from sentinel-api most likely indicates that the Nginx reverse proxy cannot reach the sentinel-api upstream service on port 8443. The most probable root cause is that the sentinel-api Docker container is down, crashing, or failing its health check. The immediate next step is to SSH into prod-sentinel-1 (10.0.42.1) and inspect container status and logs. The absence of infrastructure files in the project directory also suggests a potential deployment/configuration management issue that should be investigated separately.
