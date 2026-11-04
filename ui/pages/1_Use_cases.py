"""Use cases page: dept-scoped assistants via the AI Gateway / Foundry Agent + gateway controls."""
import asyncio
import sys
from pathlib import Path

import streamlit as st

DEMO = Path(__file__).resolve().parents[2] / "demo"
sys.path.insert(0, str(DEMO))

import common  # noqa: E402

common.load_env()

import agent_via_gateway  # noqa: E402
import chaos_500_demo  # noqa: E402
import model_routing_demo  # noqa: E402
import token_limit_demo  # noqa: E402
from create_foundry_agents import AGENT_NAMES, invoke  # noqa: E402

st.set_page_config(page_title="Use cases", page_icon="⚓", layout="wide")
st.markdown(
    "<style>html{font-size:18px}.block-container{padding-top:2rem}"
    "[data-testid=stChatMessage]{font-size:1.05rem}"
    "[data-testid=stMetricValue]{font-size:1.4rem}[data-testid=stMetricValue] *{overflow:visible!important;"
    "text-overflow:clip!important;white-space:normal!important;word-break:break-all}</style>",
    unsafe_allow_html=True)

DEPT_INFO = {
    "ops": ("Terminal Operations", "Vessel schedule, berth plan, cranes, containers, yard, customs, truck slots."),
    "it": ("Terminal IT", "Incident logs and error analysis only - no operational data."),
    "cs": ("Customer Service", "Container tracking, customs holds and truck slots - no vessel/berth/crane or IT data."),
}
EXAMPLES = {
    "ops": ["Which vessels arrive in the next 24 hours and at which berth?",
            "Which cranes work the berthed vessels?"],
    "cs": ["Where is container HFCU1042428 and is there a customs hold?",
           "List free truck slots tomorrow at NCT-B"],
    "it": ["Summarize the llm-proxy errors and the likely cause", "Show open incidents"],
}
SCOPE_TEST = {
    "ops": "Search the incident logs for timeouts and list open incidents.",
    "it": "Which vessels arrive in the next 24 hours and at which berth?",
    "cs": "Which cranes work the berthed vessels and what is the berth plan?",
}
MODE_GW, MODE_AGENT = "Agent via AI Gateway", "Foundry Agent (Agent Service)"

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Settings")
    dept = st.selectbox("Department", common.DEPARTMENTS, format_func=lambda d: f"{d} - {DEPT_INFO[d][0]}")
    st.caption(DEPT_INFO[dept][1])
    st.markdown("**Tools:** " + ", ".join(f"`{t}`" for t in common.DEPARTMENT_TOOLS[dept]))
    mode = st.radio("Mode", [MODE_GW, MODE_AGENT])
    model = "gpt-4.1-mini"
    if mode == MODE_GW:
        model = st.selectbox("Model", ["gpt-4.1-mini", "gpt-4.1", "auto"])
    else:
        st.caption(f"Agent: `{AGENT_NAMES[dept]}`")
    if st.button("Clear this chat"):
        st.session_state.pop(f"chat-{dept}-{mode}", None)
        st.rerun()

st.title("⚓ Northport Container Terminal - Use cases")
key = f"chat-{dept}-{mode}"
history = st.session_state.setdefault(key, [])


# ---------------------------------------------------------------- helpers
def gateway_info(headers: dict, usage: dict, model: str) -> dict:
    routed = common.header(headers, "x-portops-routed-model") or model
    base = common.price_model(routed)
    return {
        "routed": routed,
        "dept": common.header(headers, "x-portops-department"),
        "prompt": usage["prompt"], "completion": usage["completion"],
        "remaining": common.header(headers, "x-ratelimit-remaining-tokens"),
        "cost": common.estimate_cost(base, usage["prompt"], usage["completion"]),
        "request_id": common.header(headers, "apim-request-id"),
    }


def render_extras(m: dict) -> None:
    if m.get("mode") == MODE_GW and m.get("tool_calls") is not None:
        with st.expander(f"Tool calls ({len(m['tool_calls'])})"):
            if not m["tool_calls"]:
                st.write("No tool was called.")
            for tc in m["tool_calls"]:
                st.markdown(f"**`{tc['name']}`**")
                st.json(tc["args"], expanded=False)
                st.code(tc["result"][:400], language="json")
    g = m.get("gateway")
    if g:
        with st.container(border=True):
            st.caption("AI Gateway")
            c = st.columns(5)
            c[0].metric("Routed model", g["routed"] or "-")
            c[1].metric("Department", g["dept"] or "-")
            c[2].metric("Tokens (in / out)", f"{g['prompt']} / {g['completion']}")
            c[3].metric("Remaining dept tokens", g["remaining"] or "-")
            c[4].metric("Est. cost (list price)", f"${g['cost']:.5f}")
            st.caption("apim-request-id")
            st.code(g["request_id"] or "-", language=None)


