"""Department -> tool name mapping (server-side enforcement; mirror this in Foundry allowed_tools)."""

OPS_TOOLS = ["get_vessel_schedule", "get_berth_plan", "get_crane_assignments"]
CONTAINER_TOOLS = ["track_container", "get_yard_position", "check_customs_hold", "list_truck_slots", "book_truck_slot"]
IT_TOOLS = ["search_incident_logs", "get_incident", "summarize_errors"]

DEPARTMENT_TOOLS = {
    "ops": OPS_TOOLS + CONTAINER_TOOLS,
    "it": IT_TOOLS,
    "cs": CONTAINER_TOOLS,
}
ALL_TOOL_NAMES = OPS_TOOLS + CONTAINER_TOOLS + IT_TOOLS
