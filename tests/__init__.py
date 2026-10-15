"""EveryDay 测试包。

把 ``src`` 加入 ``sys.path``，使得 ``python -m unittest discover -s tests``
在未执行 ``pip install -e .`` 的情况下也能正常运行。
"""

from __future__ import annotations

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)
