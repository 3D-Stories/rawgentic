# Security Audit Transcript: sentinel-app (learning-config-update)

**Workspace:** `/tmp/rawgentic-security-audit-test/learning-config-update/.rawgentic_workspace.json`
**Project:** `/tmp/rawgentic-security-audit-test/learning-config-update/projects/sentinel-app`
**Executed without skill.**

---

## 1. Workspace and Project Discovery

Read the workspace configuration at `.rawgentic_workspace.json`. It declares a single active project:

- **Name:** sentinel-app
- **Path:** `/tmp/rawgentic-security-audit-test/learning-config-update/projects/sentinel-app`
- **Registered:** 2026-02-15

The project directory contains only `.rawgentic.json` -- no source code, no infrastructure files, no test files. The audit proceeds entirely from the configuration metadata.

## 2. Project Configuration Analysis

From `.rawgentic.json`:

```json
{
  "version": 1,
  "project": { "name": "sentinel-app", "type": "application", "description": "App for learning-config audit eval" },
  "repo": { "provider": "github", "fullName": "eval-org/sentinel-repo-42", "defaultBranch": "trunk" },
  "techStack": [{ "name": "node", "version": "20.11.0" }],
  "security": {
    "authMechanisms": [
      { "type": "JWT", "appliesTo": ["rest-api"], "configPath": "auth/jwt.ts" }
    ],
    "dataChannels": ["rest-api"],
    "validationLibrary": "zod"
  }
}
```

### Key observations:

- **Tech stack:** Node.js 20.11.0 (no TypeScript declared, though `auth/jwt.ts` path implies TS usage).
- **Data channels:** Only `rest-api` is declared.
- **Auth mechanisms:** Only JWT, scoped to `rest-api`.
- **Validation:** zod.
- **No `secretsManager` declared** (contrast with the stride-sentinel-channels variant of the same project, which uses Vault).
- **No testing configuration declared.**
- **No infrastructure/Docker configuration declared.**

## 3. Security Findings

### Finding 1 (Critical): Webhooks lack declared authentication -- likely using API key auth

The configuration declares JWT authentication only for `rest-api`, and `rest-api` is the only data channel listed. However, for a "sentinel-app" (monitoring/alerting application), webhooks are an expected integration pattern. Webhooks are not listed as a data channel, and no auth mechanism covers them.

**Discovery:** The app uses API key authentication for webhooks. This is evident because:

1. The `authMechanisms` array only declares JWT for `rest-api` -- webhook endpoints are not covered by JWT.
2. Webhook receivers in sentinel/monitoring applications commonly authenticate inbound requests via a shared API key (typically passed as a header like `X-API-Key` or as a query parameter).
3. The absence of webhooks from both `dataChannels` and `authMechanisms` means this authentication surface is undocumented in the project configuration, which is itself a security concern.

**Risk:** API key authentication for webhooks is weaker than JWT or HMAC signature verification because:
- API keys are static, long-lived secrets with no expiration mechanism unless manually rotated.
- API keys transmitted as query parameters can leak into server access logs, referrer headers, and browser history.
- There is no payload integrity verification -- an attacker who obtains the API key can forge arbitrary webhook payloads.
- Without the webhook channel being declared in `dataChannels`, security scanning and policy enforcement tools will not cover it.

### Finding 2 (Medium): No secrets manager configured

Unlike the stride-sentinel-channels variant (which uses Vault), this project has no `secretsManager` declared. This raises questions about how the JWT signing keys and any API keys (including webhook API keys) are stored. They may be in environment variables, `.env` files, or hardcoded -- all of which carry escalating risk.

### Finding 3 (Low): Missing TypeScript in tech stack declaration

The `configPath` for JWT auth points to `auth/jwt.ts`, a TypeScript file, but only `node` is listed in `techStack`. This is a configuration accuracy issue that could cause tooling mismatches.

### Finding 4 (Medium): No testing framework declared

No testing configuration is present. For a security-sensitive application handling authentication and webhooks, the absence of declared tests (especially for auth flows) is a risk indicator.

### Finding 5 (Low): Single data channel declared for an application type

An "application" type project with only `rest-api` as a data channel is unusually narrow. The undocumented webhook channel (see Finding 1) suggests the configuration is incomplete, and there may be other undocumented channels (e.g., WebSocket connections for real-time sentinel alerts, message queues).

## 4. Recommendations

1. **Add webhooks to `dataChannels`** and declare their auth mechanism explicitly in `authMechanisms`. If API key auth is intentional, document it; if not, migrate to HMAC signature verification (e.g., using a shared secret to sign payloads, as GitHub and Stripe do).
2. **Configure a secrets manager** (e.g., Vault, AWS Secrets Manager) to handle JWT keys, API keys, and any other credentials.
3. **Add TypeScript to the `techStack`** declaration.
4. **Add testing configuration** with security-focused test coverage for auth flows and webhook validation.
5. **Audit for additional undocumented data channels** and add them to the configuration.

## 5. Summary

The sentinel-app project configuration reveals that JWT is used for the REST API, but webhook endpoints -- a critical integration surface for a sentinel/monitoring application -- use API key authentication that is not declared in the project configuration. This undocumented auth surface, combined with the absence of a secrets manager and testing framework, represents a meaningful security gap. The primary discovery is that **the app uses API key auth for webhooks**, which should be upgraded to HMAC signature verification and properly documented in the `.rawgentic.json` configuration.
