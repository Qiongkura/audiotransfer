# STM32《茶汤》OLED 播放器

这是本项目当前已编译并通过 ST-Link 校验的播放器。包含完整 308.773 秒的单声部旋律、2:50 附近的八小节间奏校正、128×64 SSD1306 I²C 屏幕，以及两个音量按键。开机音量为50%。

## 接线

| 外设引脚 | STM32F103C8T6 |
| --- | --- |
| OLED VCC / GND | 3.3V / GND |
| OLED SCL / SDA | PB6 / PB7 |
| 音量＋按键 | PA0 与 GND 之间 |
| 音量－按键 | PA1 与 GND 之间 |
| 蜂鸣器 VCC / IO / GND | 3.3V / PA8 / GND |

默认用于低电平导通的三针无源蜂鸣器模块。按键使用内部上拉，按下接地。0～20%每次调1%，更高档位每次调5%；长按可连续调节，0%静音。屏幕显示歌名、歌手、播放时间、进度条和音量。

## 编译与烧录

在仓库根目录安装并运行 PlatformIO：

```powershell
python -m pip install platformio
pio run -d examples/chatang-player
pio run -d examples/chatang-player -t upload
```

使用 ST-Link 连接 PA13/SWDIO、PA14/SWCLK、GND 与适配供电。`firmware/firmware.bin` 是本次已验证的编译结果，重新生成源码后该文件不会自动更新。

本次构建：Flash 40372 字节、RAM 2260 字节，STM32 平台20.0.0、Arduino Core for STM32 3.0.0。`firmware/verification.json` 记录固件校验值。屏幕控制器不同时，可通过根目录说明中的播放器生成命令选择 SH1106；其初始化与列偏移已经在生成器中处理。

## 重新生成

Windows 安装项目依赖后，双击仓库根目录“茶汤校谱重生成.bat”，会从 `examples/chatang/` 的校谱和音高分析资料生成 `out_chatang_v8_interlude` 与 `out_chatang_player`；无需包含或重新分离原曲音频。随后编译烧录 `out_chatang_player`。

间奏约2:49.840～2:55.640保留休止，2:55.640～3:12.776播放校对的八小节，再按原时间接回主歌。项目保留单声部主旋律，不能重现原录音的人声、全部伴奏或真实乐器音色。

参考：[《茶汤》简谱](https://www.jianpujia.com/jianpu/154500.html)。原曲与原始分离音轨不包含在仓库中。
