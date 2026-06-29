"""
prompt_router.py
Routes (role, mode_id) → (system_prompt, mode_prompt).
Super Admin can run any role's modes for testing via admin panel.
"""

from prompts.detailer_prompts   import DETAILER_MODES,   DETAILER_SYSTEM
from prompts.fabricator_prompts import FABRICATOR_MODES, FABRICATOR_SYSTEM
from prompts.shared_rules       import GLOBAL_FORMAT_RULES  # noqa: F401

ROLE_MAP = {
    "detailer":   (DETAILER_SYSTEM,   DETAILER_MODES),
    "fabricator": (FABRICATOR_SYSTEM, FABRICATOR_MODES),
}


def get_system_prompt(role: str) -> str:
    if role == "super_admin":
        return "You are SteelSight — full-access admin mode. You may run any role's modes for testing and review."
    system, _ = ROLE_MAP.get(role, (None, None))
    if not system:
        raise ValueError(f"Unknown role: {role!r}")
    return system


def get_mode_prompt(role: str, mode_id: str) -> str:
    if role == "super_admin":
        # Admin can test any role's mode
        for _, modes in ROLE_MAP.values():
            if mode_id in modes:
                return modes[mode_id]["prompt"]
        raise ValueError(f"Mode {mode_id!r} not found in any role.")
    _, modes = ROLE_MAP.get(role, (None, None))
    if modes is None:
        raise ValueError(f"Unknown role: {role!r}")
    if mode_id not in modes:
        raise ValueError(f"Mode {mode_id!r} not available for role {role!r}")
    return modes[mode_id]["prompt"]


def get_mode_meta(mode_id: str) -> dict | None:
    """Return the full mode metadata (label, group, description, icon, time) from any role."""
    for role, (_, modes) in ROLE_MAP.items():
        if mode_id in modes:
            m = modes[mode_id]
            return {
                "id": mode_id,
                "role": role,
                "label": m["label"],
                "group": m["group"],
                "description": m["description"],
                "icon": m["icon"],
                "time": m["time"],
            }
    return None


def list_all_modes_flat() -> list[dict]:
    """Used by Super Admin to see every mode across all roles."""
    result = []
    for role, (_, modes) in ROLE_MAP.items():
        for mid, m in modes.items():
            result.append({
                "id": mid, "role": role,
                "label": m["label"], "group": m["group"],
                "description": m["description"],
                "icon": m["icon"], "time": m["time"],
            })
    return result


def list_modes_for_role(role: str) -> list[dict]:
    """Used by dashboard mode selector — returns only this role's modes."""
    if role == "super_admin":
        return list_all_modes_flat()
    _, modes = ROLE_MAP.get(role, (None, {}))
    return [
        {"id": k, "label": v["label"], "group": v["group"],
         "description": v["description"], "icon": v["icon"], "time": v["time"], "role": role}
        for k, v in (modes or {}).items()
    ]
