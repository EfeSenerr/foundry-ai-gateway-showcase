"""Generates the synthetic JSON datasets in mcp/data (time fields are offsets from "now").

Run once: python gen_data.py   (the output is committed; the server resolves offsets at startup)
All names, numbers and IDs are fictional.
"""
import json
import random
from pathlib import Path

OUT = Path(__file__).parent / "data"
rnd = random.Random(2026)


def iso6346(owner: str, serial: int, cat: str = "U") -> str:
    base = f"{owner}{cat}{serial:06d}"
    values = {}
    n = 10
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if n % 11 == 0:
            n += 1
        values[ch] = n
        n += 1
    total = 0
    for i, ch in enumerate(base):
        v = values[ch] if ch.isalpha() else int(ch)
        total += v * (2 ** i)
    return f"{base}{total % 11 % 10}"


# ---- vessels -------------------------------------------------------------
# eta_h / etd_h are hours relative to now.
V = [
    # name, imo, carrier, service, status, eta_h, etd_h, berth, discharge, load, terminal
    ("MV Nordlicht Star", "9700101", "Baltic Arrow Lines", "BAL-NE1", "berthed", -14, 10, "A1", 1850, 1620),
    ("MV Elbe Voyager", "9700218", "Hanseatic Feeder Co", "HFC-Feeder2", "berthed", -6, 8, "A2", 620, 540),
    ("MV Sundance Meridian", "9700335", "Meridian Ocean Carriers", "MOC-Asia-EU", "berthed", -20, 4, "B1", 2400, 2210),
    ("MV Aurora Bridge", "9700442", "Polar Bridge Shipping", "PBS-Atlantic", "berthed", -3, 21, "B2", 1320, 1100),
    ("MV Kestrel Dawn", "9700559", "Baltic Arrow Lines", "BAL-NE1", "at anchor", 5, 29, None, 1480, 1390),
    ("MV Fjord Mariner", "9700667", "Polar Bridge Shipping", "PBS-Atlantic", "at anchor", 9, 33, None, 960, 870),
    ("MV Cobalt Horizon", "9700774", "Meridian Ocean Carriers", "MOC-Asia-EU", "en route", 18, 44, None, 2650, 2400),
    ("MV Saltmarsh Express", "9700881", "Hanseatic Feeder Co", "HFC-Feeder2", "en route", 26, 40, None, 540, 480),
    ("MV Ostsee Pioneer", "9700992", "Baltic Arrow Lines", "BAL-Baltic3", "en route", 38, 60, None, 780, 720),
    ("MV Glacier Tern", "9701003", "Polar Bridge Shipping", "PBS-Atlantic", "en route", 52, 78, None, 1190, 1010),
    ("MV Amber Lantern", "9701110", "Hanseatic Feeder Co", "HFC-Feeder2", "departed", -40, -22, "A1", 700, 650),
    ("MV Corsair Meridian", "9701227", "Meridian Ocean Carriers", "MOC-Asia-EU", "departed", -30, -9, "B1", 2050, 1980),
]
planned_berth = {"MV Kestrel Dawn": "A1", "MV Fjord Mariner": "B2", "MV Cobalt Horizon": "B1",
                 "MV Saltmarsh Express": "A2", "MV Ostsee Pioneer": "A1", "MV Glacier Tern": "B2"}
vessels = []
for name, imo, carrier, svc, st, eta, etd, berth, dis, ld in V:
    vessels.append({
        "name": name, "imo": imo, "carrier": carrier, "service": svc, "status": st,
        "eta_offset_h": eta, "etd_offset_h": etd,
        "berth": berth, "planned_berth": berth or planned_berth[name],
        "teu_discharge": dis, "teu_load": ld,
        "delay_minutes": rnd.choice([0, 0, 0, 30, 45, 90, 180]) if st in ("en route", "at anchor") else 0,
    })

# ---- berths & cranes -----------------------------------------------------
berth_defs = [("A1", "NCT-A", 400, 16.0), ("A2", "NCT-A", 350, 14.5), ("A3", "NCT-A", 300, 13.5),
              ("B1", "NCT-B", 420, 16.5), ("B2", "NCT-B", 380, 15.0), ("B3", "NCT-B", 320, 13.0)]
