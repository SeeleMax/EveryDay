"""生成 Windows 可执行文件用的 ``.ico``。

仓库刻意只存文本（界面图标由 :mod:`everyday.ui.icons` 用 ``QPainter`` 画），
所以 exe 图标在**打包时**现场生成，而不是把一个二进制文件签进版本库。

用法::

    python tools/make_icon.py [输出路径]

默认输出到 ``build/everyday.ico``（``build/`` 已被 .gitignore 忽略）。
优先用 Qt 直接写 ICO；Qt 不支持写 ICO 时回落到 Pillow；两者都没有就跳过，
打包仍可继续（只是 exe 用默认图标）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: 需要包含的图标尺寸（Windows 会按 DPI 选用）
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_png(size: int, target: Path) -> bool:
    """用应用自己的图标绘制逻辑渲染一张 PNG。"""
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841

    from everyday.ui.icons import app_icon

    pixmap = app_icon(size).pixmap(size, size)
    if pixmap.isNull():
        return False
    return bool(pixmap.save(str(target), "PNG"))


def write_ico_with_qt(target: Path) -> bool:
    """尝试直接用 Qt 写 ICO（部分 Qt 构建只支持读 ICO）。"""
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841

    from everyday.ui.icons import app_icon

    # Qt 的 ICO 写入支持不稳定，先拿 256 试一下
    pixmap = app_icon(256).pixmap(256, 256)
    return bool(pixmap.save(str(target), "ICO"))


def write_ico_with_pillow(target: Path, png_path: Path) -> bool:
    """用 Pillow 把 PNG 转成多尺寸 ICO。"""
    try:
        from PIL import Image
    except ImportError:
        return False

    if not png_path.is_file():
        return False

    base = Image.open(png_path).convert("RGBA")
    base.save(target, format="ICO", sizes=[(s, s) for s in ICON_SIZES])
    return True


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "build" / "everyday.ico"
    target.parent.mkdir(parents=True, exist_ok=True)

    if write_ico_with_qt(target):
        print(f"已生成 ICO（Qt 直接写入）：{target}")
        return 0

    png_path = target.with_suffix(".png")
    if render_png(256, png_path) and write_ico_with_pillow(target, png_path):
        print(f"已生成 ICO（Qt 渲染 + Pillow 转换）：{target}")
        return 0

    print(
        "跳过图标生成：Qt 不支持写 ICO，且未安装 Pillow。\n"
        "可执行文件仍能构建，只是使用默认图标。\n"
        "如需图标请安装 Pillow：pip install pillow",
        file=sys.stderr,
    )
    return 0  # 图标缺失不应让打包失败


if __name__ == "__main__":
    sys.exit(main())
