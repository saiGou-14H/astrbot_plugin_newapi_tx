"""MySQL integration tests against the disposable testdb container ONLY."""
import os
import unittest
from unittest.mock import AsyncMock, patch

import aiomysql
import httpx
import test_core_identity  # installs the isolated compat_plugin package path
from compat_plugin.newapi_utils import NewApiCore


@unittest.skipUnless(os.getenv("COMPAT_TEST_MYSQL") == "1", "disposable MySQL not requested")
class MySQLIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Deliberately hard-coded test database, never load deployed settings.
        self.pool = await aiomysql.create_pool(
            host="astrbot-newapi-compat-testdb", user="root",
            password="test-only-local-compat", db="compat_test",
            autocommit=True, minsize=1, maxsize=1,
        )
        self.addAsyncCleanup(self.close_pool)
        self.core = NewApiCore({"binding_settings": {"quota_display_ratio": 100}})
        self.core.db_pool = self.pool
        self.core.db_mode = "mysql"
        self.core.revert_user_group = AsyncMock(return_value=True)
        guard = patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock,
                             side_effect=AssertionError("Live HTTP forbidden"))
        self.http = guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(self.http.assert_not_awaited)
        await self.sql("DROP TRIGGER IF EXISTS reject_openid_delete")
        for table in ("newapi_bindings", "newapi_openid_bindings"):
            await self.sql(f"DROP TABLE IF EXISTS {table}")
        await self.sql("CREATE TABLE newapi_bindings (id INT PRIMARY KEY AUTO_INCREMENT, qq_id BIGINT UNIQUE NOT NULL, website_user_id INT UNIQUE NOT NULL, binding_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_check_in_time TIMESTAMP NULL) ENGINE=InnoDB")
        await self.sql("CREATE TABLE newapi_openid_bindings (id INT PRIMARY KEY AUTO_INCREMENT, openid VARCHAR(64) UNIQUE NOT NULL, website_user_id INT UNIQUE NOT NULL, binding_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB")
        await self.core.insert_openid_binding("synthetic-openid", 13)

    async def close_pool(self):
        self.pool.close()
        await self.pool.wait_closed()

    async def sql(self, query):
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query)

    async def test_openid_website_lookup_and_adjustment(self):
        kind, binding = await self.core.lookup_binding(13)
        self.assertEqual((kind, binding["website_user_id"]), ("WEBSITE_ID", 13))
        self.core.get_api_user_data = AsyncMock(side_effect=[{"quota": 1000}, {"quota": 1125}])
        self.core.manage_user_quota = AsyncMock(return_value=True)
        status, details = await self.core.adjust_balance_by_identifier("13", 1.25)
        self.assertEqual(status, "SUCCESS")
        self.assertEqual(details["new_display_quota"], 11.25)
        self.core.manage_user_quota.assert_awaited_once_with(13, "add", 125)

    async def test_dual_binding_deletes_atomically(self):
        await self.core.insert_binding(70001, 13)
        success, _ = await self.core.purge_user_binding(13)
        self.assertTrue(success)
        self.assertIsNone(await self.core.get_user_by_website_id(13))
        self.assertIsNone(await self.core.get_openid_by_website_id(13))

    async def test_second_delete_failure_rolls_back_first(self):
        await self.core.insert_binding(70001, 13)
        await self.sql("CREATE TRIGGER reject_openid_delete BEFORE DELETE ON newapi_openid_bindings FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='synthetic failure'")
        success, _ = await self.core.purge_user_binding(13)
        self.assertFalse(success)
        self.assertIsNotNone(await self.core.get_user_by_website_id(13))
        self.assertIsNotNone(await self.core.get_openid_by_website_id(13))
        await self.sql("DROP TRIGGER reject_openid_delete")

    async def test_namespace_collision_and_precise_rollback(self):
        await self.core.insert_binding(13, 26)
        self.assertEqual((await self.core.lookup_binding("qq:13"))[1]["website_user_id"], 26)
        self.assertEqual((await self.core.lookup_binding("13"))[1]["website_user_id"], 13)
        self.assertEqual(await self.core.delete_binding(qq_id=13, website_user_id=39), 0)
        self.assertIsNotNone(await self.core.get_user_by_website_id(26))
