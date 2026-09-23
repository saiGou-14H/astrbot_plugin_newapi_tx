"""Offline regression contracts for NewApiCore (no plugin initialization).

Run with the project's dependencies available, for example:
    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp python -m unittest discover -s tests -v
"""
import importlib.util
from pathlib import Path
import sys
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

import aiosqlite
import httpx
from astrbot.api import AstrBotConfig


# Register the package path without executing __init__.py, which imports the
# complete bot plugin. Only the core under test is imported, never initialized.
if "compat_plugin" not in sys.modules:
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "compat_plugin", root / "__init__.py",
        submodule_search_locations=[str(root)],
    )
    if spec is None or spec.loader is None:
        raise ImportError("Cannot locate compat_plugin source package")
    sys.modules["compat_plugin"] = importlib.util.module_from_spec(spec)

from compat_plugin.newapi_utils import NewApiCore


class CoreIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Never construct AstrBotConfig through its disk-reading __init__.
        config = AstrBotConfig.__new__(AstrBotConfig)
        config.update({
            "binding_settings": {"quota_display_ratio": 100},
            "group_leave_settings": {"revert_group_on_leave": "default"},
        })
        self.core = NewApiCore(config)
        self.connection = await aiosqlite.connect(":memory:")
        self.addAsyncCleanup(self.connection.close)
        self.connection.row_factory = aiosqlite.Row
        self.core.db_mode = "sqlite"
        self.core.db_conn = self.connection
        for ddl in NewApiCore._TRANSFER_SQLITE_DDL.values():
            await self.connection.execute(ddl)
        await self.connection.commit()
        self.core.api_base_url = "https://offline.invalid"
        self.core.api_access_token = "Bearer synthetic-admin-token"
        self.core.api_admin_user_id = "999"

        # A swallowed AssertionError must still fail the test at cleanup.
        guard = patch.object(
            httpx.AsyncClient, "request", new_callable=AsyncMock,
            side_effect=AssertionError("Real HTTP is forbidden in core tests"),
        )
        self.blocked_http = guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(self.blocked_http.assert_not_awaited)

    async def seed_qq(self, qq_id=70001, site_id=13):
        await self.connection.execute(
            "INSERT INTO newapi_bindings (qq_id, website_user_id) VALUES (?, ?)",
            (qq_id, site_id),
        )
        await self.connection.commit()

    async def seed_openid(self, openid="synthetic-openid", site_id=13):
        await self.connection.execute(
            "INSERT INTO newapi_openid_bindings (openid, website_user_id) VALUES (?, ?)",
            (openid, site_id),
        )
        await self.connection.commit()

    async def assert_site_lookup(self, identifier, site_id=13):
        kind, binding = await self.core.lookup_binding(identifier)
        self.assertEqual(kind, "WEBSITE_ID")
        self.assertIsNotNone(binding)
        self.assertEqual(binding["website_user_id"], site_id)
        return binding

    async def test_lookup_openid_only_numeric_and_numeric_string(self):
        await self.seed_openid()
        for identifier in (13, "13"):
            with self.subTest(identifier=identifier):
                binding = await self.assert_site_lookup(identifier)
                self.assertEqual(binding["openid"], "synthetic-openid")
                self.assertNotIn("qq_id", binding)

    async def test_lookup_qq_only(self):
        await self.seed_qq()
        for identifier in (13, "13"):
            with self.subTest(identifier=identifier):
                binding = await self.assert_site_lookup(identifier)
                self.assertEqual(binding["qq_id"], 70001)

    async def test_lookup_dual_binding_preserves_qq_preference(self):
        await self.seed_qq()
        await self.seed_openid()
        for identifier in (13, "13"):
            with self.subTest(identifier=identifier):
                binding = await self.assert_site_lookup(identifier)
                self.assertEqual(binding["qq_id"], 70001)

    async def test_lookup_missing(self):
        for identifier in (13, "13", "qq:13", "site:13", "openid:missing"):
            with self.subTest(identifier=identifier):
                self.assertEqual(await self.core.lookup_binding(identifier), ("NOT_FOUND", None))

    async def test_text_numeric_prefers_site_over_qq_and_numeric_openid(self):
        await self.seed_openid(site_id=13)
        await self.seed_qq(qq_id=13, site_id=26)
        await self.seed_openid(openid="13", site_id=39)
        await self.assert_site_lookup("13")
        await self.assert_site_lookup(13)

    async def test_forced_prefixes_disambiguate_identity(self):
        await self.seed_openid(site_id=13)
        await self.seed_qq(qq_id=13, site_id=26)
        await self.seed_openid(openid="13", site_id=39)
        await self.assert_site_lookup("site:13")
        kind, binding = await self.core.lookup_binding("qq:13")
        self.assertEqual(kind, "QQ_ID")
        self.assertEqual(binding["website_user_id"], 26)
        for identifier, site_id in (("openid:13", 39), ("openid:synthetic-openid", 13)):
            with self.subTest(identifier=identifier):
                kind, binding = await self.core.lookup_binding(identifier)
                self.assertEqual(kind, "OPENID")
                self.assertEqual(binding["website_user_id"], site_id)

    async def test_prefixes_do_not_fall_back_to_other_namespaces(self):
        await self.seed_openid(site_id=13)
        await self.seed_qq(qq_id=26, site_id=39)
        for identifier in ("qq:13", "site:26", "openid:26"):
            with self.subTest(identifier=identifier):
                self.assertEqual(await self.core.lookup_binding(identifier), ("NOT_FOUND", None))

    async def test_get_user_by_website_id_remains_qq_table_only(self):
        await self.seed_openid()
        self.assertIsNone(await self.core.get_user_by_website_id(13))
        await self.seed_qq()
        binding = await self.core.get_user_by_website_id(13)
        self.assertEqual(binding["qq_id"], 70001)

    async def test_get_user_by_identity_never_falls_back_to_website(self):
        await self.seed_openid()
        await self.seed_qq()
        for identifier in (13, "13"):
            with self.subTest(identifier=identifier):
                self.assertIsNone(await self.core.get_user_by_identity(identifier))
        self.assertEqual((await self.core.get_user_by_identity("70001"))["website_user_id"], 13)
        self.assertEqual((await self.core.get_user_by_identity("synthetic-openid"))["website_user_id"], 13)

    async def test_adjust_openid_only_add_and_subtract(self):
        await self.seed_openid()
        for amount, mode, updated in ((1.25, "add", 1125), (-1.25, "subtract", 875)):
            with self.subTest(amount=amount):
                calls = []
                async def _mock_request(method, endpoint, json_data=None):
                    calls.append((method, endpoint, json_data))
                    if method == "GET":
                        return {"success": True, "data": {"id": 13, "quota": 1000 if len(calls) == 1 else updated}}
                    self.assertEqual((method, endpoint), ("POST", "/api/user/manage"))
                    self.assertEqual(json_data, {"id": 13, "action": "add_quota", "mode": mode, "value": 125})
                    return {"success": True}
                with patch.object(self.core, "api_request", side_effect=_mock_request):
                    status, details = await self.core.adjust_balance_by_identifier(13, amount)
                self.assertEqual(status, "SUCCESS")
                self.assertEqual(details["website_user_id"], 13)
                self.assertAlmostEqual(details["new_display_quota"], updated / 100)
                self.assertEqual([c[:2] for c in calls], [("GET", "/api/user/13"), ("POST", "/api/user/manage"), ("GET", "/api/user/13")])

    async def test_adjust_post_success_second_get_failure_is_applied(self):
        await self.seed_openid()
        calls = []
        async def _mock_request(method, endpoint, json_data=None):
            calls.append((method, endpoint, json_data))
            if method == "POST":
                return {"success": True}
            return {"success": True, "data": {"id": 13, "quota": 1000}} if len(calls) == 1 else None
        with patch.object(self.core, "api_request", side_effect=_mock_request):
            status, details = await self.core.adjust_balance_by_identifier(13, 1.25)
        self.assertEqual(status, "APPLIED_BALANCE_UNAVAILABLE")
        self.assertEqual(details["website_user_id"], 13)
        self.assertEqual([c[0] for c in calls], ["GET", "POST", "GET"])

    async def test_user_token_headers_and_expected_identity(self):
        for token in ("synthetic-user-token", "Bearer synthetic-user-token"):
            for returned_id in (13, 26):
                with self.subTest(token_form=token.startswith("Bearer"), returned_id=returned_id):
                    async def _mock_request(method, url, **kwargs):
                        self.assertEqual(method, "GET")
                        self.assertEqual(url, "https://offline.invalid/api/user/self")
                        headers = httpx.Headers(kwargs.get("headers", {}))
                        self.assertEqual(headers["New-Api-User"], "13")
                        self.assertEqual(headers["Authorization"], "Bearer synthetic-user-token")
                        self.assertNotIn("synthetic-admin-token", str(headers))
                        return httpx.Response(200, json={"success": True, "data": {"id": returned_id, "username": "synthetic"}}, request=httpx.Request(method, url))
                    with patch.object(httpx.AsyncClient, "request", side_effect=_mock_request) as request:
                        result = await self.core.get_self_by_user_token(token, expected_user_id=13)
                    request.assert_awaited_once()
                    # Validate outside the mock as well: the HTTP helper may
                    # catch exceptions, including assertions raised inside it.
                    sent_headers = httpx.Headers(request.await_args.kwargs.get("headers", {}))
                    self.assertEqual(sent_headers["New-Api-User"], "13")
                    self.assertEqual(sent_headers["Authorization"], "Bearer synthetic-user-token")
                    if returned_id == 13:
                        self.assertEqual(result["user_id"], 13)
                    else:
                        self.assertIsNone(result)

    async def test_purge_openid_only_restores_group(self):
        await self.seed_openid()
        with patch.object(self.core, "revert_user_group", new_callable=AsyncMock, return_value=True) as revert:
            success, binding = await self.core.purge_user_binding(13)
        self.assertTrue(success)
        self.assertEqual(binding["website_user_id"], 13)
        revert.assert_awaited_once_with(13)
        self.assertIsNone(await self.core.get_openid_by_website_id(13))

    async def test_purge_revert_failure_preserves_both_bindings(self):
        await self.seed_qq()
        await self.seed_openid()
        with patch.object(self.core, "revert_user_group", new_callable=AsyncMock, return_value=False):
            success, _ = await self.core.purge_user_binding(13)
        self.assertFalse(success)
        self.assertIsNotNone(await self.core.get_user_by_website_id(13))
        self.assertIsNotNone(await self.core.get_openid_by_website_id(13))

    async def test_purge_second_delete_failure_rolls_back_first_delete(self):
        await self.seed_qq()
        await self.seed_openid()
        # Fail whichever table is deleted second, independent of delete order.
        for table, other in (("newapi_bindings", "newapi_openid_bindings"), ("newapi_openid_bindings", "newapi_bindings")):
            await self.connection.execute(f"""
                CREATE TRIGGER fail_second_{table} BEFORE DELETE ON {table}
                WHEN NOT EXISTS (SELECT 1 FROM {other} WHERE website_user_id = OLD.website_user_id)
                BEGIN SELECT RAISE(ABORT, 'synthetic second-delete failure'); END
            """)
        await self.connection.commit()
        with patch.object(self.core, "revert_user_group", new_callable=AsyncMock, return_value=True):
            success, _ = await self.core.purge_user_binding(13)
        self.assertFalse(success)
        self.assertIsNotNone(await self.core.get_user_by_website_id(13))
        self.assertIsNotNone(await self.core.get_openid_by_website_id(13))

    async def test_insert_failure_never_returns_success(self):
        for method, identity in ((self.core.insert_binding, 70001), (self.core.insert_openid_binding, "synthetic-openid")):
            for result in (None, 0):
                with self.subTest(method=method.__name__, result=result):
                    with patch.object(self.core, "execute_query", new_callable=AsyncMock, return_value=result):
                        with self.assertRaises(Exception):
                            await method(identity, 13)
        self.assertIsNone(await self.core.get_user_by_website_id(13))
        self.assertIsNone(await self.core.get_openid_by_website_id(13))

    async def test_sqlite_last_heist_time_is_datetime(self):
        stamp = "2026-01-02 03:04:05"
        await self.connection.execute(
            "INSERT INTO daily_heist_log (robber_qq_id, victim_website_id, heist_time, outcome, amount) VALUES (?, ?, ?, ?, ?)",
            (70001, 13, stamp, "SUCCESS", 1),
        )
        await self.connection.commit()
        last_time = await self.core.get_last_heist_time_by_qq(70001)
        self.assertIsInstance(last_time, datetime)
        self.assertEqual(last_time, datetime(2026, 1, 2, 3, 4, 5))
        self.assertIsNone(await self.core.get_last_heist_time_by_qq(70002))


if __name__ == "__main__":
    unittest.main()
