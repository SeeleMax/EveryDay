"""EveryDay 启动入口。

在 PyCharm 中把本文件设为 Run Configuration 的脚本即可直接运行：

    python main.py                # 正常启动，显示主窗口
    python main.py --minimized    # 开机自启模式：只驻留托盘与悬浮窗
    python main.py --data-dir D:\\tmp\\everyday   # 覆盖数据目录（便于调试）
"""

from __future__ import annotations

import os
import sys

# 让 `python main.py` 在未执行 pip install 的情况下也能找到 src 下的 everyday 包。
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from everyday.app import main  # noqa: E402  (必须在 sys.path 调整之后导入)

if __name__ == "__main__":
    sys.exit(main())
