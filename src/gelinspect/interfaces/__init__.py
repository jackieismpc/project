"""接口层：算法、视频源、设备控制和溯源的抽象契约。

行为契约用 ``typing.Protocol`` 定义，数据契约用 Pydantic 2 定义，
由 ``infrastructure`` 或 ``simulation`` 中的实现提供。本层只依赖标准库、numpy、
Pydantic 和 ``gelinspect.domain``。
"""
