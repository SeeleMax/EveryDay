"""零依赖覆盖率报告。

为什么不用 ``pytest-cov``
------------------------
本机 venv 的 ``pip`` 在读取配置目录时会因为注册表查询失败而直接崩溃
（``FileNotFoundError: CSIDL_COMMON_APPDATA``），装不上任何包。
而覆盖率是验收指标之一，所以这里改用标准库的 :mod:`trace`：

* **可执行行**：用 ``code.co_lines()`` 递归收集编译产物里出现的行号
* **已执行行**：``trace.Trace(count=1)`` 跑一遍完整的单元测试
* **覆盖率** = 已执行行 / 可执行行

用法::

    python tools/coverage_report.py            # 默认排除 ui 包
    python tools/coverage_report.py --include-ui

退出码：覆盖率低于 ``--threshold``（默认 60）时返回 1。
"""

from __future__ import annotations

import argparse
import io
import sys
import trace
import unittest
from pathlib import Path
from types import CodeType

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
# 两个路径都要有：src 用于导入 everyday，项目根用于导入 tests 包
for _path in (PROJECT_ROOT, SRC):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

#: 需要统计的包根目录
PACKAGE_ROOT = SRC / "everyday"

#: 默认排除的模块（纯 UI，需要真实事件循环，按验收口径不计入核心服务覆盖率）
DEFAULT_EXCLUDES = ("ui/", "__main__.py")


def executable_lines(path: Path) -> set[int]:
    """返回一个 Python 文件里所有「可执行」行号。

    通过递归遍历编译产物的代码对象收集，因此不会把注释、空行、
    纯文档字符串计进去——这与 ``coverage.py`` 的口径一致。
    """
    source = path.read_text(encoding="utf-8")
    try:
        module_code = compile(source, str(path), "exec")
    except SyntaxError:
        return set()

    lines: set[int] = set()
    stack: list[CodeType] = [module_code]
    while stack:
        code = stack.pop()
        for _start, _end, lineno in code.co_lines():
            if lineno:
                lines.add(lineno)
        for const in code.co_consts:
            if isinstance(const, CodeType):
                stack.append(const)
    return lines


def collect_targets(include_ui: bool) -> list[Path]:
    """返回要统计的文件列表。"""
    targets: list[Path] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(PACKAGE_ROOT).as_posix()
        if not include_ui and any(
            relative.startswith(prefix) or relative.endswith(prefix)
            for prefix in DEFAULT_EXCLUDES
        ):
            continue
        targets.append(path)
    return targets


def relative(path: Path) -> str:
    """把绝对路径转成 ``everyday/...`` 形式的短路径。"""
    return "everyday/" + path.relative_to(PACKAGE_ROOT).as_posix()


def main() -> int:
    parser = argparse.ArgumentParser(description="EveryDay 覆盖率报告")
    parser.add_argument("--include-ui", action="store_true", help="把 ui 包也计入")
    parser.add_argument("--threshold", type=float, default=60.0, help="覆盖率下限（百分比）")
    parser.add_argument("--show-missing", action="store_true", help="列出未覆盖的行号")
    args = parser.parse_args()

    targets = collect_targets(args.include_ui)

    print(f"正在以 trace 模式运行全部单元测试（{len(targets)} 个文件参与统计）…", flush=True)
    tracer = trace.Trace(count=1, trace=0, ignoredirs=[sys.prefix, sys.exec_prefix])

    stream = io.StringIO()
    result_holder: dict[str, unittest.TestResult] = {}

    def run_tests() -> None:
        # discover() 本身会 import 全部测试模块，进而 import everyday。
        # 必须把它放在 trace 之内，否则所有**模块级**代码（包 __init__、
        # 类定义、常量）都会被漏统计，覆盖率被人为压低。
        suite = unittest.TestLoader().discover(str(PROJECT_ROOT / "tests"))
        runner = unittest.TextTestRunner(stream=stream, verbosity=0)
        result_holder["result"] = runner.run(suite)

    tracer.runfunc(run_tests)
    result = result_holder["result"]
    executed = tracer.results().counts

    # ------------------------------------------------------------------
    rows: list[tuple[str, int, int, float]] = []
    total_executable = 0
    total_covered = 0

    for path in targets:
        key = str(path.resolve())
        lines = executable_lines(path)
        if not lines:
            continue
        hit = {lineno for (filename, lineno) in executed if str(Path(filename).resolve()) == key}
        covered = len(lines & hit)
        total_executable += len(lines)
        total_covered += covered
        percent = (covered / len(lines) * 100) if lines else 0.0
        rows.append((relative(path), covered, len(lines), percent))

    rows.sort(key=lambda row: row[3])

    print()
    print("=" * 66)
    print(f"{'模块':<38}{'已覆盖':>8}{'可执行':>8}{'覆盖率':>10}")
    print("-" * 66)
    for name, covered, lines, percent in rows:
        print(f"{name:<38}{covered:>8}{lines:>8}{percent:>9.1f}%")
    print("-" * 66)
    overall = (total_covered / total_executable * 100) if total_executable else 0.0
    scope = "含 ui" if args.include_ui else "不含 ui"
    print(f"{'合计（' + scope + '）':<38}{total_covered:>8}{total_executable:>8}{overall:>9.1f}%")
    print("=" * 66)

    tests_run = result.testsRun if result else 0
    failures = len(result.failures) + len(result.errors) if result else 0
    print(f"测试：运行 {tests_run} 项，失败/错误 {failures} 项")

    if failures and result:
        # 打印前几条失败信息，否则只能看到「失败了」而无从下手
        print()
        print("失败详情（最多 3 条）：")
        for label, entries in (("ERROR", result.errors), ("FAIL", result.failures)):
            for case, detail in entries[:3]:
                print(f"  [{label}] {case}")
                print("    " + detail.strip().splitlines()[-1])

    if args.show_missing:
        print()
        print("未覆盖的行号：")
        for path in targets:
            key = str(path.resolve())
            lines = executable_lines(path)
            hit = {lineno for (filename, lineno) in executed if str(Path(filename).resolve()) == key}
            missing = sorted(lines - hit)
            if missing:
                print(f"  {relative(path)}: {missing}")

    if failures:
        print("注意：测试有失败，覆盖率数字仅供定位，不能作为验收依据。")
        return 1

    if overall < args.threshold:
        print(f"覆盖率 {overall:.1f}% 低于要求 {args.threshold:.1f}%")
        return 1

    print(f"覆盖率 {overall:.1f}% 达到要求 {args.threshold:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
