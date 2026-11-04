"""Cost & usage: who used how many tokens, and what that costs - per team, person and model."""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

DEMO = Path(__file__).resolve().parents[2] / "demo"
sys.path.insert(0, str(DEMO))

import common  # noqa: E402

common.load_env()

import cost  # noqa: E402

st.set_page_config(page_title="Cost & usage", page_icon="💶", layout="wide")
st.title("💶 Cost & usage")
st.caption("Estimated cost = tokens counted by the AI Gateway × price per model and token type "
           "(`infra/pricing/model_prices.json` - the same table the budget kill switch uses). "
           "The real bill is in Cost Management (tab *Estimate vs. actual*).")

KNOWN_APPS = {"04b07795-8ddb-461a-bbee-02f9e1bf7b46": "Azure CLI"}
COLORS = {cost.TOKEN_LABELS[k]: c for k, c in zip(cost.TOKEN_TYPES, ("#1f77b4", "#9ecae1", "#ff9896", "#d62728"))}
TODAY = datetime.now(timezone.utc).date()
PERIODS = {"Today (UTC)": TODAY, "Last 7 days": TODAY - timedelta(days=6), "Last 30 days": TODAY - timedelta(days=29),
           "This month": TODAY.replace(day=1)}


@st.cache_data(ttl=300, show_spinner="Reading gateway token metrics…")
def load_usage(start: date) -> pd.DataFrame:
    return pd.DataFrame(cost.query_usage(start))


@st.cache_data(ttl=1800, show_spinner="Reading Cost Management…")
def load_actual(start: date, end: date):
    return cost.query_actual_cost(start, end)


def money(v: float, cur: str) -> str:
    sym = {"USD": "$", "EUR": "€"}.get(cur, cur + " ")
    return f"{sym}{v:,.4f}" if abs(v) < 1 else f"{sym}{v:,.2f}"


