# 0.3 桌面版验证记录

2026-10-14，在 Windows / Python 3.10 环境验证。

- Python 自动检查 76 项通过，覆盖转换失败不开放烧录、工程修改检测、先编译再烧录、中文路径临时编译与写入校验状态。
- `tools/check_player_logic.py` 将实际 C++ 按键和时钟逻辑编译为 Cortex-M3 指令，在 Unicorn 中执行：52 项通过，包括消抖、长按不重复暂停、多次暂停恢复、时钟溢出和音量调节。
- 实际启动 `AudioTransfer.exe` 并检查桌面布局；TkDnD 扩展已加载，含中文、空格、括号的拖入路径、多文件拒绝、忙时禁止更换输入等检查通过。
- 完整《茶汤》指纹识别、校谱生成、614 个音符事件、308773 ms 总时长及中文 OLED 播放器导出通过。
- 从另一首完整录音截取 18 秒，实际通过 Demucs / CREPE 自动提取路径，输出 18000 ms、54 个事件；此项验证工作流程，不代表人工确认音准。
- Windows 原生 WAV 试听暂停后位置保持不变，恢复后继续递增。
- 新屏幕固件编译通过：Flash 40760 B / RAM 2288 B。含中文路径的输出工程经英文临时目录编译通过，生成的二进制和日志保存回输出目录。

连接恢复后，目标电压约 3.26 V。包含 52 项逻辑检查的测试固件在 STM32F103C8T6 上实际执行全部通过。通过调整 PA2 内部上拉／下拉模拟按键，检查实际 MCU 运行状态：

- 暂停及保持 1.5 秒后，播放时间均为 11683 ms；PWM 比较寄存器为 0，实际驱动关闭。
- 保持暂停时屏幕继续刷新 6 帧，I²C 传输失败数为 0，帧缓冲包含暂停双竖线。
- PA0 模拟音量＋，音量从 50% 调为 55%，暂停进度保持不变。
- 松开暂停键保持暂停，再次按下才继续；恢复时继续相同频率的音符，保持整个暂停时间不计入歌曲。
- 第二次暂停／恢复正常，长按不重复触发；最后恢复音量 50%。

正式《茶汤》示例固件随后通过 ST-Link 写入，OpenOCD 返回 `Verified OK` 并复位开始播放，开机音量为 50%。

以上为实板运行与 GPIO 电平模拟测试，未人工按动外接机械开关，也未人工判定声学音色。正式固件的校验值和写入状态记录在 `examples/chatang-player/firmware/verification.json`，运行检查记录在同目录 `pause-checks.json`。

复核命令：

```powershell
python -m pytest -q
python -m pip install unicorn pyelftools
python tools/check_player_logic.py
pio run -d examples/chatang-player
```
