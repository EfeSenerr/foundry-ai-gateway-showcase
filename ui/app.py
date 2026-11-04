"""Landing page of the Northport Container Terminal PoC showcase UI."""
import streamlit as st

st.set_page_config(page_title="Northport AI Gateway PoC", page_icon="⚓", layout="wide")

st.title("⚓ Northport Container Terminal")
st.subheader("Foundry + AI Gateway PoC - workshop showcase")

st.markdown(
    """
**What you can try here**

- **Use cases** (left menu): department-scoped assistants with MCP tools, answered either through the
  **APIM AI Gateway** or by a **Foundry Agent**. See tool calls, routed model, tokens, cost and the
  gateway request id for every answer. Gateway controls: token limit (429), simulated backend 500, model routing.

- **Governance** (left menu): your Entra team/user quota, the team budget kill switch (simulate alert → 403 → reset),
  MCP governance scenarios and links to the Workbook / portal views.

- **Cost & usage** (left menu): estimated spend per team, person and model in dollars - priced per token type
  (input, cached input, cache write, output) and per model - plus a check against the real bill in Cost Management.

**Departments**: `ops` (terminal operations), `it` (incident logs), `cs` (customer service) -
each with its own key, token quota and tool set.

*Synthetic data only. Costs are estimates from the price table `infra/pricing/model_prices.json`.*
"""
)