def priced(frame: pd.DataFrame, prices: dict) -> pd.DataFrame:
    """Adds tok_*/cost_*/unpriced_* columns, Tokens, Cost and Unpriced per row."""
    rows = []
    for r in frame.itertuples(index=False):
        tok = cost.split_tokens(r.Prompt, r.Cached, r.CacheWrite, r.Output)
        c, u = cost.cost_of(tok, prices["models"].get(r.Model))
        rows.append({**{f"tok_{k}": v for k, v in tok.items()}, **{f"cost_{k}": v for k, v in c.items()},
                     **{f"unpriced_{k}": v for k, v in u.items()}})
    out = pd.concat([frame.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    out["Tokens"] = out[[f"tok_{k}" for k in cost.TOKEN_TYPES]].sum(axis=1)
    out["Cost"] = out[[f"cost_{k}" for k in cost.TOKEN_TYPES]].sum(axis=1)
    out["Unpriced"] = out[[f"unpriced_{k}" for k in cost.TOKEN_TYPES]].sum(axis=1)
    return out


# ------------------------------------------------------------------ filters
prices = cost.load_prices()
CUR = prices.get("currency", "USD")
c1, c2, c3 = st.columns([1, 2, 1])
period = c1.selectbox("Period", list(PERIODS), index=1)
start = PERIODS[period]
if c3.button("🔄 Refresh data", width="stretch"):
    load_usage.clear()
    load_actual.clear()

try:
    raw = load_usage(start)
except Exception as e:  # noqa: BLE001
    st.error(f"Could not read the gateway metrics: {e}\n\nRun `az login` (tenant of the PoC subscription) and retry.")
    st.stop()
if raw.empty:
    st.info("No token metrics in this period yet. Generate traffic (Use cases page or `run_all_smoke.py`) and wait 3-5 min.")
    st.stop()

raw["Person"] = raw["User"].where(raw["User"] != "", "(team key – no person)")
raw["App"] = raw["App"].map(lambda a: KNOWN_APPS.get(a, a) if a else "(team key)")
raw["Day"] = pd.to_datetime(raw["Day"]).dt.date
teams = sorted(t for t in raw["Team"].unique() if t)
sel_teams = c2.multiselect("Teams", teams, default=teams)
all_priced = priced(raw, prices)
df = all_priced[all_priced["Team"].isin(sel_teams)].copy()

# ------------------------------------------------------------------ completeness
unpriced_total = df["Unpriced"].sum()
if unpriced_total > 0:
    gaps = (df[df["Unpriced"] > 0].groupby("Model")[[f"unpriced_{k}" for k in cost.TOKEN_TYPES]].sum())
    detail = "; ".join(f"**{m}**: " + ", ".join(f"{cost.TOKEN_LABELS[k]} {int(row[f'unpriced_{k}']):,}"
                                                 for k in cost.TOKEN_TYPES if row[f"unpriced_{k}"] > 0)
                       for m, row in gaps.iterrows())
    st.error(f"**Estimate incomplete:** {int(unpriced_total):,} tokens have no price and are **not** included - {detail}. "
             "Add the missing prices in the **Prices** tab. (The budget kill switch counts them at the highest known price.)")


def by_type(frame: pd.DataFrame, group: str) -> pd.DataFrame:
    """Long format: one row per group value and token type (for stacked bars)."""
    g = frame.groupby(group)[[f"cost_{k}" for k in cost.TOKEN_TYPES]].sum().reset_index()
    long = g.melt(id_vars=group, var_name="Token type", value_name="Cost")
    long["Token type"] = long["Token type"].str[5:].map(cost.TOKEN_LABELS)
    return long[long["Cost"] > 0]


def summary(group: list[str]) -> pd.DataFrame:
    g = df.groupby(group).agg(
        Input=("tok_input", "sum"), Cached=("tok_cached", "sum"), CacheWrite=("tok_cache_write", "sum"),
        Output=("tok_output", "sum"), Tokens=("Tokens", "sum"), Cost=("Cost", "sum"), Unpriced=("Unpriced", "sum")).reset_index()
    g["Share"] = g["Cost"] / max(g["Cost"].sum(), 1e-12)
    return g.sort_values("Cost", ascending=False)


def money_cols(cols=("Cost",)):
    fmt = "$%.4f" if CUR == "USD" else "€%.4f" if CUR == "EUR" else f"%.4f {CUR}"
    cfg = {c: st.column_config.NumberColumn(c, format=fmt) for c in cols}
    cfg["Share"] = st.column_config.ProgressColumn("Share", format="percent", min_value=0, max_value=1)
    for c in ("Input", "Cached", "CacheWrite", "Output", "Tokens", "Unpriced"):
        cfg[c] = st.column_config.NumberColumn({"CacheWrite": "Cache write", "Unpriced": "Unpriced tokens"}.get(c, c),
                                               format="localized")
    return cfg


# ------------------------------------------------------------------ KPIs
total = df["Cost"].sum()
saved = sum(r.tok_cached / 1e6 * ((cost.price_of(prices["models"].get(r.Model), "input") or 0) -
                                  (cost.price_of(prices["models"].get(r.Model), "cached") or 0))
            for r in df.itertuples() if cost.price_of(prices["models"].get(r.Model), "cached") is not None)
people = df.loc[df["User"] != "", "User"].nunique()
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Estimated spend", money(total, CUR) + (" +?" if unpriced_total else ""),
          help=f"{period}, selected teams" + (" - incomplete, some tokens have no price" if unpriced_total else ""))
k2.metric("Tokens", f"{df['Tokens'].sum():,.0f}")
k3.metric("Teams / people", f"{df['Team'].nunique()} / {people}")
top = summary(["Team"]).iloc[0]
k4.metric("Top team", top["Team"], money(top["Cost"], CUR), delta_color="off")
k5.metric("Saved by caching", money(saved, CUR), help="Cached input tokens × (input price − cached price)")

with st.expander("Why not just count tokens? (token types and model prices)"):
    st.markdown(
        "A token is not a fixed amount of money. Each model has its own price, and each request has up to four token types:\n\n"
        "| Token type | What it is | Typical price |\n|---|---|---|\n"
        "| **Input** | the prompt the app sends (instructions, chat history, tool results) | base price |\n"
        "| **Cached input** | the part of the prompt the model has seen recently | ~10–25 % of input |\n"
        "| **Cache write** | storing a prompt prefix for later reuse (newer models) | slightly above input |\n"
        "| **Output** | what the model generates, incl. reasoning tokens | 4–5 × input |\n\n"
        "So 1M tokens on a small model can cost a few cents and on a large model tens of dollars. "
        "This page prices every token type per model; the **Prices** tab shows the table.")

tab_team, tab_person, tab_model, tab_trend, tab_prices, tab_actual = st.tabs(
    ["Teams", "People", "Models", "Trend", "Prices", "Estimate vs. actual"])

# ------------------------------------------------------------------ teams
with tab_team:
    l, r = st.columns([3, 2])
    l.plotly_chart(px.bar(by_type(df, "Team"), x="Team", y="Cost", color="Token type", color_discrete_map=COLORS,
                          title=f"Estimated cost per team ({CUR})"), width="stretch")
    s = summary(["Team"])
    r.plotly_chart(px.pie(s, names="Team", values="Cost", title="Share of spend", hole=0.5), width="stretch")
    st.dataframe(s, hide_index=True, width="stretch", column_config=money_cols())

# ------------------------------------------------------------------ people
with tab_person:
    st.caption("People are known when they call the gateway with their Entra sign-in (`/entra/openai`). "
               "Calls made with a team key appear as *(team key – no person)*.")
    s = summary(["Person", "Team"])
    st.plotly_chart(px.bar(s.head(15).iloc[::-1], x="Cost", y="Person", color="Team", orientation="h",
                           title=f"Top people by estimated cost ({CUR})"), width="stretch")
    st.dataframe(s, hide_index=True, width="stretch", column_config=money_cols())
    with st.expander("Per person and model / calling app"):
        st.dataframe(summary(["Person", "Team", "Model", "App"]), hide_index=True, width="stretch",
                     column_config=money_cols())

# ------------------------------------------------------------------ models
with tab_model:
    l, r = st.columns(2)
    l.plotly_chart(px.bar(by_type(df, "Model"), x="Model", y="Cost", color="Token type", color_discrete_map=COLORS,
                          title=f"Estimated cost per model ({CUR})"), width="stretch")
    s = summary(["Model"])
    s["Cost per 1K tokens"] = s["Cost"] / s["Tokens"].clip(lower=1) * 1000
    r.plotly_chart(px.bar(s, x="Model", y="Tokens", title="Tokens per model"), width="stretch")
    st.caption("Same tokens, different money: compare *Tokens* with *Cost* and *Cost per 1K tokens*.")
    st.dataframe(s, hide_index=True, width="stretch", column_config=money_cols(("Cost", "Cost per 1K tokens")))

# ------------------------------------------------------------------ trend
with tab_trend:
    split = st.radio("Split by", ["Team", "Model", "Person"], horizontal=True)
    t = df.groupby(["Day", split])["Cost"].sum().reset_index()
    st.plotly_chart(px.bar(t, x="Day", y="Cost", color=split, title=f"Estimated cost per UTC day ({CUR})"),
                    width="stretch")

# ------------------------------------------------------------------ prices
with tab_prices:
    st.markdown(
        f"Price per **1M tokens** in **{CUR}** · updated {prices.get('updated') or '-'}. "
        "This one table drives this page, the workbook **and the budget kill switch** (spend alerts). "
        "Empty = no price (shown as *unpriced*, never as free). **Source** `list` = refreshed from the Azure Retail "
        "Prices API (weekly via `scripts/refresh-prices.ps1 -Register`); `manual` = your negotiated price, never "
        "overwritten - editing a price sets it to `manual`.")
    models = sorted(set(prices["models"]) | set(raw["Model"]))
    rows = []
    for m in models:
        e = prices["models"].get(m, {})
        rows.append({"Model": m, "Deployment type": e.get("deployment_type", cost.DEFAULT_VARIANT["deployment_type"]),
                     "Plan": e.get("plan", cost.DEFAULT_VARIANT["plan"]), "Context": e.get("context", cost.DEFAULT_VARIANT["context"]),
                     "Source": e.get("source", "list"),
                     **{cost.TOKEN_LABELS[k]: cost.price_of(e, k) for k in cost.TOKEN_TYPES}})
    table = pd.DataFrame(rows)
    cfg = {cost.TOKEN_LABELS[k]: st.column_config.NumberColumn(format="%.4f", min_value=0) for k in cost.TOKEN_TYPES}
    cfg.update({"Deployment type": st.column_config.SelectboxColumn(options=list(cost.DEPLOYMENT_TYPES), required=True),
                "Plan": st.column_config.SelectboxColumn(options=list(cost.PLANS), required=True,
                                                         help="Standard = pay-as-you-go; Flex / Priority only exist for some models"),
                "Context": st.column_config.SelectboxColumn(options=list(cost.CONTEXTS), required=True,
                                                            help="Some models bill long-context requests higher; gateway metrics don't show context length"),
                "Source": st.column_config.SelectboxColumn(options=["list", "manual"], required=True)})
    edited = st.data_editor(table, hide_index=True, width="stretch", num_rows="dynamic", key="price_editor", column_config=cfg)

    def table_to_prices(t: pd.DataFrame) -> dict:
        out = {}
        for _, row in t.iterrows():
            name = str(row["Model"]).strip()
            if not name or name == "nan":
                continue
            old = prices["models"].get(name, {})
            entry = {"deployment_type": row["Deployment type"], "plan": row["Plan"], "context": row["Context"],
                     "source": row["Source"] or "list"}
            for k in cost.TOKEN_TYPES:
                v = row[cost.TOKEN_LABELS[k]]
                entry[k] = None if pd.isna(v) else float(v)
                if entry["source"] == "list" and old and entry[k] != cost.price_of(old, k):
                    entry["source"] = "manual"
            out[name] = entry
        return out

    def apply(msg: str) -> None:
        cost.save_prices(prices)
        try:
            synced = cost.sync_azure(prices)
            st.success(msg + (f" Applied to: {', '.join(synced)}." if synced else " Azure alerts already up to date."))
        except Exception as e:  # noqa: BLE001
            st.warning(msg + f" Saved locally, but could not update the spend alerts/workbook: {e}")

    a, b, c = st.columns(3)
    cur = a.selectbox("Currency", ["USD", "EUR"], index=["USD", "EUR"].index(CUR) if CUR in ("USD", "EUR") else 0,
                      help="Spend-alert thresholds are in this currency")
    if b.button("⬇️ Load Azure list prices", width="stretch",
                help="Refreshes all models with source 'list' for their deployment type / plan / context"):
        try:
            prices["models"] = table_to_prices(edited)
            prices["currency"] = cur
            prices, notes = cost.refresh_list_prices(prices, prices.get("region") or common.env("AZURE_LOCATION"))
            for n in notes:
                st.info(n)
            apply("List prices loaded.")
        except Exception as e:  # noqa: BLE001
            st.error(f"Price lookup failed: {e}")
    if c.button("💾 Save & apply", type="primary", width="stretch",
                help="Saves the table and updates the deployed spend alerts (kill switch) and workbook"):
        prices["models"] = table_to_prices(edited)
        prices.update(currency=cur, updated=date.today().isoformat())
        apply("Saved.")
    if any(e.get("context") == "Short" for e in prices["models"].values()):
        st.caption("ℹ️ Models with *Short* context are priced at short-context rates. Long-context requests (if any) "
                   "are billed higher, which shows up as a gap in *Estimate vs. actual*.")

# ------------------------------------------------------------------ estimate vs actual
with tab_actual:
    st.markdown(
        "Cost Management shows the **real bill** per model and token type for the PoC resource group - for **all teams** "
        "and whole **UTC days** (the team filter above does not apply here). It cannot say *who* used it and arrives "
        "**8–24 h late**, so recent days usually show less actual than estimated.")
    try:
        actual, act_cur = load_actual(start, TODAY)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read Cost Management: {e}")
        actual, act_cur = [], CUR
    if actual:
        known = sorted(set(prices["models"]) | set(raw["Model"]))
        a = pd.DataFrame(actual)
        a["Model"] = a["model"].map(lambda m: cost.meter_to_model(m, known))
        a["Token type"] = a["kind"].map(lambda k: cost.TOKEN_LABELS.get(k, k))
        est = by_type(all_priced, "Model").rename(columns={"Cost": "Estimated"})
        act = a.groupby(["Model", "Token type"])["cost"].sum().reset_index().rename(columns={"cost": "Actual"})
        cmp = est.merge(act, on=["Model", "Token type"], how="outer").fillna(0)
        per = cmp.groupby("Model")[["Estimated", "Actual"]].sum()
        m1, m2, m3 = st.columns(3)
        m1.metric("Estimated (gateway, all teams)", money(per["Estimated"].sum(), CUR) +
                  (" +?" if all_priced["Unpriced"].sum() else ""))
        m2.metric(f"Actual (Cost Management, {act_cur})", money(per["Actual"].sum(), act_cur))
        unexplained = per[(per["Estimated"] == 0) & (per["Actual"] > 0)]
        m3.metric("Billed, not in gateway metrics", money(unexplained["Actual"].sum(), act_cur),
                  help="Models with actual cost but no gateway token metrics in this period.")
        if not unexplained.empty:
            st.warning(
                f"Billed but not seen in gateway metrics: **{', '.join(unexplained.index)}**. Possible causes - check "
                "before concluding: calls that **bypass the gateway** (direct to Foundry, e.g. playground or apps with "
                "keys), missing telemetry (metric policy not applied on that API), a model/meter name the mapping "
                "doesn't recognise, day boundaries (Cost Management books by UTC day), or a price-table mismatch.")
        st.plotly_chart(px.bar(per.reset_index().melt(id_vars="Model", var_name="Source", value_name="Cost"),
                               x="Model", y="Cost", color="Source", barmode="group",
                               title="Estimated vs. actual per model"), width="stretch")
        st.dataframe(cmp.sort_values(["Model", "Token type"]), hide_index=True, width="stretch",
                     column_config=money_cols(("Estimated", "Actual")))
        if CUR != act_cur:
            st.info(f"Price table is in {CUR}, the bill in {act_cur}; totals are not directly comparable.")
    else:
        st.info("No model cost in Cost Management for this period yet (data arrives 8–24 h after use).")
