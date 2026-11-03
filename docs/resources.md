# Further reading

**Last verified: 2026-10-22** (every link below was fetched and returned HTTP 200 with content matching the description on that date).

Public Microsoft Learn documentation, GitHub samples and customer stories related to this PoC: AI gateway in front of Microsoft Foundry, cost control, agent identity and permissions, network isolation and agent use cases such as mailbox triage and log analysis. Items marked **(preview)** are in preview. Always check the current status in the linked page before planning production use.

## 1. Industry use cases (logistics, ports, shipping)

| Resource | Type | Why it's relevant |
|---|---|---|
| [AD Ports Group builds 100+ AI agents with Azure AI Foundry](https://www.microsoft.com/en/customers/story/25589-ad-ports-group-azure-ai-foundry) | Story | A port and terminal operator (34 terminals) builds agents with Foundry and Copilot Studio, e.g. a Vessel Speed Optimizer (berth availability → fuel savings) and a Container Balancer (empty-container repositioning), plus an internal "AI Agents Directory" to scale agent creation across departments. A good match for a central platform serving many teams. |
| [C.H. Robinson automates the logistics industry using Azure AI](https://www.microsoft.com/en/customers/story/19575-ch-robinson-azure-ai-studio) | Story | A logistics company uses Foundry and Azure OpenAI to automate thousands of daily customer emails; a reference for mailbox triage. |
| [Dynamic AI Agents at Scale pattern](https://learn.microsoft.com/en-us/azure/architecture/solution-ideas/articles/ai-agents-at-scale) | Doc (architecture) | Shows how a multi-agent system can pick specialised agents per request, a pattern for terminal-operations scenarios. |
| [Maersk expands its relationship with Microsoft (2023)](https://www.maersk.com/news/articles/2023/06/08/maersk-expands-relationship-with-microsoft-to-further-digitize-and-decarbonize-logistics) | Story (press release, older) | A shipping and logistics leader's Azure data and IoT platform for container and vessel operations; context for data foundations before agents. |


## 2. Agent samples

| Resource | Type | Why it's relevant |
|---|---|---|
| [M365 Inbox Agent for Azure Functions (Python)](https://github.com/Azure-Samples/m365-inbox-serverless-agent-python) | Sample | Scans an Outlook inbox via Microsoft Graph with rules, escalates urgent mail and drafts replies; a starting point for mailbox triage. |
| [Email triage agent with Azure Functions and Azure OpenAI](https://github.com/russmsft/csa-ai-projects/blob/master/docs/how-to/03-email-triage-agent.md) | Sample (community, not an official Microsoft repo) | Walks through classify, draft and route-to-team-queue; useful as a design reference for routing logic. |
| [Tutorial: Triage incoming emails with Power Automate](https://learn.microsoft.com/en-us/azure/ai-services/language-service/custom-text-classification/tutorials/triage-email) | Doc (low-code) | Low-code route for email classification for teams that prefer workflows over code. |
| [AI playbook, examples and samples for Azure Logic Apps](https://learn.microsoft.com/en-us/azure/logic-apps/ai-resources) | Doc | Collection of agentic workflow examples in Logic Apps, including integration with Foundry. |
| [Overview of Azure SRE Agent](https://learn.microsoft.com/en-us/azure/sre-agent/overview) | Doc | Agent for incident and log investigation on Azure; good model for a log-analysis agent. |
| [Agent permissions in Azure SRE Agent](https://learn.microsoft.com/en-us/azure/sre-agent/permissions) | Doc | Explains how the agent's access is scoped, relevant to "log analysis with restricted data access". |
| [Set up the MCP connector in Azure SRE Agent](https://learn.microsoft.com/en-us/azure/sre-agent/custom-mcp-server) | Doc | Shows how to connect your own MCP tools, similar to the Terminal Ops Assistant pattern. |
| [What is Foundry IQ?](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/concepts/what-is-foundry-iq) | Doc | Knowledge layer for agents (SOPs, manuals, runbooks) with managed retrieval. |
| [File search tool for Foundry agents](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/tools/file-search) | Doc | Quick way to ground an agent on documents without building your own index. |
| [Connect agents to MCP server endpoints](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/tools/model-context-protocol) | Doc | How prompt agents call remote MCP tools, as in the demo. |
| [Microsoft Agent Framework (overview)](https://learn.microsoft.com/en-us/agent-framework/overview/) and [GitHub repo with Python/.NET samples](https://github.com/microsoft/agent-framework/tree/main/python/samples) | Doc / sample | Code-first multi-agent and workflow orchestration that runs against Foundry. |
| [Foundry Agent Service overview](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/overview) | Doc | Entry point for hosted agents, tools and the runtime. |

## 3. AI gateway and API Management

| Resource | Type | Why it's relevant |
|---|---|---|
| [Azure-Samples/AI-Gateway (labs overview)](https://github.com/Azure-Samples/AI-Gateway) | Lab collection | Hands-on labs for AI models, MCP and agents behind API Management; the base of this PoC. |
| [Lab: token rate limiting](https://github.com/Azure-Samples/AI-Gateway/tree/main/labs/token-rate-limiting) | Lab | Per-consumer token limits, the basis for per-department quotas. |
| [Lab: FinOps framework](https://github.com/Azure-Samples/AI-Gateway/tree/main/labs/finops-framework) | Lab | Cost tracking and budget enforcement through the gateway. |
| [Lab: access controlling](https://github.com/Azure-Samples/AI-Gateway/tree/main/labs/access-controlling) | Lab | Per-user and per-group access to models via the gateway, comparable to LiteLLM permissions. |
| [Lab: model routing](https://github.com/Azure-Samples/AI-Gateway/tree/main/labs/model-routing) | Lab | Route requests to different models by rule. |
| [Lab: backend pool load balancing](https://github.com/Azure-Samples/AI-Gateway/tree/main/labs/backend-pool-load-balancing) | Lab | Retry and failover across deployments, useful against intermittent 429/5xx errors. |
| [Lab: model context protocol](https://github.com/Azure-Samples/AI-Gateway/tree/main/labs/model-context-protocol) | Lab | Expose and govern MCP servers through the gateway. |
| [Lab: AI Agent Service](https://github.com/Azure-Samples/AI-Gateway/tree/main/labs/ai-agent-service) | Lab | Foundry agents working through the gateway. |
| [Lab: semantic caching](https://github.com/Azure-Samples/AI-Gateway/tree/main/labs/semantic-caching) | Lab | Reduce cost and latency for repeated questions. |
| [Lab: built-in logging](https://github.com/Azure-Samples/AI-Gateway/tree/main/labs/built-in-logging) | Lab | Request and token logging into Log Analytics, the source for the workbook in this repo. |
| [Lab: zero to production](https://github.com/Azure-Samples/AI-Gateway/tree/main/labs/zero-to-production) | Lab | Step-by-step path from a simple gateway to a production-ready setup. |
| [AI gateway capabilities in Azure API Management](https://learn.microsoft.com/en-us/azure/api-management/genai-gateway-capabilities) | Doc | Overview of token limits, metrics, load balancing, caching and MCP support. |
| [llm-token-limit policy](https://learn.microsoft.com/en-us/azure/api-management/azure-openai-token-limit-policy) and [llm-emit-token-metric policy](https://learn.microsoft.com/en-us/azure/api-management/llm-emit-token-metric-policy) | Doc (policy reference) | The exact policies behind per-department limits and token metrics. |
| [Access Foundry Models through a gateway (Architecture Center)](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/azure-openai-gateway-guide) | Doc (architecture guide) | Design guidance on when and how to put a gateway in front of Foundry models. |
| [Bring your own model / gateway to Foundry Agent Service](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/ai-gateway) | Doc | Shows how agents can use models exposed through your own gateway, relevant if an existing proxy (for example LiteLLM) or APIM stays in the path. |
| [LiteLLM: budgets and rate limits](https://docs.litellm.ai/docs/proxy/users) and [LiteLLM Azure provider](https://docs.litellm.ai/docs/providers/azure/) | Doc (third party) | Reference for LiteLLM per-user budgets and its Azure configuration. |

## 4. Cost management and FinOps for AI

| Resource | Type | Why it's relevant |
|---|---|---|
| [Plan and manage costs for Microsoft Foundry](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/manage-costs) | Doc | Cost model, tagging, budgets and analysis for Foundry. |
| [Monitor usage and spending with cost alerts](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/cost-mgt-alerts-monitor-usage-spending) | Doc | Budget alerts at subscription or resource-group level. |
| [Deployment types in Foundry Models](https://learn.microsoft.com/en-us/azure/ai-foundry/foundry-models/concepts/deployment-types) | Doc | Compares Global, Data Zone and Regional, Standard, Provisioned and Batch; the EU Data Zone decision. |
| [Provisioned throughput for Foundry Models](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/concepts/provisioned-throughput) | Doc | Explains PTU concepts and when they make sense versus pay-per-token. |
| [Provisioned throughput billing and cost management](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/provisioned-throughput-onboarding) | Doc | Sizing, billing and reservations for PTUs. |
| [Operate provisioned throughput deployments in production](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/provisioned-get-started) | Doc | Operational guidance, monitoring and utilisation. |
| [Spillover traffic management](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/spillover-traffic-management) | Doc | Combine a provisioned baseline with pay-per-token overflow. |
| [Microsoft FinOps toolkit overview](https://learn.microsoft.com/en-us/cloud-computing/finops/toolkit/finops-toolkit-overview) and [GitHub repo](https://github.com/microsoft/finops-toolkit) | Doc / sample | Dashboards and tooling for cost transparency across subscriptions. |
| [Azure OpenAI performance and latency](https://learn.microsoft.com/en-us/azure/ai-foundry/openai/how-to/latency) | Doc | Latency and throughput tuning, useful when investigating intermittent errors. |

## 5. Network isolation and landing zone

| Resource | Type | Why it's relevant |
|---|---|---|
| [Configure network isolation for Microsoft Foundry](https://learn.microsoft.com/en-us/azure/ai-foundry/how-to/configure-private-link) | Doc | Private endpoints and public-access settings for Foundry inside a private network. |
| [Private networking for Foundry Agent Service](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/virtual-networks) | Doc | Network-injected agent runtime with your own VNet. |
| [foundry-samples: Bicep infrastructure setup](https://github.com/microsoft-foundry/foundry-samples/tree/main/infrastructure/infrastructure-setup-bicep) | Sample | Ready-made templates for private-network Foundry deployments. |
| [Baseline Microsoft Foundry chat reference architecture](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/architecture/baseline-azure-ai-foundry-chat) | Doc (architecture) | Reference design with private endpoints, gateway and monitoring. |
| [Azure/AI-Landing-Zones](https://github.com/Azure/AI-Landing-Zones) | Sample (accelerator) | Reference implementation (Bicep and Terraform) of an AI landing zone. |
| [API Management with an Azure virtual network](https://learn.microsoft.com/en-us/azure/api-management/virtual-network-concepts) | Doc | Options for placing the gateway inside or in front of your network. |
| [Microsoft Foundry architecture](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/architecture) and [rollout across an organization](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/planning) | Doc | Resource and project structure; helps decide one Foundry resource versus several subscriptions. |
| [AI agent adoption guidance (Cloud Adoption Framework)](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ai-agents/) | Doc | Governance and organisation guidance for agents at scale. |

## 6. Agent identity and permissions

| Resource | Type | Why it's relevant |
|---|---|---|
| [What are agent identities? (Microsoft Entra Agent ID)](https://learn.microsoft.com/en-us/entra/agent-id/identity-platform/what-is-agent-id) | Doc | Introduces dedicated identities for agents, the basis for machine-context permissions. |
| [Agent OAuth flows: on-behalf-of flow](https://learn.microsoft.com/en-us/entra/agent-id/identity-platform/agent-on-behalf-of-oauth-flow) | Doc | How an agent acts with the signed-in user's permissions (user context). |
| [Agent identity concepts in Microsoft Foundry](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/concepts/agent-identity) | Doc | How Foundry assigns identities to agents and what to grant them. |
| [Set up MCP server authentication](https://learn.microsoft.com/en-us/azure/ai-foundry/agents/how-to/mcp-authentication) | Doc | Authentication options for MCP tools, including identity passthrough versus agent identity. |
| [Role-based access control for Microsoft Foundry](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/rbac-foundry) | Doc | Built-in roles for who can build, run and manage agents. |
