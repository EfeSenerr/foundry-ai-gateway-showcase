"""Tool implementations for the Terminal Ops Assistant MCP server (synthetic data only).

Dates in the JSON files are stored as offsets and resolved against the current time on each call,
so the data always looks "live".
"""
from __future__ import annotations

import json
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent / "data"
TERMINALS = ("NCT-A", "NCT-B")


def _load(name: str):
    return json.loads((DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))


VESSELS = _load("vessels")
BERTHS = _load("berths")
CRANES = _load("cranes")
CONTAINERS = {c["container_id"]: c for c in _load("containers")}
TRUCK_SLOTS = _load("truck_slots")
LOGS = _load("incident_logs")
INCIDENTS = {i["incident_id"]: i for i in _load("incidents")}
BOOKINGS: dict[str, dict] = {}
_lock = threading.Lock()


def _now() -> datetime:
    n = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return n - timedelta(minutes=n.minute % 15)


def _fmt(dt: Optional[datetime]) -> Optional[str]:
    return dt.strftime("%Y-%m-%dT%H:%MZ") if dt else None


def _vessel_view(v: dict, now: datetime) -> dict:
    eta = now + timedelta(hours=v["eta_offset_h"], minutes=v["delay_minutes"])
    etd = now + timedelta(hours=v["etd_offset_h"], minutes=v["delay_minutes"])
    return {"name": v["name"], "imo": v["imo"], "carrier": v["carrier"], "service": v["service"],
            "status": v["status"], "eta": _fmt(eta), "etd": _fmt(etd), "delay_minutes": v["delay_minutes"],
            "berth": v["berth"], "planned_berth": v["planned_berth"],
            "teu_discharge": v["teu_discharge"], "teu_load": v["teu_load"]}


def _norm(s: Optional[str]) -> Optional[str]:
    return s.strip().lower() if s else None


# ---------------------------------------------------------------- ops
def get_vessel_schedule(status: Optional[str] = None, hours_ahead: Optional[int] = None) -> dict:
    """List vessel calls at Northport Container Terminal (NCT-A / NCT-B).

    Args:
        status: Optional filter: "en route", "at anchor", "berthed" or "departed".
        hours_ahead: Optional. Only vessels whose ETA is within the next N hours (berthed vessels
            are included; departed vessels are excluded when set).
    Returns vessels with IMO, carrier, service, ETA/ETD (UTC), delay, berth and TEU load/discharge counts.
    """
    now = _now()
    out = []
    for v in VESSELS:
        view = _vessel_view(v, now)
        if status and _norm(status) != view["status"]:
            continue
        if hours_ahead is not None:
            if view["status"] == "departed":
                continue
            if now + timedelta(hours=v["eta_offset_h"]) > now + timedelta(hours=hours_ahead):
                continue
        out.append(view)
    out.sort(key=lambda x: x["eta"])
    return {"as_of": _fmt(now), "count": len(out), "vessels": out}


def get_berth_plan(berth: Optional[str] = None) -> dict:
    """Get the berth plan: for each berth (A1-A3 at NCT-A, B1-B3 at NCT-B) its status, current vessel and upcoming calls.

    Args:
        berth: Optional berth id such as "A1" or "B2". Omit for all six berths.
    """
    now = _now()
    by_name = {v["name"]: v for v in VESSELS}
    result = []
    for b in BERTHS:
        if berth and _norm(berth) != b["berth"].lower():
            continue
        calls = []
        for n in b["plan"]:
            v = _vessel_view(by_name[n], now)
            calls.append({"vessel": n, "status": v["status"], "eta": v["eta"], "etd": v["etd"]})
        result.append({"berth": b["berth"], "terminal": b["terminal"], "length_m": b["length_m"], "depth_m": b["depth_m"],
                       "status": b["status"], "maintenance_note": b["maintenance_note"], "calls": calls})
    return {"as_of": _fmt(now), "berths": result}


def get_crane_assignments(berth: Optional[str] = None, vessel: Optional[str] = None) -> dict:
    """Get quay crane (QC01..QC14) assignments with productivity in moves per hour.

    Args:
        berth: Optional berth id (e.g. "B1") to filter cranes.
        vessel: Optional vessel name (case-insensitive, partial match allowed) to filter cranes.
    """
    out = [c for c in CRANES
           if (not berth or _norm(berth) == c["berth"].lower())
           and (not vessel or (c["vessel"] and _norm(vessel) in c["vessel"].lower()))]
    working = [c["moves_per_hour"] for c in out if c["status"] in ("working", "degraded")]
    return {"count": len(out), "avg_moves_per_hour": round(sum(working) / len(working), 1) if working else 0,
            "cranes": out}


# ---------------------------------------------------------------- containers
def _find_container(container_id: str) -> Optional[dict]:
    return CONTAINERS.get(container_id.strip().upper().replace(" ", "").replace("-", ""))


