# Architecture

PoC for the "Terminal Ops Assistant": many teams, one gateway, costs visible per team, agents with controlled tool access. All data is synthetic (fictional *Northport Container Terminal*).

## Layer picture (where a customer stack fits)

```mermaid
flowchart LR
    DEV["Dev tool<br/>(OpenCode)"] --> LLM["LiteLLM proxy<br/>virtual keys, budgets"]
    LLM --> APIM["APIM AI Gateway<br/>token limits, routing,<br/>metrics, MI auth"]
    APIM --> FDY["Foundry<br/>models + agents"]
    FDY --> M365["M365 / enterprise data<br/>(WorkIQ, tools via MCP)"]
```

LiteLLM and APIM are complementary: LiteLLM gives developers per-user keys and a provider-neutral API; APIM adds Azure-native identity, enforced token quotas, backend pools/circuit breaking and telemetry in Azure Monitor. See [litellm-vs-apim.md](litellm-vs-apim.md).

## Option B (PoC): one central platform, one project + product per department

```mermaid
flowchart TB
    subgraph clients["Clients"]
        CLI["demo/agent_via_gateway.py<br/>(per-dept key)"]
        AG["Foundry prompt agents<br/>(ops / it / cs)"]
    end
    subgraph rg["rg-portops-poc-&lt;suffix&gt; (swedencentral)"]
        APIM["APIM BasicV2<br/>products dept-ops / dept-it / dept-cs<br/>llm-token-limit, llm-emit-token-metric,<br/>model routing, retry, x-chaos switch"]
        subgraph F["Foundry account aif-portops (AIServices)"]
            P1["project ops"]
            P2["project it"]
            P3["project cs"]
            D1["gpt-4.1-mini"]
            D2["gpt-4.1"]
        end
        MCP["Container App<br/>FastMCP server<br/>/ops/mcp /it/mcp /cs/mcp /mcp"]
        LAW["Log Analytics + App Insights"]
        BUD["Budget + action group"]
    end
    CLI -- "api-key per dept" --> APIM
    APIM -- "managed identity<br/>(OpenAI User)" --> D1
    APIM --> D2
    AG --> P1 & P2 & P3
    AG -- "MCP tool calls (scoped)" --> MCP
    CLI -- "MCP tools (scoped)" --> MCP
    APIM -- "GatewayLogs, LlmLogs, token metrics" --> LAW
    F -- "diagnostic settings" --> LAW
    BUD -. alerts .-> LAW
```

Key points
- One Foundry resource, three projects: each project tags usage with `project` so Cost analysis can split cost per department (preview, Models sold by Azure only).
- One APIM product + subscription key per department: the key identifies the department (`Department` metric dimension) and carries the token quota.
- APIM calls Foundry with its managed identity (no keys).
- Tool scope per department is enforced at the MCP endpoint (`/<dept>/mcp`) and in the agent's `allowed_tools`.
- MCP is governed at the gateway too: agents and clients call `https://<apim>/mcp/<dept>` (native APIM MCP API, team key or Entra `Team.*` role, per-team tool allow-list, rate limit, `McpToolCalls` metric); the Container App only accepts calls carrying the APIM-injected `x-mcp-key`. See [mcp-governance.md](mcp-governance.md).

## Foundry traffic through the gateway

`scripts\setup-foundry-gateway.ps1` creates two connections in each department project, both pointing at `https://<apim>.azure-api.net/openai` with that department's APIM subscription key (read with `listSecrets` at runtime, stored only in the Foundry connection):

| Connection | Category | Used by |
|---|---|---|
| `apim-gateway-<dept>` | `ApiManagement` (metadata `deploymentInPath=true`, `inferenceAPIVersion=2024-10-21`, static `models` list) | Foundry prompt agents and Responses calls, model name `apim-gateway-<dept>/gpt-4.1-mini` |
| `apim-gateway-aoai-<dept>` | `AzureOpenAI` | Playground / Azure OpenAI-style clients (colleague pattern) |

Connection names are unique per Foundry account, hence the `-<dept>` suffix. Agent Service only calls `POST /deployments/{id}/chat/completions`, which the existing API already exposes, so no new APIM operations were needed: department product, token limit, metrics, routing and logging apply unchanged. `create_foundry_agents.py` uses the gateway model by default (`--direct` bypasses APIM). Caveats: the feature is preview and documented for Standard v2/Premium (works on BasicV2 here); tool calls to the MCP server still go straight from Agent Service to the MCP endpoint, only model calls pass APIM.

## Option A (alternative): one Foundry resource per department

```mermaid
flowchart TB
    APIM["Central APIM AI Gateway"]
    subgraph sa["Sub/RG ops"]
        FA["Foundry ops"]
    end
    subgraph sb["Sub/RG it"]
        FB["Foundry it"]
    end
    subgraph sc["Sub/RG cs"]
        FC["Foundry cs"]
    end
    APIM --> FA & FB & FC
    LAW["Central Log Analytics"]
    FA & FB & FC -. diagnostics .-> LAW
```

| | Option B (PoC) | Option A |
|---|---|---|
| Cost split | `project` tag + APIM product | Native: one resource/RG/sub per dept |
| Quotas (TPM) | Shared pool per deployment; gateway limits per dept | Own quota per resource |
| Isolation / RBAC | Project-level | Resource/subscription level |
| Ops effort | Low | Higher (N resources, N networks) |
| Blast radius | Shared | Contained |

## Call shape

`POST {APIM_GATEWAY_URL}/openai/deployments/{model}/chat/completions?api-version=2024-10-21` with header `api-key: <department key>`. `model=auto` lets the gateway route (small model by default, large for long/"analy..." prompts). Response headers: `apim-request-id`, `x-portops-department`, `x-portops-routed-model`, remaining/consumed tokens.
