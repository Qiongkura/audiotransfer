# 0.3 桌面版验证记录

2026-10-14，在 Windows / Python 3.10 环境验证。

- Python 自动检查 76 项通过，覆盖转换失败不开放烧录、工程修改检测、先编译再烧录、中文路径临时编译与写入校验状态。
- `tools/check_player_logic.py` 将实际 C++ 按键和时钟逻辑编译为 Cortex-M3 指令，在 Unicorn 中执行：52 项通过，包括消抖、长按不重复暂停、多次暂停恢复、时钟溢出和音量调节。
- 实际启动 `AudioTransfer.exe` 并检查桌面布局；TkDnD 扩展已加载，含中文、空格、括号的拖入路径、多文件拒绝、忙时禁止更换输入等检查通过。
- 完整《茶汤》指纹识别、校谱生成、614 个音符事件、308773 ms 总时长及中文 OLED 播放器导出通过。
- 从另一首完整录音截取 18 秒，实际通过 Demucs / CREPE 自动提取路径，输出 18000 ms、54 个事件；此项验证工作流程，不代表人工确认音准。
- Windows 原生 WAV 试听暂停后位置保持不变，恢复后继续递增。
- 新屏幕固件编译通过：Flash 40760 B / RAM 2288 B。含中文路径的输出工程经英文临时目录编译通过，生成的二进制和日志保存回输出目录。

本次 ST-Link 可识别，检测到目标电压约 3.26 V，但 OpenOCD 返回 `init mode failed (unable to connect to the target)`。因此本次新固件未完成实物写入，PA2 暂停、屏幕暂停图标与恢复渐入仍待连接恢复后的实物验证。固件校验值和状态记录在 `examples/chatang-player/firmware/verification.json`。

复核命令：

```powershell
python -m pytest -q
python -m pip install unicorn pyelftools
python tools/check_player_logic.py
pio run -d examples/chatang-player
```
