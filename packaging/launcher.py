"""发布包的程序入口（Nuitka 从这里开始编译）。

与 ``uv run gelinspect`` 走同一个 ``main``：发布模式由 ``gelinspect._build_info`` 判断，
资源和数据目录的差异在 ``gelinspect.services.runtime_paths`` 里处理。
"""

from gelinspect.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