berths = []
for b, term, length, depth in berth_defs:
    calls = [v for v in vessels if v["planned_berth"] == b]
    calls.sort(key=lambda v: v["eta_offset_h"])
    berths.append({"berth": b, "terminal": term, "length_m": length, "depth_m": depth,
                   "status": "occupied" if any(v["status"] == "berthed" and v["berth"] == b for v in vessels) else "free",
                   "maintenance_note": "Fender replacement scheduled, 6h window" if b == "A3" else None,
                   "plan": [v["name"] for v in calls]})
# A3 has no calls & maintenance -> reduced availability; keep plan empty

cr_alloc = {"A1": ["QC01", "QC02", "QC03", "QC04"], "A2": ["QC05", "QC06"], "B1": ["QC09", "QC10", "QC11", "QC12"],
            "B2": ["QC13", "QC14"]}
cranes = []
berth_vessel = {v["berth"]: v["name"] for v in vessels if v["status"] == "berthed"}
for b, ids in cr_alloc.items():
    for cid in ids:
        cranes.append({"crane_id": cid, "berth": b, "vessel": berth_vessel[b], "status": "working",
                       "moves_per_hour": rnd.choice([26, 27, 28, 29, 30, 31, 32, 33]),
                       "moves_done": rnd.randint(300, 1400)})
cranes[5]["moves_per_hour"] = 14  # degraded QC10 (links to incident)
cranes[5]["status"] = "degraded"
cranes.append({"crane_id": "QC07", "berth": "A3", "vessel": None, "status": "maintenance", "moves_per_hour": 0, "moves_done": 0})
cranes.append({"crane_id": "QC08", "berth": "A3", "vessel": None, "status": "idle", "moves_per_hour": 0, "moves_done": 0})
cranes.sort(key=lambda c: c["crane_id"])

# ---- containers ----------------------------------------------------------
owners = ["BALU", "HFCU", "MOCU", "PBSU", "MSCX"]
carrier_owner = {"Baltic Arrow Lines": "BAL", "Hanseatic Feeder Co": "HFC", "Meridian Ocean Carriers": "MOC",
                 "Polar Bridge Shipping": "PBS"}
consignees = ["Rheinwerk Maschinenbau GmbH", "Fjordfrisch Foods AS", "Lindenhof Textilien KG", "Aurelia Pharma Logistik",
              "Seebrise Möbel GmbH", "Tidewater Auto Parts Ltd", "Kornblum Chemie AG", "Nordstern Elektronik GmbH",
              "Havenbrot Bakery Supply", "Weserblick Bikes GmbH"]
hold_reasons = ["Customs document check pending", "X-ray inspection scheduled", "Missing import declaration (ATLAS)",
                "Veterinary inspection required", "Duty payment outstanding"]
containers = []
used = set()
serial = 104200
non_departed = [v for v in vessels if v["status"] != "departed"]
for i in range(60):
    v = rnd.choice(vessels if i % 6 else non_departed)
    owner = carrier_owner[v["carrier"]]
    serial += rnd.randint(7, 61)
    cid = iso6346(owner, serial)
    typ = rnd.choices(["20GP", "40GP", "40HC", "40RF", "20TK"], [20, 25, 35, 15, 5])[0]
    st = v["status"]
    if st in ("en route", "at anchor"):
        status = "expected - on vessel"
    elif st == "berthed":
        status = rnd.choice(["discharged - in yard", "discharged - in yard", "on vessel - awaiting discharge", "released - ready for pickup"])
    else:
        status = rnd.choice(["loaded - vessel departed", "gated out", "loaded - vessel departed"])
    in_yard = status in ("discharged - in yard", "released - ready for pickup")
    reefer = typ == "40RF"
    hold = in_yard and rnd.random() < 0.3 or (status == "expected - on vessel" and rnd.random() < 0.15)
    containers.append({
        "container_id": cid, "size_type": typ, "status": status, "vessel": v["name"],
        "yard": ({"block": rnd.choice(["A", "B", "C", "D", "R"] if not reefer else ["R"]) + str(rnd.randint(1, 9)),
                  "row": rnd.randint(1, 24), "tier": rnd.randint(1, 5),
                  "terminal": v["planned_berth"][0] and ("NCT-" + v["planned_berth"][0])} if in_yard else None),
        "customs_hold": bool(hold),
        "customs_reason": rnd.choice(hold_reasons) if hold else None,
        "reefer_temp_c": rnd.choice([-18.0, -18.0, -20.0, 2.0, 4.0, 5.5]) if reefer else None,
        "weight_kg": rnd.randint(4200, 28500),
        "consignee": rnd.choice(consignees),
        "commodity": rnd.choice(["machinery parts", "frozen fish", "apparel", "pharmaceuticals", "furniture", "auto spare parts",
                                 "industrial chemicals", "consumer electronics", "bakery supplies", "bicycles"]),
    })
    used.add(cid)
