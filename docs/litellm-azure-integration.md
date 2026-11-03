# LiteLLM with Microsoft Foundry / Azure OpenAI — integration notes

**Last verified: 2026-10-22** (GitHub issues/PRs read via the GitHub API; docs pages fetched on that date)

## TL;DR

- LiteLLM talks to Foundry-hosted Azure OpenAI deployments through the `azure/<deployment>` prefix. Other Foundry models use `azure_ai/…`, and Foundry Agent Service uses `azure_ai/agents/<agent-id>`.
- Entra ID auth is supported through `tenant_id`/`client_id`/`client_secret`, OIDC, or `DefaultAzureCredential` (managed identity). Config-file support for the last one is documented only for the SDK, so it is untested here.
- Intermittent 500s and drops from LiteLLM have a visible pattern of open and closed bugs in LiteLLM's aiohttp transport, streaming, retries and error mapping. Check LiteLLM's own logs and version before blaming Azure.
- Azure request IDs are available to LiteLLM callbacks and as `llm_provider-*` response headers, but they are not in the default response to the client. Capture them in a callback.
- Two supply-chain incidents affected LiteLLM releases in 2026. Pin the version by digest and review the advisory below.

> **Supply-chain advisory (as of 2026-10-22).** [Issue #47826](https://github.com/BerriAI/litellm/issues/47826) (open, created 2026-10-21) reports that the `litellm==1.107.0` wheels on PyPI contained a credential stealer and remote-code loader, built from the lookalike repo `Berri-AI/litellm`. PyPI removed 1.107.0, and the Docker `latest`/`main-latest`/`main-stable` tags (ghcr.io and docker.io) that briefly pointed to the infected image have been yanked. The issue advises pinning `1.106.2` (listed as clean) by digest. An earlier incident (v1.82.7 and v1.82.8, March 2026, [#24518](https://github.com/BerriAI/litellm/issues/24518)) is closed.
> **Action:** check which version and image digest you run, pin by digest, rotate any secrets that touched an affected version, and do not expose the proxy admin plane publicly.

## 1. How LiteLLM talks to Foundry / Azure

| Prefix | Use | Source |
|---|---|---|
| `azure/<deployment-name>` | Azure OpenAI-style chat, embeddings, images, audio, batches. Reasoning-series names (o-series, gpt-5) are auto-detected, or use `azure/o_series/…` and `azure/gpt5_series/…`. | https://docs.litellm.ai/docs/providers/azure/ |
| `azure/…` with `azure_api_type: responses` | Responses API on Azure. | https://docs.litellm.ai/docs/providers/azure/azure_responses ; issue #22747 |
| `azure_ai/<model>` | Non-OpenAI Foundry models (for example Mistral). Env vars `AZURE_AI_API_KEY` and `AZURE_AI_API_BASE`. Claude on Foundry also uses `azure_ai/claude-*`. | https://docs.litellm.ai/docs/providers/azure_ai ; https://docs.litellm.ai/docs/providers/azure/azure_anthropic |
| `azure_ai/agents/<agent-id>` | Foundry Agent Service. Needs Entra auth, not API keys. | `BerriAI/litellm:litellm/llms/azure_ai/agents/transformation.py` ; https://docs.litellm.ai/docs/providers/azure_ai_agents |

Code lives under `litellm/llms/azure/` (including `common_utils.py` for auth) and `litellm/llms/azure_ai/`.

**Entra ID options** (`litellm/llms/azure/common_utils.py`, `get_azure_ad_token`; docs https://docs.litellm.ai/docs/providers/azure/ → "Authentication"):
- `azure_ad_token` (a pre-obtained token), or `azure_ad_token_provider` (an SDK callable).
- `tenant_id` + `client_id` + `client_secret`, with `azure_scope` defaulting to `https://cognitiveservices.azure.com/.default`.
- `client_id` + `azure_username` + `azure_password`.
- OIDC: `azure_ad_token` starting with `oidc/`.
- `DefaultAzureCredential` (picks up managed identity) through `litellm.enable_azure_ad_token_refresh = True`. Docs show this for the SDK only.

**Config snippet for a Data Zone EU deployment with Entra ID (UNTESTED):**

```yaml
model_list:
  - model_name: coding-model                 # name the developers' tools use
    litellm_params:
      model: azure/<deployment-name>         # Foundry deployment (Data Zone EU type is transparent to LiteLLM)
      api_base: https://<resource>.openai.azure.com/
      api_version: "<supported-api-version>"
      tenant_id: os.environ/AZURE_TENANT_ID
      client_id: os.environ/AZURE_CLIENT_ID
      client_secret: os.environ/AZURE_CLIENT_SECRET
      timeout: 300
      stream_timeout: 60

litellm_settings:
  num_retries: 2
  request_timeout: 600
  # For managed identity via DefaultAzureCredential (documented for the SDK; confirm it works from the YAML):
  # enable_azure_ad_token_refresh: true
```

The identity needs the Cognitive Services OpenAI User role on the resource. A Data Zone deployment is an Azure deployment type, so we assume nothing LiteLLM-specific changes (inference, not verified in LiteLLM docs).

## 2. Known issues relevant to intermittent 500s and drops

| # | Title (short) | Status | Fix | Takeaway |
|---|---|---|---|---|
| [#22100](https://github.com/BerriAI/litellm/issues/22100) | `CancelledError` in aiohttp transport bypasses retry, logging, mapping | Closed 2026-09-02 | PR #39240 (merged 2026-09-02) | Bare 500s with no log and no retry on Azure `/v1/responses`. |
| [#22747](https://github.com/BerriAI/litellm/issues/22747) | `azure_api_type: responses` disconnects after ~60s | Open | none | Long reasoning calls fail with `ServerDisconnectedError` while `timeout: 300` is ignored. |
| [#33567](https://github.com/BerriAI/litellm/issues/33567) | Stale-socket fast-fail on Azure/OpenAI calls (keepalive not wired) | Closed 2026-07-28 | unverified | Idle flows reaped by a middlebox give instant `ReadTimeout`. Opt-in `AIOHTTP_SO_KEEPALIVE` exists (`litellm/constants.py`). |
| [#38957](https://github.com/BerriAI/litellm/issues/38957) | Cached client finalizer closes pool under in-flight SSE streams | Open | none | Streams die in batches roughly hourly with a clean EOF. |
| [#47481](https://github.com/BerriAI/litellm/issues/47481) | aiohttp hangs 40–137s on stopped upstream | Open | none | Stale DNS cache and the connect timeout replaced by `request_timeout`. Env workarounds exist. Reported on Docker backends, not Azure. |
| [#4312](https://github.com/BerriAI/litellm/issues/4312) | Long Azure streams: `incomplete chunked read` | Closed (stale) 2025-08-14 | none | Surfaces as `APIConnectionError`. |
| [#40260](https://github.com/BerriAI/litellm/issues/40260) | Clean EOF without `finish_reason` reported as success | Open | none | Truncated streams can look successful. |
| [#29602](https://github.com/BerriAI/litellm/issues/29602) | Mid-stream timeouts logged as success | Closed 2026-09-04 | not verified | Failure metrics undercount, so rely on logs as well as metrics. |
| [#38090](https://github.com/BerriAI/litellm/issues/38090) | `apim-request-id` missing in callbacks (Responses API) | Closed 2026-10-15 | PR #38131 (2026-09-03), PR #42824 (2026-09-24) | Request-id correlation now works on more endpoints. Check you run a version with both. |
| [#42247](https://github.com/BerriAI/litellm/issues/42247) | Azure `content_filter` not mapped to `ContentPolicyViolationError` | Open | PR #42255 closed unmerged | `content_policy_fallbacks` won't fire. This is a 400, not a 500. |
| [#47417](https://github.com/BerriAI/litellm/pull/47417) | Entra token fetch blocks the event loop; Azure `/responses/compact` 500 | Open PR | related #47348 merged 2026-10-19 | If you use `oidc/` auth, upgrade for the stall fix. |
| [#33969](https://github.com/BerriAI/litellm/issues/33969) | Random `InternalServerError … Connection error` | Open | none | Same symptom pattern as intermittent 500s, no diagnosis yet. |

### Debugging checklist

LiteLLM side:
1. Run a version after the `#39240` fix, pinned by digest (see the advisory).
2. Set `timeout`, `stream_timeout`, `request_timeout`, `num_retries` and `allowed_fails`/`cooldown_time` explicitly (https://docs.litellm.ai/docs/proxy/timeout, https://docs.litellm.ai/docs/proxy/reliability). Defaults seen in `constants.py`: `DEFAULT_ALLOWED_FAILS=3`, `DEFAULT_COOLDOWN_TIME_SECONDS=5`.
3. For stale or dropped connections, test `AIOHTTP_SO_KEEPALIVE=true`, a lower `AIOHTTP_KEEPALIVE_TIMEOUT` (default 15), and `AIOHTTP_TTL_DNS_CACHE` (default 300). `DISABLE_AIOHTTP_TRANSPORT=True` is mentioned as a workaround in issue #47481.
4. Use `keepalive_seconds` on the deployment for long silent streams (https://docs.litellm.ai/docs/proxy/timeout).
5. Read the response headers `x-litellm-call-id`, `x-litellm-model-api-base`, `x-litellm-attempted-retries` and `x-litellm-attempted-fallbacks` (https://docs.litellm.ai/docs/proxy/response_headers). These tell you whether a 500 came after retries.
6. Set `general_settings.include_call_id_in_error_body: true`.
7. Capture provider headers in a custom callback. `litellm.return_response_headers` exists. Azure headers appear as `_response_headers` / `_hidden_params["additional_headers"]` with an `llm_provider-` prefix (`process_azure_headers` in `litellm/llms/azure/common_utils.py`).

Azure side:
- Turn on diagnostic settings for the Foundry resource to a Log Analytics workspace, and match the request id captured in step 7 against the Azure-side logs.
- If APIM is in the path, use its gateway logs (`ApiManagementGatewayLogs`: `ResponseCode` vs `BackendResponseCode`) and token metrics (https://learn.microsoft.com/en-us/azure/api-management/genai-gateway-capabilities).
- Check that Azure actually returned a 5xx before assuming Azure caused the 500. Several issues above are LiteLLM-side.

## 3. Repos and samples

| Repo | Official / community | Stars | Last update | What it shows | Relevance |
|---|---|---|---|---|---|
| [BerriAI/litellm](https://github.com/BerriAI/litellm) | Vendor | ~61.8k | 2026-10-22 | The gateway itself | Core |
| [karpikpl/foundry-with-apim](https://github.com/karpikpl/foundry-with-apim) | Community | 21 | 2026-07-30 | 7 gateway options including an `ai-gateway-litellm` Bicep (Container Apps + Postgres, private networking) | High |
| [msft-mfg-ai/ai-foundry-deployment-options](https://github.com/msft-mfg-ai/ai-foundry-deployment-options) | Community | 14 | 2026-09-28 | `ai-gateway-litellm` on Container Apps behind App Gateway with private endpoints; described as a non-APIM option | High |
| [cmw2/openwebui-litellm](https://github.com/cmw2/openwebui-litellm) | Community, "sample code" disclaimer | 0 | 2026-10-08 | LiteLLM calls APIM, APIM uses managed identity to Foundry agents | High for chaining |
| [ams0/foundry-stack](https://github.com/ams0/foundry-stack) | Community | 0 | 2026-06-03 | Terraform with optional `apim` and `litellm` modules; LiteLLM uses managed identity | Medium |
| [Build5Nines/azd-litellm](https://github.com/Build5Nines/azd-litellm) | Community | 8 | 2026-05-21 | Minimal `azd` template for LiteLLM on Container Apps + PostgreSQL | Medium |
| [Azure-Samples/AI-Gateway](https://github.com/Azure-Samples/AI-Gateway) | Official | ~1k | 2026-09-16 | APIM AI gateway labs (no LiteLLM lab) | Reference for the APIM side |

No official Azure-Samples repo deploys LiteLLM; community repos are samples, not supported products.

## 4. Architecture options

| Option | Pros | Cons | Choose when |
|---|---|---|---|
| LiteLLM → Foundry | Fewest hops; per-user virtual keys and budgets; Entra auth supported | No Azure-side gateway logs; LiteLLM's HTTP stack is a drop suspect | Simple setup, strong LiteLLM operations |
| LiteLLM → APIM → Foundry | Azure-side request logs and token metrics; managed identity to Foundry; separates "LiteLLM drop" from "Azure error" | Extra hop and cost; two places for limits; double-retry risk | You need Azure-side visibility and central limits |
| APIM only | Native Azure policies, per-department keys, token limits | No per-user virtual keys or LiteLLM UX; tool-specific client config | Department-level control is enough |

## 5. Client configuration pointers

- **OpenCode → LiteLLM:** `opencode.json` with `@ai-sdk/openai-compatible` and `options.baseURL` set to the proxy `/v1`. Model keys must match LiteLLM `model_name`. Vision models need an explicit `modalities` entry (https://docs.litellm.ai/docs/tutorials/opencode_integration).
- **OpenCode → Azure directly:** OpenCode's provider docs include Azure OpenAI with an Entra ID (Azure CLI) option and deployment-name overrides (https://opencode.ai/docs/providers/).
- **GitHub Copilot CLI BYOK:** `COPILOT_PROVIDER_TYPE` (`openai` / `azure` / `anthropic`), `COPILOT_PROVIDER_BASE_URL`, `COPILOT_PROVIDER_API_KEY` or `COPILOT_PROVIDER_BEARER_TOKEN`, `COPILOT_PROVIDER_AZURE_API_VERSION`. Models must support tool calling and streaming (https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/use-byok-models).
- **LiteLLM observability to Azure:** OpenTelemetry (https://docs.litellm.ai/docs/observability/opentelemetry_integration) and Azure Sentinel via the Azure Monitor Logs Ingestion API (https://docs.litellm.ai/docs/observability/azure_sentinel).

## 6. Open questions and PoC tests

- Does `enable_azure_ad_token_refresh` work from YAML with managed identity in your hosting?
- Does a run with `AIOHTTP_SO_KEEPALIVE=true` reduce drops on idle connections?
- Capture the Azure request id for a failed call through a custom callback and find it in Azure diagnostic logs.
- Compare the 500 rate with and without APIM in the path, under identical load.
- Check for retry stacking between LiteLLM `num_retries` and APIM backend retry policies.
- Confirm Data Zone EU deployments need no LiteLLM-side changes.
- Which LiteLLM version contains all fixes above, and is it pinned by digest after the incident?
