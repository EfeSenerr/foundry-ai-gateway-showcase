"""Offline tests for demo/cost.py (no Azure calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common  # noqa: E402
import cost  # noqa: E402


def test_parse_meter_old_and_new_formats():
    assert cost.parse_meter("gpt 4.1 mini cached Inp DZone Tokens")["kind"] == "cached"
    assert cost.parse_meter("gpt 4.1 Outp Data Zone Tokens")["per"] == 1_000
    m = cost.parse_meter("6-astra LongCo Cd Wr Std DZ 1M Tokens")
    assert (m["model"], m["kind"], m["ctx"], m["plan"], m["per"]) == ("6-astra", "cache_write", "LongCo", "Std", 1_000_000)
    assert cost.parse_meter("gpt 4.1 mini Batch Inp DZone Tokens") is None


def test_split_tokens_cached_is_part_of_prompt():
    t = cost.split_tokens(1000, 300, 100, 50)
    assert t == {"input": 600, "cached": 300, "cache_write": 100, "output": 50}


def test_missing_price_is_unpriced_not_free():
    tok = cost.split_tokens(1_000_000, 500_000, 0, 1_000_000)
    c, u = cost.cost_of(tok, {"input": 0.44, "cached": None, "output": 1.76})
    assert round(sum(c.values()), 4) == round(0.22 + 1.76, 4)
    assert u["cached"] == 500_000 and u["input"] == 0
    c, u = cost.cost_of(tok, None)
    assert sum(c.values()) == 0 and sum(u.values()) == 2_000_000


def test_kql_block_markers_and_nulls():
    block = cost.kql_price_block({"currency": "USD", "models": {"m": {"input": 1, "cached": None, "cache_write": None, "output": 2}}})
    assert block.startswith(cost.KQL_BEGIN) and block.endswith(cost.KQL_END)
    assert '"m", 1.0, real(null), real(null), 2.0' in block
    text, n = cost.replace_price_block("x\n" + block + "\ny", "NEW")
    assert n == 1 and text == "x\nNEW\ny"


def test_generated_kql_matches_price_file():
    assert cost.PRICE_KQL.read_text(encoding="utf-8") == cost.kql_price_block(cost.load_prices()) + "\n"


def test_alert_query_has_team():
    q = cost.alert_query("cs")
    assert 'Team == "cs"' in q and "__TEAM__" not in q and "invoke add_cost(true)" in q


def test_price_model_longest_match():
    assert common.price_model("gpt-4.1-mini-2025-04-14") == "gpt-4.1-mini"
    assert common.price_model("gpt-4.1") == "gpt-4.1"
