"""Governance page: Entra user/team limits, team kill switch, MCP governance, and where to see it."""
import asyncio
import sys
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

DEMO = Path(__file__).resolve().parents[2] / "demo"
sys.path.insert(0, str(DEMO))

import common  # noqa: E402

common.load_env()

import budget_demo  # noqa: E402
import entra_limits_demo  # noqa: E402
import mcp_governance_demo  # noqa: E402

st.set_page_config(page_title="Governance", page_icon="🛡️", layout="wide")
st.title("🛡️ Governance")
st.caption("Identity-based limits, team budget kill switch and MCP governance - all enforced by the APIM AI Gateway.")

SUB, RG = common.env("AZURE_SUBSCRIPTION_ID"), common.env("AZURE_RESOURCE_GROUP")
APIM = common.env("APIM_NAME")
TEAMS = common.DEPARTMENTS


def err(e: Exception) -> str:
    return f"{type(e).__name__}: {str(e)[:300]}"


tab_me, tab_kill, tab_mcp, tab_where = st.tabs(
    ["My limits (Entra)", "Team kill switch", "MCP governance", "Where to see it"])

# ------------------------------------------------------------------ (a) My limits
with tab_me:
    st.markdown("Calls `/entra/openai` with **your** `az login` token (audience "
                f"`{common.env('ENTRA_API_AUDIENCE')}`). The gateway maps you to your team (Entra group) and "
                "enforces a per-team and a per-user quota.")
    if st.button("Call the gateway as me", key="me_call"):
        with st.spinner("Getting token and calling the gateway..."):
            try:
                token = entra_limits_demo.token_me()
                claims = entra_limits_demo.decode_claims(token)
                r = entra_limits_demo.call(token, common.env("MODEL_SMALL", "gpt-4.1-mini"), 1)
                st.session_state["me"] = {"r": r, "upn": claims.get("preferred_username") or claims.get("upn", "")}
            except (Exception, SystemExit) as e:  # token_me exits on az failure
                st.session_state["me"] = {"error": err(e) if isinstance(e, Exception) else str(e)}
    me = st.session_state.get("me")
    if me:
        if "error" in me:
            st.error(me["error"])
        else:
            r = me["r"]
            (st.success if r["status"] == 200 else st.error)(
                f"HTTP {r['status']} {r['error']}".strip())
            c = st.columns(4)
            c[0].metric("Team", r["team"] or "-")
            c[1].metric("Team quota left (tokens)", r["team_quota"] or "-")
            c[2].metric("User tokens left (this minute)", r["user_tokens"] or "-")
            c[3].metric("User quota left (tokens)", r["user_quota"] or "-")
            st.caption(f"Signed in as: {r['user'] or me['upn']}")

# ------------------------------------------------------------------ (b) Kill switch
with tab_kill:
    st.markdown("A spend alert (Azure Monitor) triggers the **budget enforcer Logic App**, which sets the APIM named "
                "value `team-<team>-enabled` to `false`; the gateway then answers `403 TeamBudgetExhausted`.")
    team = st.selectbox("Team", TEAMS, index=TEAMS.index("cs"), key="kill_team")

    def show_state():
        rows = []
        try:
            est = budget_demo.todays_estimate()
        except Exception:
            est = {}
        for t in TEAMS:
            try:
                nv = budget_demo.named_value(t)
            except Exception as e:
                nv = err(e)
            rows.append({"team": t, f"team-<x>-enabled": nv, "est. USD today": round(est.get(t, 0.0), 4)})
        return pd.DataFrame(rows)

    c1, c2, c3, c4 = st.columns(4)
    do_refresh = c1.button("Refresh status", key="k_refresh")
    do_sim = c2.button("Simulate budget alert", key="k_sim")
    do_reset = c3.button("Reset (enable team)", key="k_reset")
    do_test = c4.button("Test call", key="k_test")

    if do_sim:
        with st.status(f"Simulating budget alert for {team}...", expanded=True) as s:
            try:
                r = requests.post(budget_demo.callback_url(), json=budget_demo.alert_payload(team), timeout=120)
                st.write(f"Logic App HTTP {r.status_code}")
                flipped = r.status_code == 200 and budget_demo.wait_named_value(team, "false")
                st.write(f"named value team-{team}-enabled = {budget_demo.named_value(team)}")
                code = budget_demo.wait_gateway(team, 403) if flipped else budget_demo.gateway_status(team)
                st.write(f"Gateway call for {team}: {code[0]} {code[1]}")
                st.write(f"Last Logic App run: {budget_demo.last_run()}")
                ok = flipped and code[0] == 403
                s.update(label=("Team blocked: 403 " + code[1]) if ok else "Unexpected result",
                         state="complete" if ok else "error")
            except Exception as e:
                s.update(label=err(e), state="error")
    if do_reset:
        with st.status(f"Resetting {team}...", expanded=True) as s:
            try:
                ok = budget_demo.do_reset(team)
                code = budget_demo.gateway_status(team)
                st.write(f"named value team-{team}-enabled = {budget_demo.named_value(team)}")
                st.write(f"Gateway call for {team}: {code[0]}")
                s.update(label="Team enabled again (200)" if ok and code[0] == 200 else "Reset incomplete",
                         state="complete" if ok and code[0] == 200 else "error")
            except Exception as e:
                s.update(label=err(e), state="error")
    if do_test:
        try:
            code = budget_demo.gateway_status(team)
            (st.success if code[0] == 200 else st.error)(
                f"Gateway call with team `{team}` key: HTTP {code[0]} {code[1]}".strip())
        except Exception as e:
            st.error(err(e))

    if do_refresh or do_sim or do_reset or do_test or "kill_df" not in st.session_state:
        st.session_state["kill_df"] = show_state()
    st.dataframe(st.session_state["kill_df"], hide_index=True, width='stretch')
    try:
        st.caption(f"Last enforcer run: {budget_demo.last_run()}")
    except Exception as e:
        st.caption(f"Last enforcer run: n/a ({err(e)[:100]})")

