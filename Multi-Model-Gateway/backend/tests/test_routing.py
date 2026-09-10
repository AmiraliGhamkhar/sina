"""Unit tests for app.services.provider_router (resolve_role + ProviderRouter).

Stdlib unittest only — no pytest dependency. Tests are offline (no DB, no
network): resolve_role is a pure function over the request shape, and
ProviderRouter.resolve tests mock the registry (get_default_provider /
row_to_provider) and the DB session so no real rows or connections are needed.
If the module can't be imported in this environment (missing settings/secret
deps), the whole suite skips cleanly.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

try:
    from app.services.provider_router import ProviderRouter, resolve_role
    from app.services.router import ChatRequest, Provider
    from app.services.providers import OpenAICompatProvider
    from app.services.provider_registry import ProviderConfigError
except Exception as exc:  # noqa: BLE001 — env may lack required settings
    ProviderRouter = None
    resolve_role = None
    ChatRequest = None
    Provider = None
    OpenAICompatProvider = None
    ProviderConfigError = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


def _req(**overrides) -> ChatRequest:
    payload = {
        "messages": [{"role": "user", "content": "hello there"}],
        "model": "auto",
        "stream": True,
        "provider": "auto",
        "private": False,
    }
    payload.update(overrides)
    return ChatRequest(**payload)


class ResolveRoleTests(unittest.TestCase):
    """Pure heuristic: which role handles the request. No DB, no IO."""

    @classmethod
    def setUpClass(cls):
        if resolve_role is None:
            raise unittest.SkipTest(
                f"app.services.provider_router import failed in this env: {_IMPORT_ERROR}"
            )

    def test_private_is_local(self):
        self.assertEqual(resolve_role(text="hello", provider_choice="auto", is_private=True, message_count=2), "local")

    def test_provider_local_is_local(self):
        self.assertEqual(resolve_role(text="hello", provider_choice=Provider.local, is_private=False, message_count=2), "local")

    def test_provider_openrouter_is_cloud(self):
        self.assertEqual(resolve_role(text="hello", provider_choice=Provider.openrouter, is_private=False, message_count=2), "cloud")

    def test_coding_keyword_is_cloud(self):
        self.assertEqual(resolve_role(text="write me a python script", provider_choice="auto", is_private=False, message_count=2), "cloud")

    def test_private_beats_coding_keyword(self):
        """First match wins: private short-circuits the coding heuristic."""
        self.assertEqual(resolve_role(text="fix this code", provider_choice="auto", is_private=True, message_count=2), "local")

    def test_many_messages_is_cloud(self):
        self.assertEqual(resolve_role(text="hello", provider_choice="auto", is_private=False, message_count=81), "cloud")

    def test_default_is_local(self):
        self.assertEqual(resolve_role(text="hello", provider_choice="auto", is_private=False, message_count=2), "local")

    def test_code_keyword_list_matches_cloud(self):
        """The keyword list is exactly the coding-trigger set — any member routes cloud."""
        for kw in ("script", "code", "function", "debug", "bug", "python", "c++", "java", "javascript", "typescript"):
            with self.subTest(kw=kw):
                self.assertEqual(resolve_role(text=f"write {kw}", provider_choice="auto", is_private=False, message_count=2), "cloud")


class _FakeResult:
    """Mimics a SQLAlchemy AsyncResult enough for scalar_one_or_none()."""

    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeDB:
    """Mimics an AsyncSession for the pinned-provider lookup only."""

    def __init__(self, row):
        self._row = row

    async def execute(self, *args, **kwargs):
        return _FakeResult(self._row)


# A fake Provider row that satisfies every attribute the routing code touches.
def _row(**overrides) -> SimpleNamespace:
    base = dict(
        id="prov-1",
        name="Local (LM Studio)",
        user_id="user-1",
        type="openai_compatible",
        role="local",
        base_url="http://host:1234",
        default_model="local-model",
        api_key_encrypted=None,
        is_default=True,
        enabled=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class ProviderRouterTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if ProviderRouter is None:
            raise unittest.SkipTest(
                f"app.services.provider_router import failed in this env: {_IMPORT_ERROR}"
            )

    async def test_legacy_local_fallback_uses_env_and_normalizes_v1(self):
        """No configured rows → env-var OpenAICompatProvider with /v1 appended."""
        with patch("app.services.provider_router.get_default_provider", new=AsyncMock(return_value=None)):
            r = await ProviderRouter().resolve(
                _req(provider=Provider.local, model="auto"), "user-1", None
            )
        self.assertIsInstance(r.provider, OpenAICompatProvider)
        self.assertEqual(r.model, r.provider.default_model)
        self.assertEqual(r.role, "local")

    async def test_default_row_path_uses_row(self):
        """A configured default row supplies the adapter, model, and role."""
        dummy = SimpleNamespace(name="dummy-provider")
        with (
            patch("app.services.provider_router.get_default_provider", new=AsyncMock(return_value=_row())),
            patch("app.services.provider_router.row_to_provider", return_value=dummy),
        ):
            r = await ProviderRouter().resolve(
                _req(provider=Provider.local, model="auto"), "user-1", None
            )
        self.assertIs(r.provider, dummy)
        self.assertEqual(r.model, "local-model")
        self.assertEqual(r.role, "local")

    async def test_pinned_row_without_default_model_auto_raises(self):
        """Pinned row + model 'auto' + no default_model → ProviderConfigError."""
        no_default = _row(id="prov-2", name="No Default Model", default_model=None)
        fake_db = _FakeDB(no_default)
        with self.assertRaises(ProviderConfigError) as ctx:
            await ProviderRouter().resolve(_req(provider_id="prov-2", model="auto"), "user-1", fake_db)
        self.assertIn("no default model", str(ctx.exception))

    async def test_pinned_row_explicit_model_is_used_as_is(self):
        """Pinned row + explicit model + no default_model → no raise, model as given."""
        no_default = _row(id="prov-2", name="No Default Model", default_model=None)
        fake_db = _FakeDB(no_default)
        dummy = SimpleNamespace(name="dummy-provider")
        with patch("app.services.provider_router.row_to_provider", return_value=dummy):
            r = await ProviderRouter().resolve(
                _req(provider_id="prov-2", model="explicit-model"), "user-1", fake_db
            )
        self.assertIs(r.provider, dummy)
        self.assertEqual(r.model, "explicit-model")
        self.assertEqual(r.role, "local")

    async def test_default_row_without_default_model_auto_raises(self):
        """Per-role default row + model 'auto' + no default_model → ProviderConfigError."""
        no_default = _row(id="prov-3", name="Cloud No Default", default_model=None, role="cloud")
        with (
            patch("app.services.provider_router.get_default_provider", new=AsyncMock(return_value=no_default)),
            patch("app.services.provider_router.row_to_provider", return_value=SimpleNamespace()),
        ):
            with self.assertRaises(ProviderConfigError) as ctx:
                await ProviderRouter().resolve(_req(provider=Provider.openrouter, model="auto"), "user-1", None)
        self.assertIn("no default model", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
