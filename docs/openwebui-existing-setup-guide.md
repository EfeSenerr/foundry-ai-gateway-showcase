# Guide: add Entra ID + AI Gateway to an EXISTING Open WebUI (keep LiteLLM side by side)

For teams that already run Open WebUI with Entra SSO and send model traffic through a proxy (e.g. LiteLLM with a shared key). At the end:
- Users sign in as before (Entra SSO).
- A **new** model connection sends the **user's own Entra token** to APIM. APIM knows user + team, enforces team/user token quotas, logs per user/team/app and calls Foundry with its managed identity.
- The existing LiteLLM connection keeps working, so teams can migrate one at a time.

From-scratch demo (local Docker): [openwebui-entra-gateway.md](openwebui-entra-gateway.md). Agents as the user: [openwebui-agents.md](openwebui-agents.md).

```
Before:  User ─SSO─► Open WebUI ─(shared LiteLLM key)─► LiteLLM ─► Foundry
After:   User ─SSO─► Open WebUI ─┬─(shared key)──────────────► LiteLLM ─► Foundry        (unchanged, migrate later)
                                 └─(user's Entra token)──────► APIM ─(MI)─► Foundry      (new: identity, quotas, logs)
```

## 0. What changes, what doesn't

| Component | Change | Impact |
|---|---|---|
| Entra: gateway API app | **new** app registration (scope `Gateway.Use`, app roles `Team.*`) | none for existing apps |
| Entra: existing Open WebUI app | + delegated permission `Gateway.Use` (admin consent); optional app roles `Team.*` | users sign in again once |
| Open WebUI env | `MICROSOFT_OAUTH_SCOPE` (or `OAUTH_SCOPES` for generic OIDC) gets `offline_access api://<gateway-app>/Gateway.Use` | the stored access token is now **for the gateway** (one audience per token). Features that used it for Microsoft Graph (e.g. profile picture, OneDrive picker) won't work with it. Check before production |
| Open WebUI connections | + one (or two) OpenAI connections with Auth = OAuth | existing LiteLLM connection unchanged |
| APIM | + API `entra/v1` (+ named values) | additive; existing APIs/products unchanged |
| Foundry | APIM managed identity needs *Cognitive Services OpenAI User* | none |

## 1. Prerequisites

- APIM **v2 tier** (Basic v2 is enough; the Foundry portal "AI Gateway" creates Basic v2) with a **system-assigned managed identity**. Not Consumption: `llm-token-limit` isn't supported there.
- APIM MI has **Cognitive Services OpenAI User** on the Foundry resource.
- Application Insights logger on APIM (for `llm-emit-token-metric`) and the diagnostic setting "Logs related to generative AI gateway" → Log Analytics.
- Rights to create app registrations, grant admin consent, and manage groups.

## 2. Entra: gateway API app and budget groups

Portal: **Entra ID > App registrations > New registration** `ai-gateway` (single tenant), then:
1. **Expose an API**: Application ID URI `api://ai-gateway`, add scope `Gateway.Use` (admins and users).
2. **Manifest**: `"requestedAccessTokenVersion": 2`.
3. **App roles**: `Team.Ops`, `Team.IT`, `Team.CS` (+ optional `Tier.Power`), allowed member type Users/Groups.
4. **Enterprise applications > ai-gateway > Users and groups**: assign one security group per team ("budget group") to its `Team.*` role. Granting or removing access is now a group membership change.

Script equivalent (demo names): [`scripts/setup-entra.ps1`](../scripts/setup-entra.ps1).

## 3. APIM: API `entra/v1`