# make sure a reefer is present on yard and a couple of showcase containers exist
showcase = containers[:3]
showcase[0].update(status="discharged - in yard", customs_hold=True, customs_reason="Customs document check pending",
                   yard={"block": "B4", "row": 12, "tier": 2, "terminal": "NCT-A"})

# ---- truck slots ---------------------------------------------------------
slots = []
for day in (0, 1, 2):
    for term in ("NCT-A", "NCT-B"):
        for hour in range(6, 22, 2):
            cap = 24 if term == "NCT-A" else 18
            booked = min(cap, int(cap * rnd.choice([0.3, 0.5, 0.7, 0.9, 1.0])))
            slots.append({"slot_id": f"TS-{term[-1]}-D{day}-{hour:02d}", "terminal": term, "day_offset": day,
                          "start_hour": hour, "end_hour": hour + 2, "capacity": cap, "booked": booked})

# ---- incident logs -------------------------------------------------------
logs = []


def log(minutes_ago, system, level, component, msg, incident=None):
    logs.append({"minutes_ago": minutes_ago, "system": system, "level": level, "component": component,
                 "message": msg, "incident_id": incident})


# Hand-crafted incidents' logs
for m, msg in [(190, "Upstream timeout after 30000ms calling model endpoint aif-portops /openai/deployments/gpt-4.1/chat/completions"),
               (188, "HTTP 500 from model endpoint: InternalServerError (x-request-id 7f3a-19c2)"),
               (185, "HTTP 500 from model endpoint: InternalServerError (x-request-id 7f3a-19c8)"),
               (184, "Retry 1/3 failed: HTTP 500 from model endpoint"),
               (181, "Circuit breaker OPEN for backend model-endpoint-primary (3 failures in 60s)"),
               (176, "Upstream timeout after 30000ms calling model endpoint"),
               (170, "Circuit breaker HALF-OPEN, probing model-endpoint-primary"),
               (169, "Circuit breaker CLOSED, backend recovered")]:
    log(m, "API-GATEWAY", "ERROR" if "CLOSED" not in msg and "HALF" not in msg else "INFO", "llm-proxy", msg, "INC-1001")
for m in (95, 94, 61):
    log(m, "API-GATEWAY", "ERROR", "llm-proxy",
        f"HTTP 500 llm-proxy -> model-endpoint: backend returned 500 after {rnd.randint(2, 9)}.{rnd.randint(0, 9)}s (x-request-id {rnd.randint(1000, 9999):x}-{rnd.randint(100, 999):x})", "INC-1002")
for m in (40, 38, 12):
    log(m, "API-GATEWAY", "WARN", "llm-proxy", "HTTP 429 Too Many Requests: token quota exceeded for subscription dept-it, retry-after 30s")
for m, msg in [(410, "OCR confidence 0.41 below threshold on lane 3 camera (plate unreadable)"),
               (409, "Lane 3 OCR retry failed, manual review required"),
               (402, "Lane 3 OCR service restarted by watchdog"),
               (398, "Lane 3 OCR confidence back to 0.97")]:
    log(m, "GATE-OCR", "ERROR" if "below" in msg or "failed" in msg else "INFO", "ocr-lane3", msg, "INC-1003")
