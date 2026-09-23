"""Run inside the deployed AstrBot container; permits SELECT and GET only.
Print only lines prefixed VERIFY_RESULT when collecting this subprocess output.
"""
import asyncio
import json
from pathlib import Path

import aiomysql
from data.plugins.astrbot_plugin_newapi_tx.newapi_utils import NewApiCore
from data.plugins.astrbot_plugin_newapi_tx.heist_logic import HeistLogic


async def verify():
    config = json.loads(Path('/AstrBot/data/config/astrbot_plugin_newapi_tx_config.json').read_text(encoding='utf-8-sig'))
    db = config['database_settings']
    api = config['api_settings']
    core = NewApiCore(config)
    core.api_base_url = api['api_base_url']
    token = api['api_access_token'].strip()
    core.api_access_token = token if token.lower().startswith('bearer ') else 'Bearer ' + token
    core.api_admin_user_id = api['api_admin_user_id']
    pool = await aiomysql.create_pool(host=db['host'], port=int(db['port']), user=db['user'], password=db['password'], db=db['name'], autocommit=True, minsize=1, maxsize=1)
    core.db_pool = pool
    try:
        raw_query = core.execute_query
        async def readonly_query(query, *args, **kwargs):
            assert query.strip().upper().startswith('SELECT '), 'Live database writes prohibited'
            return await raw_query(query, *args, **kwargs)
        core.execute_query = readonly_query
        raw_api = core.api_request
        async def readonly_api(method, *args, **kwargs):
            assert method == 'GET', 'Live API writes prohibited'
            return await raw_api(method, *args, **kwargs)
        core.api_request = readonly_api
        for target in (13, '13', 'site:13'):
            kind, binding = await core.lookup_binding(target)
            assert kind == 'WEBSITE_ID' and binding and binding['website_user_id'] == 13
        via_openid = await core.lookup_binding('openid:' + binding['openid'])
        assert via_openid[1]['website_user_id'] == 13
        self_binding = await core.get_openid_by_website_id(1)
        assert self_binding is not None
        status, parties = await HeistLogic(config, core)._resolve_heist_parties('openid:' + self_binding['openid'], 13)
        assert status == 'VALID' and parties['victim_site_id'] == 13
        user = await core.get_api_user_data(13)
        assert user and user['id'] == 13
        assert await core.check_api_connection()
        print('VERIFY_RESULT=' + json.dumps({
            'website13_lookup': 'ok', 'openid_target_lookup': 'ok',
            'heist_parties': 'VALID', 'victim_website_id': 13,
            'api_user_read': 'ok', 'admin_api_check': 'ok',
            'live_quota_writes': 0, 'live_binding_writes': 0,
        }))
    finally:
        pool.close()
        await pool.wait_closed()


if __name__ == '__main__':
    try:
        asyncio.run(verify())
    except Exception as exc:
        print('VERIFY_RESULT=' + json.dumps({'failed': type(exc).__name__}))
        raise SystemExit(1)
