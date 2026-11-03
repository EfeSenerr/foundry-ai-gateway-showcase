# LiteLLM vs. APIM AI Gateway

Position: **complement, not replace.** Both can be chained: `OpenCode -> LiteLLM -> APIM -> Foundry`.

| | LiteLLM | APIM AI Gateway |
|---|---|---|
| Type | Open-source, self-hosted proxy | Managed Azure service |
| API | One OpenAI-compatible API over 100+ providers | Azure OpenAI/Foundry-compatible API, Azure-centric |
| Keys / limits | Virtual keys, budgets, rate limits per user/team | Subscriptions/products, token limit + quota policies |
| Identity | Keys; your own auth integration | Entra ID, managed identity to Foundry |
| Resilience | Retries, fallbacks | Backend pools, circuit breaker, retry policy |
| Telemetry | Spend tracking | Token metrics, gateway/LLM logs in Azure Monitor |
| Network | You deploy it | VNET integration, private endpoints |
| Operations | You run HA, patching, scaling | Azure SLA, you configure policies |
| Extras | Provider breadth, developer UX | Semantic cache, MCP/agent governance |

## When to chain
- Developers keep LiteLLM (per-user virtual keys, OpenCode integration, multi-provider).
- LiteLLM points at the APIM gateway (one department/team subscription key per LiteLLM deployment) -> enforced quota, Azure-side logs and managed identity to Foundry.
- Result: per-user control in LiteLLM, per-team hard limits and visibility in Azure.

## 500 debugging checklist (intermittent 500s / dropped connections LiteLLM -> Foundry)
Likely causes: transient backend errors, timeouts on long or streaming responses, idle timeouts on load balancers/firewalls, SNAT exhaustion, LiteLLM wrapping an upstream error as a generic 500.

1. Get a request id from the failing call (LiteLLM log / `apim-request-id` / `x-request-id`) and the exact timestamp.
2. Via APIM: look it up by `CorrelationId` (`request_lookup`). Compare `ResponseCode` vs `BackendResponseCode`:
   - gateway-generated -> policy, timeout, circuit breaker; fix in policy.
   - backend 5xx -> Foundry side; check `foundry_requests` (RequestResponse diagnostics) for the same time.
   - 2xx/429 at backend but 5xx to client -> retry exhausted / transformation.
3. Check latency (`latency_per_model`): spikes near a fixed duration (e.g. 60/240 s) point to idle/timeouts on a network hop.
4. Streaming and long generations: raise client/proxy timeouts; use keepalive.
5. Many outbound connections from one host: check SNAT exhaustion (NAT Gateway / more IPs), connection reuse.
6. Mitigate: retry with backoff on 429/5xx (APIM `retry` policy), backend pool with a second deployment/region, circuit breaker.
7. Without a gateway: enable Foundry diagnostic settings -> Log Analytics and chart metrics split by status code.

Demo: `python demo\chaos_500_demo.py` creates a synthetic 500 (`x-chaos: 500`) to practise steps 1-2.