for m, msg in [(300, "QC10 hoist drive fault code F-217, speed reduced to 50%"),
               (299, "QC10 spreader twistlock sensor timeout"),
               (290, "QC10 operating in degraded mode, maintenance ticket MT-5521 opened"),
               (120, "QC10 still degraded, moves/hour 14 vs plan 30")]:
    log(m, "CRANE-PLC", "ERROR" if "fault" in msg or "timeout" in msg else "WARN", "qc10-plc", msg, "INC-1004")
for m, msg in [(520, "EDI COPRAR from Meridian Ocean Carriers rejected: segment BGM invalid"),
               (515, "EDI COPRAR retry rejected: duplicate message reference"),
               (505, "EDI COPRAR accepted after carrier resend")]:
    log(m, "EDI", "ERROR" if "rejected" in msg else "INFO", "edi-gateway", msg, "INC-1005")
for m, msg in [(230, "Yard planning deadlock detected for block B4, 12 move orders blocked"),
               (228, "TOS job queue depth 1,840 (threshold 1,000)"),
               (222, "TOS rebuilt B4 stack plan, queue draining"),
               (210, "TOS job queue depth 220")]:
    log(m, "TOS", "ERROR" if "deadlock" in msg or "depth 1,840" in msg else "INFO", "yard-planner", msg, "INC-1006")
for m, msg in [(75, "Truck appointment API latency p95 4.8s (SLO 1.5s)"),
               (73, "Gate system slot sync lag 6 min for terminal NCT-B"),
               (65, "Slot sync caught up")]:
    log(m, "TOS", "WARN" if "Truck" in msg or "lag" in msg else "INFO", "appointments", msg, "INC-1007")
for m, msg in [(55, "Reefer monitoring: R3-14 temperature excursion +3.2C above setpoint"),
               (50, "Reefer R3-14 plugged in, temperature recovering"),
               (35, "Reefer R3-14 back within tolerance")]:
    log(m, "TOS", "WARN" if "excursion" in msg else "INFO", "reefer-monitor", msg, "INC-1008")

# Background noise
noise = [("TOS", "INFO", "yard-planner", "Stack plan optimisation completed for block {b} in {n} ms"),
         ("TOS", "INFO", "vessel-planner", "Berth window updated for vessel, delta {n} min"),
         ("GATE-OCR", "INFO", "ocr-lane{l}", "Container ID read OK, confidence 0.9{d}"),
         ("GATE-OCR", "WARN", "ocr-lane{l}", "OCR confidence {c} on lane {l}, accepted with warning"),
         ("CRANE-PLC", "INFO", "qc{q}-plc", "Heartbeat OK, spreader locked, load {n} kg"),
         ("EDI", "INFO", "edi-gateway", "BAPLIE received and parsed ({n} segments)"),
         ("EDI", "INFO", "edi-gateway", "CODECO sent to carrier, ack received"),
         ("API-GATEWAY", "INFO", "llm-proxy", "POST /openai/deployments/gpt-4.1-mini/chat/completions 200 in {n} ms"),
         ("API-GATEWAY", "INFO", "llm-proxy", "POST /openai/deployments/gpt-4.1/chat/completions 200 in {n} ms")]
for i in range(48):
    s, lvl, comp, tpl = rnd.choice(noise)
    msg = tpl.format(b=rnd.choice("ABCD") + str(rnd.randint(1, 9)), n=rnd.randint(40, 2400), l=rnd.randint(1, 6),
                     d=rnd.randint(0, 9), c=round(rnd.uniform(0.6, 0.79), 2), q=f"{rnd.randint(1, 14):02d}")
    comp = comp.format(l=rnd.randint(1, 6), q=f"{rnd.randint(1, 14):02d}") if "{" in comp else comp
    log(rnd.randint(5, 1400), s, lvl, comp, msg)
logs.sort(key=lambda x: -x["minutes_ago"])
for i, l in enumerate(logs, 1):
    l["log_id"] = f"LOG-{i:04d}"