1. **Named values**: `entra-tenant-id`, `entra-api-app-id` (gateway app's client ID), `quota-team-<team>` (monthly tokens), `quota-owui-user` (monthly tokens per user), `team-<team>-enabled` (`true`; kill switch).
2. **Backend**: `https://<foundry>.openai.azure.com/openai` (our policy uses a backend pool `portops-openai-pool`; a single backend works too).
3. **API**: path `entra/v1`, *subscription required* **off**. Operations: `POST /chat/completions`, `POST /responses`, `GET /models`.
4. **Policy**: [`infra/policies/openai-entra-v1-policy.xml`](../infra/policies/openai-entra-v1-policy.xml). It includes four policy fragments from this repo (`portops-team-enabled`, `portops-llm-backend`, `portops-llm-outbound`, `portops-llm-onerror`). Create them from `infra/policies/fragments/`, or delete the `include-fragment` lines for a minimal start. Adapt the team names, the model allow-list and the `/models` list.
5. **Diagnostics**: on the API, enable Application Insights (metrics on) and Azure Monitor with LLM logging.

Script equivalent: [`scripts/setup-apim-entra-v1.ps1`](../scripts/setup-apim-entra-v1.ps1) (reads names from `.env`).

**Test before touching Open WebUI**:
```powershell
$tok = az account get-access-token --scope "api://ai-gateway/Gateway.Use" --query accessToken -o tsv   # requires the Azure CLI to be pre-authorized on the scope, or use any client app
curl.exe -s https://<apim>.azure-api.net/entra/v1/chat/completions -H "Authorization: Bearer $tok" -H "Content-Type: application/json" -d '{"model":"gpt-4.1-mini","messages":[{"role":"user","content":"hi"}]}' -i
```
Expect 200 with `x-portops-team` / `x-portops-user` headers, 401 without a token, 401 for a user without a `Team.*` role.

## 4. Entra: the existing Open WebUI app registration

1. **API permissions > Add > My APIs > ai-gateway > Delegated > `Gateway.Use`**, then **Grant admin consent**.
2. Optional (team-based model visibility in Open WebUI): add app roles `Team.Ops/IT/CS` **on the Open WebUI app too** and assign the same budget groups. Open WebUI syncs them as groups.

## 5. Open WebUI configuration

**Environment** (container/app settings), then restart:
```bash
MICROSOFT_OAUTH_SCOPE="openid email profile offline_access api://ai-gateway/Gateway.Use"   # generic OIDC: OAUTH_SCOPES=...
# optional: Entra roles -> Open WebUI groups (strict sync at every sign-in)
ENABLE_OAUTH_GROUP_MANAGEMENT=true
ENABLE_OAUTH_GROUP_CREATION=true
OAUTH_GROUP_CLAIM=roles
```
OAuth settings come from env by default (`ENABLE_OAUTH_PERSISTENT_CONFIG=false`). Users must **sign out and in again** to get a token with the new scope.

**Connection** (Admin Panel > Settings > Connections > OpenAI API > +). Connections are stored in the database, so the UI is fine:

| Field | Value |
|---|---|
| URL | `https://<apim>.azure-api.net/entra/v1` |
| Auth | **OAuth** (forwards the signed-in user's access token; `auth_type: system_oauth`) |
| API type | Chat Completions (default) |
| Model IDs | e.g. `gpt-4.1-mini`, `gpt-4.1` (set them explicitly; don't rely on `/models` discovery) |
| Prefix ID | e.g. `gw`, so the names don't collide with LiteLLM's models during migration |

**Reasoning models (gpt-5.x / gpt-6-*)**: add a **second** connection with the same URL, **API type = Responses** and those model IDs. On `/chat/completions`, these models reject function tools combined with reasoning, and Open WebUI v0.13 sends built-in tools.

**Who sees which model**: Admin Panel > Settings > Models > *model* > Access: grant read to the team groups. Users with role `user` only see models with a grant. Script equivalent: [`scripts/setup-openwebui-catalog.ps1`](../scripts/setup-openwebui-catalog.ps1) with [`openwebui/catalog.json`](../openwebui/catalog.json).

**Background tasks**: title, tag, follow-up and autocomplete generation also run on the user's token and quota. Point them to a cheap model (Admin > Settings > Interface > Task model) or disable them.

## 6. Verify (checklist)

- [ ] Sign out/in; chat with a `gw.*` model → answer.
- [ ] Log Analytics `AppMetrics`: tokens with Team / User / ClientApp (= Open WebUI app ID) / Model.
- [ ] User without a team role → error 401 in the chat.
- [ ] Set `quota-owui-user` to 100, chat twice → 403 "Token quota is exceeded…" in the chat; then restore it.
- [ ] Team kill switch: `team-<team>-enabled=false` → 403 `TeamBudgetExhausted`.
- [ ] LiteLLM models still work.

## 7. Migrating from LiteLLM

1. Pilot one team: give them only the `gw.*` models (access grants), keep LiteLLM for everyone else.
2. Compare spend and latency for a week (APIM metrics vs. the LiteLLM spend UI). Decide where the € budget lives: gateway token quota (per team/user, from `€ ÷ price per token`) plus the € kill switch ([budgets-and-limits.md](budgets-and-limits.md)), or LiteLLM in front.
3. Move the remaining teams; remove the LiteLLM connection, or keep it for non-Azure providers.
4. Optional: point LiteLLM itself at APIM (one subscription key per team) so non-Open WebUI clients (OpenCode etc.) also get Azure-side logs and quotas.

## 8. Rollback

Remove the connection(s), set `MICROSOFT_OAUTH_SCOPE` back to `openid email profile`, then sign out/in. The APIM API and the gateway app can stay; nothing else depends on them.
