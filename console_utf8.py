"""Windows 控制台输出编码兜底。

为什么需要这个模块
------------------
脚本里的 `✅`、`↔`、`mm²` 这类字符不在 GBK（cp936）里。Python 在**真实
控制台**上走 `WriteConsoleW`，不受代码页影响，所以交互式敲一行从来没出过
问题；但只要把输出重定向到管道或文件——

    python verify_suspension_3d.py | tee log.txt
    python verify_x.py > log.txt          # CI / 编辑器"运行"按钮也是这条路

`sys.stdout` 的编码就变成 locale 的 cp936，`print` 直接抛
`UnicodeEncodeError: 'gbk' codec can't encode character '\\u2705'`，
脚本**跑到一半崩掉、退出码 1**。看起来像"用例失败"，其实是输出失败——
实测 `verify_fem_eigen_link.py` 崩在 ✅，`verify_suspension_3d.py` 崩在
`mm²` 的 `²`（`\\xb2`）。

所以"改完必须重跑全部 verify 脚本"这条铁律，在默认 Windows 控制台下原本
是没法稳定执行的。这个模块把它变成可以。

用法
----
脚本入口（导入其它本地模块之前或之后都行，但要在第一次 print 之前）::

    from console_utf8 import setup_console
    setup_console()

`errors="replace"` 是刻意的：万一目标流真的收不了 UTF-8，宁可印一个 `?`
也不要崩。
"""

from __future__ import annotations

import sys

__all__ = ["setup_console"]


def setup_console(encoding: str = "utf-8") -> None:
    """把 stdout / stderr 强制成 UTF-8（不可编码字符替换为 `?`）。

    幂等、永不抛：`reconfigure` 在 Python 3.7+ 才有，被包装过的流
    （某些 IDE、pytest 捕获、Streamlit 的 ScriptRunner）可能没有这个方法，
    或者已经关闭——这些情况都直接跳过，把流保持原样。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:                     # pythonw.exe 下是 None
            continue
        try:
            stream.reconfigure(encoding=encoding, errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