def _not_found(container_id: str) -> dict:
    return {"found": False, "container_id": container_id,
            "message": "Container not found. Check the 11-character ISO 6346 ID (e.g. 4 letters + 7 digits).",
            "sample_ids": list(CONTAINERS)[:5]}


def track_container(container_id: str) -> dict:
    """Track a container by ISO 6346 ID: status, vessel, size/type, weight, consignee, customs hold, reefer temperature.

    Args:
        container_id: 11-character container id, e.g. "HFCU1042428".
    """
    c = _find_container(container_id)
    if not c:
        return _not_found(container_id)
    now = _now()
    vessel = next(v for v in VESSELS if v["name"] == c["vessel"])
    vv = _vessel_view(vessel, now)
    return {"found": True, "container_id": c["container_id"], "size_type": c["size_type"], "status": c["status"],
            "vessel": c["vessel"], "vessel_eta": vv["eta"], "vessel_etd": vv["etd"], "weight_kg": c["weight_kg"],
            "commodity": c["commodity"], "consignee": c["consignee"], "customs_hold": c["customs_hold"],
            "customs_reason": c["customs_reason"], "reefer_temp_c": c["reefer_temp_c"], "yard": c["yard"]}


def get_yard_position(container_id: str) -> dict:
    """Get the yard location (terminal, block, row, tier) of a container, if it is currently in the yard.

    Args:
        container_id: 11-character container id.
    """
    c = _find_container(container_id)
    if not c:
        return _not_found(container_id)
    if not c["yard"]:
        return {"found": True, "container_id": c["container_id"], "in_yard": False, "status": c["status"],
                "message": "Container is not in the yard."}
    return {"found": True, "container_id": c["container_id"], "in_yard": True, **c["yard"]}


def check_customs_hold(container_id: str) -> dict:
    """Check whether a container has a customs hold and why. A held container cannot be released or picked up.

    Args:
        container_id: 11-character container id.
    """
    c = _find_container(container_id)
    if not c:
        return _not_found(container_id)
    return {"found": True, "container_id": c["container_id"], "customs_hold": c["customs_hold"],
            "reason": c["customs_reason"], "consignee": c["consignee"],
            "can_book_truck_slot": not c["customs_hold"]}


def list_truck_slots(date: Optional[str] = None, terminal: Optional[str] = None) -> dict:
    """List truck appointment slots (2-hour windows) with remaining capacity.

    Args:
        date: Optional ISO date (YYYY-MM-DD). Defaults to today and the next two days (UTC).
        terminal: Optional "NCT-A" or "NCT-B".
    """
    today = _now().date()
    out = []
    for s in TRUCK_SLOTS:
        d = today + timedelta(days=s["day_offset"])
        if date and date != d.isoformat():
            continue
        if terminal and _norm(terminal) != s["terminal"].lower():
            continue
        with _lock:
            extra = sum(1 for b in BOOKINGS.values() if b["slot_id"] == s["slot_id"])
        booked = min(s["capacity"], s["booked"] + extra)
        out.append({"slot_id": s["slot_id"], "terminal": s["terminal"], "date": d.isoformat(),
                    "window": f"{s['start_hour']:02d}:00-{s['end_hour']:02d}:00", "capacity": s["capacity"],
                    "booked": booked, "available": s["capacity"] - booked})
    return {"count": len(out), "slots": out}


def book_truck_slot(container_id: str, slot_id: str, haulier: str) -> dict:
    """Book a truck pickup slot for a container (in-memory demo booking; resets on restart).

    Fails when the container is unknown, not ready for pickup, on customs hold, or the slot is full.

    Args:
        container_id: 11-character container id.
        slot_id: Slot id from list_truck_slots, e.g. "TS-A-D1-08".
        haulier: Name of the trucking company.
    """
    c = _find_container(container_id)
    if not c:
        return _not_found(container_id)
    if c["customs_hold"]:
        return {"booked": False, "reason": f"Customs hold: {c['customs_reason']}"}
    if c["status"] not in ("discharged - in yard", "released - ready for pickup"):
        return {"booked": False, "reason": f"Container status is '{c['status']}', not available for pickup."}
    slot = next((s for s in TRUCK_SLOTS if s["slot_id"].lower() == slot_id.strip().lower()), None)
    if not slot:
        return {"booked": False, "reason": f"Unknown slot_id '{slot_id}'. Use list_truck_slots."}
    if not haulier or not haulier.strip():
        return {"booked": False, "reason": "haulier is required."}
    with _lock:
        extra = sum(1 for b in BOOKINGS.values() if b["slot_id"] == slot["slot_id"])
        if slot["booked"] + extra >= slot["capacity"]:
            return {"booked": False, "reason": "Slot is full. Pick another slot."}
        if any(b["container_id"] == c["container_id"] for b in BOOKINGS.values()):
            return {"booked": False, "reason": "Container already has a booking."}
        ref = f"BK-{len(BOOKINGS) + 1001}"
        BOOKINGS[ref] = {"booking_ref": ref, "container_id": c["container_id"], "slot_id": slot["slot_id"],
                         "haulier": haulier.strip()}
    date = (_now().date() + timedelta(days=slot["day_offset"])).isoformat()
    return {"booked": True, "booking_ref": ref, "container_id": c["container_id"], "slot_id": slot["slot_id"],
            "terminal": slot["terminal"], "date": date, "window": f"{slot['start_hour']:02d}:00-{slot['end_hour']:02d}:00",
            "haulier": haulier.strip()}


