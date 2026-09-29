"""Cold-process registration regression: no plugin init, DB or network calls."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROBE = r'''
import importlib, json, sys
from types import ModuleType
from unittest.mock import patch
from astrbot.api.star import Star
from astrbot.core.star.context import Context
from astrbot.core.star.star import star_map, star_registry
from astrbot.dashboard.services.plugin_service import PluginService
name = '_cold_registration_test'
package = ModuleType(name)
package.__path__ = [sys.argv[1]]
sys.modules[name] = package
with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')) as network:
    module = importlib.import_module(name + '.main')
    metadata = star_map[module.__name__]
    registry = Context.get_all_stars(object.__new__(Context))
    snapshot = {
        'registered_once': sum(item is metadata for item in registry) == 1,
        'correct_entry_class': metadata.star_cls_type is module.NewApiSuitePlugin,
        'visible_not_ghost': not PluginService.is_ghost_plugin(metadata),
        'scheduler_not_star': not issubclass(module.TihudaceScheduler, Star),
        'entry_is_star': issubclass(module.NewApiSuitePlugin, Star),
        'module_path_correct': metadata.module_path == module.__name__,
    }
    again = importlib.import_module(name + '.main')
    snapshot['repeat_import_same_metadata'] = (again is module and
        star_map[module.__name__] is metadata and
        sum(item is metadata for item in registry) == 1)
    network.assert_not_called()
print('REGISTRATION_RESULT=' + json.dumps(snapshot))
'''


class PluginRegistrationTests(unittest.TestCase):
    def probe(self):
        with tempfile.TemporaryDirectory(prefix='newapi-registration-') as cwd:
            result = subprocess.run([sys.executable, '-c', PROBE, str(ROOT)],
                                    cwd=cwd, text=True, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        lines = [line.split('=', 1)[1] for line in result.stdout.splitlines()
                 if line.startswith('REGISTRATION_RESULT=')]
        self.assertEqual(len(lines), 1)
        return json.loads(lines[0])

    def test_register_decorator_only_belongs_to_plugin_entry(self):
        tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
        decorated = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for decorator in node.decorator_list:
                    if (isinstance(decorator, ast.Call) and
                            isinstance(decorator.func, ast.Name) and
                            decorator.func.id == 'register'):
                        decorated.append(node.name)
        self.assertEqual(decorated, ['NewApiSuitePlugin'])

    def test_cold_import_is_visible_in_dashboard_registry(self):
        snapshot = self.probe()
        self.assertTrue(all(snapshot.values()), snapshot)

    def test_second_fresh_process_does_not_need_manual_reload(self):
        snapshot = self.probe()
        self.assertTrue(snapshot['registered_once'])
        self.assertTrue(snapshot['visible_not_ghost'])
        self.assertTrue(snapshot['repeat_import_same_metadata'])


if __name__ == '__main__':
    unittest.main()
