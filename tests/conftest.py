"""测试引导：让 tests/ 直接导入插件根目录的模块。

插件在 AstrBot 里以包的形式加载（内部使用相对导入），但纯逻辑模块
（tihudace_targets / tihudace_batch / i18n）不依赖 astrbot，可脱离框架测试。
"""

import os
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, PLUGIN_ROOT)
