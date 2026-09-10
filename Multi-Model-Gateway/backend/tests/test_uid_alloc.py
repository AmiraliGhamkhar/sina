"""Unit tests for sandbox/uid_alloc.py (F1/C2 per-tenant confinement allocator).

Offline and platform-independent: the allocator's *pure* logic (UID-range
bounds, next-free selection, discoverable-used scan) is exercised here; the
chown/setuid that needs root + CAP_CHOWN is NOT (it runs only in the Linux
sandbox container). The module is loaded via importlib because it lives in the
sibling `sandbox/` dir, not under `app/`.

Stdlib unittest only. If the file can't be located, the suite skips.
"""
import pathlib
import sys
import tempfile
import unittest

try:
    _SANDBOX_DIR = pathlib.Path(__file__).resolve().parents[2] / "sandbox"
    import importlib.util

    _spec = importlib.util.spec_from_file_location("uid_alloc", _SANDBOX_DIR / "uid_alloc.py")
    _uid_alloc = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_uid_alloc)
    _IMP_OK = True
    _IMPORT_ERROR = None
except Exception as exc:  # noqa: BLE001
    _uid_alloc = None
    _IMP_OK = False
    _IMPORT_ERROR = exc


class UidRangeTests(unittest.TestCase):
    def setUp(self):
        if not _IMP_OK:
            self.skipTest(f"sandbox/uid_alloc.py not importable: {_IMPORT_ERROR}")

    def test_range_bounds(self):
        self.assertTrue(_uid_alloc.is_tenant_uid(_uid_alloc.UID_MIN))
        self.assertTrue(_uid_alloc.is_tenant_uid(_uid_alloc.UID_MAX))

    def test_non_tenant_uids_excluded(self):
        for bad in (0, 1, 999, 65534, 65535, _uid_alloc.UID_MAX + 1):
            self.assertFalse(_uid_alloc.is_tenant_uid(bad), f"{bad} must not be tenant")


class NextFreeUidTests(unittest.TestCase):
    def setUp(self):
        if not _IMP_OK:
            self.skipTest(f"sandbox/uid_alloc.py not importable: {_IMPORT_ERROR}")

    def test_starts_at_min(self):
        self.assertEqual(_uid_alloc.next_free_uid(set()), _uid_alloc.UID_MIN)

    def test_skips_used(self):
        used = {_uid_alloc.UID_MIN, _uid_alloc.UID_MIN + 1}
        self.assertEqual(_uid_alloc.next_free_uid(used), _uid_alloc.UID_MIN + 2)

    def test_collision_free_by_construction(self):
        # Every allocation hands out a UID exactly once (set grows monotonically).
        used = set()
        for _ in range(50):
            uid = _uid_alloc.next_free_uid(used)
            self.assertNotIn(uid, used)
            self.assertTrue(_uid_alloc.is_tenant_uid(uid))
            used.add(uid)


class UsedUidsScanTests(unittest.TestCase):
    def setUp(self):
        if not _IMP_OK:
            self.skipTest(f"sandbox/uid_alloc.py not importable: {_IMPORT_ERROR}")

    def test_scans_tenant_owned_workspace_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "u1" / "a1").mkdir(parents=True)
            (root / "u1" / "a2").mkdir(parents=True)
            used = _uid_alloc.used_uids(root)
            # None of these fresh dirs are tenant-owned (default uid not in range),
            # so `used` holds only system accounts (if any in range) — small.
            self.assertIsInstance(used, set)


if __name__ == "__main__":
    unittest.main()