# ---------------------------------------------------------------- it
def _log_view(l: dict, now: datetime) -> dict:
    return {"log_id": l["log_id"], "timestamp": _fmt(now - timedelta(minutes=l["minutes_ago"])), "system": l["system"],
            "level": l["level"], "component": l["component"], "message": l["message"], "incident_id": l["incident_id"]}


def search_incident_logs(query: Optional[str] = None, system: Optional[str] = None, level: Optional[str] = None,
                         limit: int = 20) -> dict:
    """Search operational log lines across terminal systems, newest first.

    Args:
        query: Optional case-insensitive text to find in message/component (e.g. "500", "timeout", "QC10").
        system: Optional: "TOS", "GATE-OCR", "CRANE-PLC", "EDI" or "API-GATEWAY".
        level: Optional: "INFO", "WARN" or "ERROR".
        limit: Max lines to return (default 20, max 100).
    """
    now = _now()
    limit = max(1, min(limit, 100))
    q = _norm(query)
    rows = [l for l in LOGS
            if (not q or q in l["message"].lower() or q in l["component"].lower())
            and (not system or _norm(system) == l["system"].lower())
            and (not level or _norm(level) == l["level"].lower())]
    rows.sort(key=lambda l: l["minutes_ago"])
    return {"total_matches": len(rows), "returned": min(len(rows), limit),
            "logs": [_log_view(l, now) for l in rows[:limit]]}


def get_incident(incident_id: str) -> dict:
    """Get an incident record with root cause, impact, next steps and its related log lines.

    Args:
        incident_id: Incident id such as "INC-1001".
    """
    i = INCIDENTS.get(incident_id.strip().upper())
    if not i:
        return {"found": False, "incident_id": incident_id, "known_ids": sorted(INCIDENTS)}
    now = _now()
    logs = [_log_view(l, now) for l in LOGS if l["incident_id"] == i["incident_id"]]
    logs.sort(key=lambda l: l["timestamp"])
    out = {k: v for k, v in i.items() if not k.endswith("_minutes_ago")}
    out.update(found=True, opened_at=_fmt(now - timedelta(minutes=i["opened_minutes_ago"])),
               resolved_at=_fmt(now - timedelta(minutes=i["resolved_minutes_ago"])) if i["resolved_minutes_ago"] else None,
               logs=logs)
    return out


def summarize_errors(system: Optional[str] = None, hours: int = 24) -> dict:
    """Summarize ERROR/WARN log lines over a time window: counts per system and component, HTTP 5xx count, open incidents.

    Args:
        system: Optional system filter ("TOS", "GATE-OCR", "CRANE-PLC", "EDI", "API-GATEWAY").
        hours: Look-back window in hours (default 24).
    """
    cutoff = hours * 60
    rows = [l for l in LOGS if l["minutes_ago"] <= cutoff and l["level"] in ("ERROR", "WARN")
            and (not system or _norm(system) == l["system"].lower())]
    by_system = Counter(l["system"] for l in rows)
    by_comp = Counter(f"{l['system']}/{l['component']}" for l in rows)
    http5xx = sum(1 for l in rows if "HTTP 5" in l["message"] or "timeout" in l["message"].lower())
    inc_ids = {l["incident_id"] for l in rows if l["incident_id"]}
    return {"window_hours": hours, "errors": sum(1 for l in rows if l["level"] == "ERROR"),
            "warnings": sum(1 for l in rows if l["level"] == "WARN"), "http_5xx_or_timeouts": http5xx,
            "by_system": dict(by_system.most_common()), "top_components": dict(by_comp.most_common(5)),
            "incidents": [{"incident_id": i, "title": INCIDENTS[i]["title"], "status": INCIDENTS[i]["status"],
                           "severity": INCIDENTS[i]["severity"]} for i in sorted(inc_ids)]}


ALL_TOOLS = {f.__name__: f for f in (
    get_vessel_schedule, get_berth_plan, get_crane_assignments, track_container, get_yard_position,
    check_customs_hold, list_truck_slots, book_truck_slot, search_incident_logs, get_incident, summarize_errors)}
