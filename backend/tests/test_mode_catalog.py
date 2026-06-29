"""
Backend tests for iteration 5: 32-mode catalog + per-role isolation + prompt routing.
"""
import os
import sys
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://steel-intel-1.preview.emergentagent.com").rstrip("/")

SUPER_ADMIN_EMAIL = "niks.shah236@gmail.com"
SUPER_ADMIN_PASSWORD = "Nikunj@1405"

SHARED_15 = [
    "MASTER_INTAKE", "PHASE_1", "PHASE_2", "PHASE_3", "SUMMARIZER",
    "ISSUE_DETECTOR", "MTO", "LANDSCAPE_SPECIALIST", "BID_STRATEGY",
    "POST_AWARD_RISK_TRACKER", "DRAWING_SUBMISSION_SCHEDULE",
    "INTERNAL_SCHEDULE_PLANNER", "CHAT_ASSISTANT", "DRAWING_CHECKER",
    "CNC_FILE_CHECKER",
]
EXPECTED_GROUPS = {
    "Intake & Index", "Engineering Review", "Estimation & Bid",
    "Take-off", "Quality & Checking", "Schedule & Planning",
    "Post-Award", "Quick Tools",
}


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": SUPER_ADMIN_EMAIL, "password": SUPER_ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def admin_h(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ── Catalog tests ───────────────────────────────────────────────────────
class TestModeCatalog:
    def test_all_modes_returns_32(self, admin_h):
        r = requests.get(f"{BASE_URL}/api/admin/modes/all", headers=admin_h)
        assert r.status_code == 200, r.text
        data = r.json()
        modes = data if isinstance(data, list) else data.get("modes", data.get("items", []))
        assert len(modes) == 32, f"Expected 32 modes, got {len(modes)}: {modes[:2]}"

    def test_partition_16_16_by_role(self, admin_h):
        r = requests.get(f"{BASE_URL}/api/admin/modes/all", headers=admin_h)
        data = r.json()
        modes = data if isinstance(data, list) else data.get("modes", data.get("items", []))
        det = [m for m in modes if m.get("role") == "detailer"]
        fab = [m for m in modes if m.get("role") == "fabricator"]
        assert len(det) == 16, f"detailer={len(det)}"
        assert len(fab) == 16, f"fabricator={len(fab)}"

    def test_eight_groups(self, admin_h):
        r = requests.get(f"{BASE_URL}/api/admin/modes/all", headers=admin_h)
        data = r.json()
        modes = data if isinstance(data, list) else data.get("modes", data.get("items", []))
        groups = {m["group"] for m in modes}
        assert groups == EXPECTED_GROUPS, f"Got groups: {groups}"

    def test_detailer_estimation_mode(self, admin_h):
        r = requests.get(f"{BASE_URL}/api/admin/modes/all", headers=admin_h)
        modes = r.json() if isinstance(r.json(), list) else r.json().get("modes", [])
        det_est = [m for m in modes if m.get("role") == "detailer" and m["id"] == "ESTIMATION_PRO"]
        assert len(det_est) == 1
        assert "Hours-Based" in det_est[0]["label"] or "Hours" in det_est[0]["label"], det_est[0]["label"]

    def test_fabricator_estimation_mode(self, admin_h):
        r = requests.get(f"{BASE_URL}/api/admin/modes/all", headers=admin_h)
        modes = r.json() if isinstance(r.json(), list) else r.json().get("modes", [])
        fab_est = [m for m in modes if m.get("role") == "fabricator" and m["id"] == "FABRICATOR_ESTIMATION_PRO"]
        assert len(fab_est) == 1
        assert "Tonnage" in fab_est[0]["label"], fab_est[0]["label"]


# ── Prompt identity test via direct import ──────────────────────────────
class TestPromptIdentity:
    def test_15_shared_prompts_identical(self):
        sys.path.insert(0, "/app/backend")
        from prompts.detailer_prompts import DETAILER_MODES
        from prompts.fabricator_prompts import FABRICATOR_MODES
        from prompts.prompt_router import get_mode_prompt

        for mode_id in SHARED_15:
            d = get_mode_prompt("detailer", mode_id)
            f = get_mode_prompt("fabricator", mode_id)
            assert d == f, f"Prompt mismatch for {mode_id}"
            assert DETAILER_MODES[mode_id]["prompt"] == FABRICATOR_MODES[mode_id]["prompt"]


