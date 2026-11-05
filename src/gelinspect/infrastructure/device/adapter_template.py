"""设备控制适配模板，通信协议（串口、Modbus 等）确定后实现。

接入步骤：

1. 复制本文件并重命名类，例如 ``infrastructure/device/modbus.py`` 的 ``ModbusController``。
2. 通信库在 ``connect`` 内部创建连接，参数从配置读取，类必须能无参构造。
3. 把协议错误转换为 ``gelinspect.interfaces.device`` 中的异常，不要向上层抛出驱动异常。
4. 全部时间使用带时区的本机时钟，与视频帧的 ``captured_at`` 保持同一时钟。
5. 在配置中设置 ``device.backend`` 指向新类。界面和业务层不需要改动。
"""

from __future__ import annotations

from gelinspect.interfaces.device import DeviceStatus, FlipResult, TrayPosition


class DeviceControllerAdapter:
    """``DeviceController`` 的实现模板。"""

    def connect(self) -> None:
        """建立与设备的连接。

        Raises:
            NotImplementedError: 模板尚未实现。
        """
        # 步骤：打开端口 → 握手确认固件版本 → 读取一次状态确认通信正常。
        raise NotImplementedError("请在协议确定后实现")

    def move_tray(self, position: TrayPosition) -> None:
        """移动滑台，到位后返回。

        Args:
            position: 目标位置。

        Raises:
            NotImplementedError: 模板尚未实现。
        """
        # 步骤：发送移动指令 → 轮询到位信号 → 超时抛 DeviceTimeoutError。
        raise NotImplementedError("请在协议确定后实现")

    def select_row(self, row_index: int) -> None:
        """让相机对准一排。相机固定时实现为空操作。

        Args:
            row_index: 排号，1–10。

        Raises:
            NotImplementedError: 模板尚未实现。
        """
        # 步骤：发送定位指令 → 轮询到位信号 → 超时抛 DeviceTimeoutError。
        raise NotImplementedError("请在协议确定后实现")

    def flip_row(self, row_index: int) -> FlipResult:
        """翻转一排并保持翻转状态。

        Args:
            row_index: 排号，1–10。

        Returns:
            翻转结果。

        Raises:
            NotImplementedError: 模板尚未实现。
        """
        # 步骤：记录开始时间 → 发送翻转指令 → 等待到位静止 → 读取编码器 → 返回结果。
        raise NotImplementedError("请在协议确定后实现")

    def restore_row(self, row_index: int) -> FlipResult:
        """把一排复位到翻转前的姿态。

        Args:
            row_index: 排号，1–10。

        Returns:
            复位结果。

        Raises:
            NotImplementedError: 模板尚未实现。
        """
        # 步骤：与 flip_row 相同，方向相反。已在原位时直接成功（必须幂等）。
        raise NotImplementedError("请在协议确定后实现")

    def read_status(self) -> DeviceStatus:
        """读取设备状态。

        Returns:
            状态快照。

        Raises:
            NotImplementedError: 模板尚未实现。
        """
        # 步骤：读取温度、舱门、滑台和故障。通信失败时返回 connected=False，不抛异常。
        raise NotImplementedError("请在协议确定后实现")

    def close(self) -> None:
        """断开连接，可重复调用。

        Raises:
            NotImplementedError: 模板尚未实现。
        """
        # 步骤：停止轮询 → 关闭端口。
        raise NotImplementedError("请在协议确定后实现")