def answer(question: str) -> dict:
    base = {"role": "assistant", "mode": mode}
    try:
        if mode == MODE_GW:
            r = asyncio.run(agent_via_gateway.run_agent(dept, question, model))
            return {**base, "content": r["answer"], "tool_calls": r["tool_calls"],
                    "gateway": gateway_info(r["headers"], r["usage"], model)}
        try:
            text = invoke(dept, question)
        except Exception as e:  # noqa: BLE001 - first az CLI token fetch can fail transiently
            if type(e).__name__ != "ClientAuthenticationError":
                raise
            text = invoke(dept, question)
        return {**base, "content": text or "(empty answer)"}
    except BaseException as e:  # noqa: BLE001 - MCP TaskGroups raise ExceptionGroup
        err = e
        while isinstance(err, BaseExceptionGroup) and err.exceptions:
            err = err.exceptions[0]
        status = getattr(err, "status_code", None)
        resp = getattr(err, "response", None)
        if status == 429:
            ra = (common.header(resp.headers, "retry-after") if resp is not None else "") or "?"
            return {**base, "warning": f"Department token limit reached (429) - retry after {ra}s. "
                                       "This is the gateway quota working as designed."}
        return {**base, "error": f"{type(err).__name__}: {str(err)[:300]}"}


def render_msg(m: dict) -> None:
    with st.chat_message(m["role"]):
        if m.get("warning"):
            st.warning(m["warning"], icon="⛔")
        if m.get("error"):
            st.error(m["error"])
        if m.get("content"):
            st.markdown(m["content"])
        if m["role"] == "assistant":
            render_extras(m)


# ---------------------------------------------------------------- tabs
tab_chat, tab_ctrl = st.tabs(["💬 Assistant", "🛠 Gateway controls"])

with tab_chat:
    pending = None
    st.caption("Try an example:")
    cols = st.columns(len(EXAMPLES[dept]) + 1)
    for i, q in enumerate(EXAMPLES[dept]):
        if cols[i].button(q, key=f"ex{i}", use_container_width=True):
            pending = q
    if cols[-1].button("🚫 Scope test (out of scope)", use_container_width=True, help=SCOPE_TEST[dept]):
        pending = SCOPE_TEST[dept]

    typed = st.chat_input("Ask the assistant...")
    question = typed or pending

    for m in history:
        render_msg(m)
    if question:
        user = {"role": "user", "content": question}
        history.append(user)
        render_msg(user)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                reply = answer(question)
        history.append(reply)
        st.rerun()

with tab_ctrl:
    st.subheader(f"Gateway controls (department: {dept})")
    b1, b2, b3 = st.tabs(["Token limit burst", "Simulate backend 500", "Model routing"])

    with b1:
        st.write("Fire parallel requests with the department key until the token quota returns **429**.")
        n = st.slider("Requests", 6, 20, 12)
        if st.button("Run token limit burst"):
            with st.spinner("Sending burst..."):
                res = token_limit_demo.run(dept, n, 6, common.env("MODEL_SMALL", "gpt-4.1-mini"))
            import pandas as pd
            df = pd.DataFrame([{
                "#": r["i"], "status": str(r["status"]), "tokens": str(r["tokens"]),
                "remaining": common.header(r["headers"], "x-ratelimit-remaining-tokens"),
                "retry-after": common.header(r["headers"], "retry-after"), "ms": r["ms"],
                "apim-request-id": common.header(r["headers"], "apim-request-id")} for r in res])
            counts = df["status"].value_counts()
            n429 = int(counts.get("429", 0))
            if n429:
                st.warning(f"{n429}/{len(res)} requests throttled (429) - the department quota is enforced.", icon="⛔")
            else:
                st.success(f"No 429 in {len(res)} requests - quota not reached for '{dept}'. "
                           "Try more requests or the `it` department.")
            st.bar_chart(counts)
            st.dataframe(df, use_container_width=True, hide_index=True)

    with b2:
        st.write("Send a normal request and one with header `x-chaos: 500`, then look both up in Log Analytics.")
        if st.button("Simulate backend 500"):
            model_small = common.env("MODEL_SMALL", "gpt-4.1-mini")
            with st.spinner("Calling gateway..."):
                ok_status, ok_id = chaos_500_demo.call(dept, model_small, False)
                bad_status, bad_id = chaos_500_demo.call(dept, model_small, True)
            c = st.columns(2)
            c[0].metric("Normal request", ok_status)
            c[0].code(ok_id, language=None)
            c[1].metric("Chaos request", bad_status)
            c[1].code(bad_id, language=None)
            if bad_status == 500:
                st.warning("500 injected - find it by apim-request-id.", icon="💥")
            st.code(f"""ApiManagementGatewayLogs
| where TimeGenerated > ago(1h)
| where CorrelationId in ("{ok_id}", "{bad_id}")
| project TimeGenerated, CorrelationId, ResponseCode, BackendResponseCode, TotalTime, BackendTime, Url""",
                    language="sql")
            st.caption("Logs can take 2-5 minutes to appear. ResponseCode=500 with empty "
                       "BackendResponseCode = gateway-produced error.")

    with b3:
        st.write("Same department, deployment `auto`: a short vs. a long analytical question.")
        if st.button("Run model routing"):
            cols = st.columns(2)
            for col, (name, q) in zip(cols, model_routing_demo.QUESTIONS.items()):
                with col, st.container(border=True):
                    st.markdown(f"**{name.title()} question**")
                    st.caption(q[:140] + ("..." if len(q) > 140 else ""))
                    try:
                        with st.spinner("Routing..."):
                            r = model_routing_demo.route(dept, name, q)
                        st.metric("Routed model", r["model"])
                        st.write(f"Tokens: {r['prompt']} / {r['completion']} - "
                                 f"est. cost ${r['cost']:.6f} (list price)")
                        st.code(r["request_id"], language=None)
                    except Exception as e:  # noqa: BLE001
                        if getattr(e, "status_code", None) == 429:
                            st.warning("Department token limit reached (429).", icon="⛔")
                        else:
                            st.error(f"{type(e).__name__}: {str(e)[:200]}")