# ---- incidents -----------------------------------------------------------
incidents = [
    {"incident_id": "INC-1001", "title": "LLM proxy to model endpoint: HTTP 500s and timeouts", "system": "API-GATEWAY",
     "severity": "SEV2", "status": "resolved", "opened_minutes_ago": 190, "resolved_minutes_ago": 169,
     "owner": "IT Platform", "root_cause": "Model endpoint backend instability; circuit breaker opened and recovered.",
     "impact": "Operations assistant answers failed or were delayed for ~20 minutes.",
     "next_steps": "Add backend pool with secondary deployment; keep retry with backoff."},
    {"incident_id": "INC-1002", "title": "Intermittent 500 llm-proxy -> model-endpoint", "system": "API-GATEWAY",
     "severity": "SEV3", "status": "investigating", "opened_minutes_ago": 95, "resolved_minutes_ago": None,
     "owner": "IT Platform", "root_cause": "Unknown; correlate x-request-id between gateway logs and model endpoint logs.",
     "impact": "~2% of agent requests return 500.", "next_steps": "Trace by request id across gateway and Foundry diagnostics."},
    {"incident_id": "INC-1003", "title": "Gate lane 3 OCR low confidence", "system": "GATE-OCR", "severity": "SEV3",
     "status": "resolved", "opened_minutes_ago": 410, "resolved_minutes_ago": 398, "owner": "Gate Systems",
     "root_cause": "Camera lens fogging at dawn; watchdog restart cleared the stall.", "impact": "Manual review at lane 3, queue of 9 trucks.",
     "next_steps": "Schedule lens heater check."},
    {"incident_id": "INC-1004", "title": "Quay crane QC10 degraded (hoist drive fault)", "system": "CRANE-PLC", "severity": "SEV2",
     "status": "monitoring", "opened_minutes_ago": 300, "resolved_minutes_ago": None, "owner": "Engineering",
     "root_cause": "Hoist drive fault F-217, spreader sensor timeouts.", "impact": "Berth B1 productivity down ~45% on one crane.",
     "next_steps": "Replace drive module at next shift change (MT-5521)."},
    {"incident_id": "INC-1005", "title": "EDI COPRAR rejected for Meridian Ocean Carriers", "system": "EDI", "severity": "SEV3",
     "status": "resolved", "opened_minutes_ago": 520, "resolved_minutes_ago": 505, "owner": "Integration",
     "root_cause": "Carrier sent malformed BGM segment; resend accepted.", "impact": "Load list delayed 15 minutes.",
     "next_steps": "Share schema validation report with carrier."},
    {"incident_id": "INC-1006", "title": "TOS yard planning deadlock in block B4", "system": "TOS", "severity": "SEV2",
     "status": "resolved", "opened_minutes_ago": 230, "resolved_minutes_ago": 210, "owner": "TOS Support",
     "root_cause": "Circular move dependency after late stack plan change.", "impact": "12 move orders blocked, queue peaked at 1,840 jobs.",
     "next_steps": "Patch planner dependency check."},
    {"incident_id": "INC-1007", "title": "Truck appointment API latency", "system": "TOS", "severity": "SEV3",
     "status": "resolved", "opened_minutes_ago": 75, "resolved_minutes_ago": 65, "owner": "TOS Support",
     "root_cause": "DB index bloat on appointments table.", "impact": "Slot booking slow for NCT-B hauliers.",
     "next_steps": "Reindex during maintenance window."},
    {"incident_id": "INC-1008", "title": "Reefer R3-14 temperature excursion", "system": "TOS", "severity": "SEV3",
     "status": "resolved", "opened_minutes_ago": 55, "resolved_minutes_ago": 35, "owner": "Yard Operations",
     "root_cause": "Power plug partially disconnected.", "impact": "Container held +3.2C for ~15 minutes, within cargo tolerance.",
     "next_steps": "Add plug-check step to reefer SOP."},
]
for inc in incidents:
    inc["related_log_ids"] = [l["log_id"] for l in logs if l["incident_id"] == inc["incident_id"]]

for name, data in [("vessels", vessels), ("berths", berths), ("cranes", cranes), ("containers", containers),
                   ("truck_slots", slots), ("incident_logs", logs), ("incidents", incidents)]:
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
print({k: len(v) for k, v in [("vessels", vessels), ("containers", containers), ("logs", logs), ("slots", slots)]})