# ------------------------------------------------------------------ (c) MCP
SCENARIOS = {
    "a": "a) ops key: list tools + allowed tool via /mcp/ops",
    "b": "b) cs key calls ops-only tool get_berth_plan (expect blocked)",
    "c": "c) it key on /mcp/ops path (expect 403)",
    "d": "d) direct Container App call without / with wrong key (expect 401)",
}


def run_scenario(key: str) -> tuple[bool, str]:
    m = mcp_governance_demo
    if key in ("a", "b"):
        team = "ops" if key == "a" else "cs"
        o, d = asyncio.run(m.call(team, "get_berth_plan", {"berth": "A1"}))
        return o == ("ok" if key == "a" else "blocked"), f"{o}: {d}"
    if key == "c":
        r = m.http_post(common.gateway_mcp_url("ops"), {"api-key": common.env("MCP_KEY_IT")})
        return r.status_code == 403, f"HTTP {r.status_code} {r.text[:100]}"
    direct = common.direct_mcp_url("ops")
    r1, r2 = m.http_post(direct, {}), m.http_post(direct, {"x-mcp-key": "wrong-key"})
    return (r1.status_code in (401, 403) and r2.status_code in (401, 403),
            f"no key HTTP {r1.status_code}, wrong key HTTP {r2.status_code}")


with tab_mcp:
    st.markdown("The MCP server is reachable **only through the gateway**: per-team key, per-team path and a tool "
                "allow-list (APIM named value `mcp-tools-<team>`).")
    st.session_state.setdefault("mcp_results", {})
    cols = st.columns(5)
    for col, k in zip(cols, SCENARIOS):
        if col.button(f"Run {k})", key=f"mcp_{k}", help=SCENARIOS[k]):
            with st.spinner(f"Scenario {k}..."):
                try:
                    ok, detail = run_scenario(k)
                except Exception as e:
                    ok, detail = False, err(e)
                st.session_state["mcp_results"][k] = (ok, detail)
    if cols[4].button("Run all", key="mcp_all"):
        with st.spinner("Running a-d..."):
            for k in SCENARIOS:
                try:
                    st.session_state["mcp_results"][k] = run_scenario(k)
                except Exception as e:
                    st.session_state["mcp_results"][k] = (False, err(e))
    res = st.session_state["mcp_results"]
    if res:
        st.dataframe(pd.DataFrame(
            [{"scenario": SCENARIOS[k], "result": "PASS" if res[k][0] else "FAIL", "detail": res[k][1]}
             for k in SCENARIOS if k in res]), hide_index=True, width='stretch')
    st.caption("Tool calls are counted by the `McpToolCalls` metric (team / tool / result) - see the Workbook section "
               "'MCP tool calls' (data arrives ~3-5 min after the calls).")

# ------------------------------------------------------------------ (d) Where to see it
with tab_where:
    rid = f"https://portal.azure.com/#@/resource/subscriptions/{SUB}/resourceGroups/{RG}/providers"
    scope = f"%2Fsubscriptions%2F{SUB}%2FresourceGroups%2F{RG}"
    wb_id = common.env("WORKBOOK_ID", "")  # written by scripts/deploy-workbook.ps1
    wb = (f"https://portal.azure.com/#@/resource{wb_id}/workbook" if wb_id
          else f"https://portal.azure.com/#@/resource/subscriptions/{SUB}/resourceGroups/{RG}/overview")
    ai = common.env("APP_INSIGHTS_NAME")
    st.markdown("\n".join([
        f"- [Workbook 'PortOps AI Gateway - Cost & Operations']({wb}) - per user, per app, MCP, blocked requests, budget",
        f"- [Application Insights metrics (namespace `portops`)]({rid}/microsoft.insights/components/{ai}/metrics)",
        f"- [API Management {APIM} - Analytics]({rid}/Microsoft.ApiManagement/service/{APIM}/apim-analytics)",
        f"- [APIM named values (team kill switch, MCP tool lists)]({rid}/Microsoft.ApiManagement/service/{APIM}/properties)",
        f"- [Logic App la-portops-budget-enforcer - run history]({rid}/Microsoft.Logic/workflows/{budget_demo.ENFORCER}/logicApp)",
        f"- [Cost analysis]"
        f"(https://portal.azure.com/#view/Microsoft_Azure_CostManagement/Menu/~/costanalysis/scope/{scope})",
    ]))
    st.caption("Details: docs/where-to-see-metrics.md")

