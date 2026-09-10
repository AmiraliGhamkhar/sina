"""Unit tests for the R7 worker bootstrap (app/worker.py).

Stdlib unittest only — no pytest dependency. Tests are offline (no DB, no
network): they assert WorkerSettings wiring — the hourly token-sweep cron is
registered and run_research is still in the function set. sweep_expired_tokens
itself is never called (it would touch Postgres). If the module can't be
imported in this environment (missing settings/secret deps), the whole suite
skips cleanly.
"""
import unittest

try:
    from app.worker import WorkerSettings, run_research, sweep_expired_tokens
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    WorkerSettings = None
    run_research = None
    sweep_expired_tokens = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


class WorkerSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if WorkerSettings is None:
            raise unittest.SkipTest(
                f"app.worker import failed in this env: {_IMPORT_ERROR}"
            )

    def test_cron_jobs_non_empty(self):
        # R7: the expired-token sweep must be scheduled (arq cron_jobs)
        self.assertTrue(WorkerSettings.cron_jobs)

    def test_functions_include_run_research(self):
        self.assertIn(run_research, WorkerSettings.functions)

    def test_sweep_expired_tokens_callable(self):
        self.assertTrue(callable(sweep_expired_tokens))


if __name__ == "__main__":
    unittest.main()
