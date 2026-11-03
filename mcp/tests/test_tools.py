import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools as t  # noqa: E402
from departments import ALL_TOOL_NAMES, DEPARTMENT_TOOLS  # noqa: E402

def check_digit(cid: str) -> int:
    vals, n = {}, 10
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if n % 11 == 0:
            n += 1
        vals[ch] = n
        n += 1
    total = sum((vals[c] if c.isalpha() else int(c)) * 2 ** i for i, c in enumerate(cid[:10]))
    return total % 11 % 10


def some(pred):
    return next(c for c in t.CONTAINERS.values() if pred(c))


def test_dataset_sizes():
    assert len(t.VESSELS) == 12 and len(t.BERTHS) == 6 and len(t.CONTAINERS) == 60
    assert len(t.LOGS) >= 80 and len(t.INCIDENTS) == 8
    assert {c["crane_id"] for c in t.CRANES} == {f"QC{i:02d}" for i in range(1, 15)}


def test_container_ids_iso6346():
    for cid in t.CONTAINERS:
        assert re.fullmatch(r"[A-Z]{3}U\d{7}", cid), cid
        assert check_digit(cid) == int(cid[-1]), cid


def test_references_consistent():
    vessels = {v["name"] for v in t.VESSELS}
    berths = {b["berth"] for b in t.BERTHS}
    for c in t.CONTAINERS.values():
        assert c["vessel"] in vessels
        assert (c["yard"] is not None) == (c["status"] in ("discharged - in yard", "released - ready for pickup"))
        assert bool(c["customs_hold"]) == bool(c["customs_reason"])
    for cr in t.CRANES:
        assert cr["berth"] in berths
        if cr["vessel"]:
            assert cr["vessel"] in vessels
    for inc in t.INCIDENTS.values():
        assert inc["related_log_ids"]
    berthed = [v for v in t.VESSELS if v["status"] == "berthed"]
    assert len({v["berth"] for v in berthed}) == len(berthed)


def test_departments():
    assert set(DEPARTMENT_TOOLS["cs"]) < set(DEPARTMENT_TOOLS["ops"])
    assert not set(DEPARTMENT_TOOLS["it"]) & set(DEPARTMENT_TOOLS["ops"])
    assert set(ALL_TOOL_NAMES) == set(t.ALL_TOOLS)


def test_vessel_schedule():
    r = t.get_vessel_schedule()
    assert r["count"] == 12
    assert all(v["status"] == "berthed" for v in t.get_vessel_schedule(status="Berthed")["vessels"])
    soon = t.get_vessel_schedule(hours_ahead=12)["vessels"]
    assert soon and all(v["status"] != "departed" for v in soon)


def test_berth_and_cranes():
    assert len(t.get_berth_plan()["berths"]) == 6
    assert t.get_berth_plan("b1")["berths"][0]["berth"] == "B1"
    assert t.get_crane_assignments(berth="A1")["count"] == 4
    assert t.get_crane_assignments(vessel="nordlicht")["count"] == 4


def test_container_tools():
    held = some(lambda c: c["customs_hold"])
    r = t.check_customs_hold(held["container_id"])
    assert r["customs_hold"] and r["reason"] and not r["can_book_truck_slot"]
    assert t.track_container(held["container_id"].lower())["found"]
    assert not t.track_container("XXXU0000000")["found"]
    yard = some(lambda c: c["yard"])
    assert t.get_yard_position(yard["container_id"])["in_yard"]
    gone = some(lambda c: not c["yard"])
    assert not t.get_yard_position(gone["container_id"])["in_yard"]


def test_booking_flow():
    c = some(lambda c: c["yard"] and not c["customs_hold"])
    slot = next(s for s in t.list_truck_slots(terminal="NCT-A")["slots"] if s["available"] > 0)
    assert not t.book_truck_slot(held_id(), slot["slot_id"], "Acme")["booked"]
    r = t.book_truck_slot(c["container_id"], slot["slot_id"], "Nordspeed Trucking")
    assert r["booked"] and r["booking_ref"].startswith("BK-")
    after = next(s for s in t.list_truck_slots(terminal="NCT-A")["slots"] if s["slot_id"] == slot["slot_id"])
    assert after["booked"] == slot["booked"] + 1
    assert not t.book_truck_slot(c["container_id"], slot["slot_id"], "Nordspeed Trucking")["booked"]
    assert not t.book_truck_slot(c["container_id"], "TS-X-D9-99", "x")["booked"]


def held_id():
    return some(lambda c: c["customs_hold"] and c["yard"])["container_id"]


def test_truck_slots_dates():
    r = t.list_truck_slots()
    assert r["count"] == 48
    day = r["slots"][0]["date"]
    assert all(s["date"] == day for s in t.list_truck_slots(date=day)["slots"])


def test_it_tools():
    r = t.search_incident_logs(query="500", system="api-gateway", level="error")
    assert r["total_matches"] >= 3
    assert all("500" in l["message"] for l in r["logs"])
    assert t.search_incident_logs(limit=5)["returned"] == 5
    i = t.get_incident("inc-1001")
    assert i["found"] and i["logs"] and "llm-proxy" in {l["component"] for l in i["logs"]}
    assert not t.get_incident("INC-9999")["found"]
    s = t.summarize_errors(hours=24)
    assert s["errors"] > 0 and s["http_5xx_or_timeouts"] > 0 and "API-GATEWAY" in s["by_system"]
    assert set(t.summarize_errors(system="EDI")["by_system"]) <= {"EDI"}