# ── Per-user mode isolation ────────────────────────────────────────────
class TestUserPermissions:
    @pytest.fixture(scope="class")
    def detailer_user(self, admin_h):
        uid_suffix = uuid.uuid4().hex[:8]
        payload = {
            "email": f"test_detailer_{uid_suffix}@example.com",
            "password": "TestPass1!",
            "first_name": "TEST",
            "last_name": "Detailer",
            "role": "detailer",
        }
        r = requests.post(f"{BASE_URL}/api/admin/users", json=payload, headers=admin_h)
        assert r.status_code in (200, 201), r.text
        user = r.json()
        uid = user.get("id") or user.get("user_id") or user.get("_id") or user.get("user", {}).get("id")
        assert uid, f"No user id in: {user}"
        yield {"id": uid, "email": payload["email"], "password": payload["password"]}
        # cleanup
        requests.delete(f"{BASE_URL}/api/admin/users/{uid}", headers=admin_h)

    @pytest.fixture(scope="class")
    def fabricator_user(self, admin_h):
        uid_suffix = uuid.uuid4().hex[:8]
        payload = {
            "email": f"test_fab_{uid_suffix}@example.com",
            "password": "TestPass1!",
            "first_name": "TEST",
            "last_name": "Fab",
            "role": "fabricator",
        }
        r = requests.post(f"{BASE_URL}/api/admin/users", json=payload, headers=admin_h)
        assert r.status_code in (200, 201), r.text
        user = r.json()
        uid = user.get("id") or user.get("user_id") or user.get("_id") or user.get("user", {}).get("id")
        yield {"id": uid, "email": payload["email"], "password": payload["password"]}
        requests.delete(f"{BASE_URL}/api/admin/users/{uid}", headers=admin_h)

    def _login(self, email, pw):
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": pw})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def test_fresh_detailer_sees_no_modes(self, detailer_user):
        h = self._login(detailer_user["email"], detailer_user["password"])
        r = requests.get(f"{BASE_URL}/api/analyses/modes", headers=h)
        assert r.status_code == 200, r.text
        data = r.json()
        modes = data.get("modes", [])
        groups = data.get("groups", [])
        assert len(modes) == 0, f"Locked detailer should see 0 modes, got {len(modes)}"
        assert len(groups) == 0, f"Locked detailer should see 0 groups, got {groups}"

    def test_enable_three_modes_for_detailer(self, admin_h, detailer_user):
        # enable MASTER_INTAKE, ESTIMATION_PRO, MTO
        new_perms = {
            "allowedModes": ["MASTER_INTAKE", "ESTIMATION_PRO", "MTO"],
        }
        r = requests.put(
            f"{BASE_URL}/api/admin/users/{detailer_user['id']}/permissions",
            json=new_perms, headers=admin_h,
        )
        assert r.status_code == 200, r.text

        # Verify permissions persisted
        g = requests.get(f"{BASE_URL}/api/admin/users/{detailer_user['id']}/permissions", headers=admin_h)
        assert g.status_code == 200, g.text
        perms = g.json()
        allowed = perms.get("allowedModes") or perms.get("feature_permissions", {}).get("allowedModes")
        assert set(allowed) == {"MASTER_INTAKE", "ESTIMATION_PRO", "MTO"}, allowed

        # Now login as detailer & list modes
        h = self._login(detailer_user["email"], detailer_user["password"])
        r = requests.get(f"{BASE_URL}/api/analyses/modes", headers=h)
        data = r.json()
        mode_ids = {m["id"] for m in data.get("modes", [])}
        assert mode_ids == {"MASTER_INTAKE", "ESTIMATION_PRO", "MTO"}, mode_ids
        groups = data.get("groups", [])
        # 3 groups: Intake & Index, Estimation & Bid, Take-off
        assert set(groups) == {"Intake & Index", "Estimation & Bid", "Take-off"}, groups

    def test_cross_role_id_isolation(self, admin_h, detailer_user):
        # try to enable FABRICATOR_ESTIMATION_PRO for a detailer
        r = requests.put(
            f"{BASE_URL}/api/admin/users/{detailer_user['id']}/permissions",
            json={"allowedModes": ["FABRICATOR_ESTIMATION_PRO"]},
            headers=admin_h,
        )
        assert r.status_code == 200, f"Cross-role id save should pass at API level: {r.text}"

        h = self._login(detailer_user["email"], detailer_user["password"])
        r = requests.get(f"{BASE_URL}/api/analyses/modes", headers=h)
        modes = r.json().get("modes", [])
        # Should be filtered out by role catalog
        assert len(modes) == 0, f"Cross-role mode should not appear, got {modes}"

    def test_fabricator_two_modes(self, admin_h, fabricator_user):
        r = requests.put(
            f"{BASE_URL}/api/admin/users/{fabricator_user['id']}/permissions",
            json={"allowedModes": ["MTO", "FABRICATOR_ESTIMATION_PRO"]},
            headers=admin_h,
        )
        assert r.status_code == 200, r.text

        h = self._login(fabricator_user["email"], fabricator_user["password"])
        r = requests.get(f"{BASE_URL}/api/analyses/modes", headers=h)
        ids = {m["id"] for m in r.json().get("modes", [])}
        assert ids == {"MTO", "FABRICATOR_ESTIMATION_PRO"}, ids


# ── Super admin sees all 32 ─────────────────────────────────────────────
class TestSuperAdminModes:
    def test_super_admin_sees_all_32(self, admin_h):
        r = requests.get(f"{BASE_URL}/api/analyses/modes", headers=admin_h)
        assert r.status_code == 200, r.text
        modes = r.json().get("modes", [])
        assert len(modes) == 32, f"Super admin should see 32, got {len(modes)}"
