# GAME 唱音识别来源与模型许可

AudioTransfer 的 ONNX 推理客户端由本项目编写，参考 [GAME 官方接口说明](https://github.com/openvpi/GAME/blob/main/ONNX.md)。上游 GAME 源码采用 MIT 许可证；本项目未复制其训练代码。

使用的模型为 openvpi 发布的 [GAME 1.0.3 Small ONNX](https://github.com/openvpi/GAME/releases/tag/v1.0.3)，用于把分离后的人声识别成音符起止和音高。模型发布说明指定 **CC BY-NC-SA 4.0**，限非商业用途；商业用途需另行取得模型权利人的许可。参见 [许可全文](https://creativecommons.org/licenses/by-nc-sa/4.0/)。本项目的源码许可不改变模型许可。

模型不随代码仓库分发。首次使用从上游下载约 44 MiB，核验压缩包及各文件 SHA-256 后保存到项目 `.models/game/`。后续可离线复用。压缩包 SHA-256：

`00ba0c64115b6b874d9ea4afd3e6cf822abda2a04e52569233b0a044fd40e4e8`

推理采用 8 步、中文语言编码、音符边界阈值 0.3、发声阈值 0.2。每个片段最长 13.2 秒，使用 CPU 和 4 个计算线程；片段间只有在两侧都识别到跨接缝的同一延音时才合并。真实同音重复保持独立。

演唱段仍由 RMVPE、CREPE 与分离轨能量共同确认。GAME 只替换这些演唱区域内的旋律，前奏、独立间奏和尾奏继续使用乐器跟踪；纯音乐跳过 GAME。演唱中的休止不会用伴奏补音。新模式绕过《茶汤》的已校对曲谱快捷匹配，识别结果不依赖提供简谱。
