"""Small Tkinter front end that runs analysis off the UI thread."""

from __future__ import annotations

from pathlib import Path
import math
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .analysis import AnalysisConfig
from .output import validate_board, validate_stm32_pin, validate_upload_protocol
from .pipeline import process_file, upload_platformio


class AudioTransferApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("AudioTransfer - 单旋律转 Arduino / STM32")
        self.root.geometry("980x650")
        self.messages: queue.Queue[tuple[str, object]] = queue.Queue()

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(Path.cwd() / "out"))
        self.ffmpeg_var = tk.StringVar(value="ffmpeg")
        self.target_var = tk.StringVar(value="STM32 PlatformIO")
        self.board_var = tk.StringVar(value="bluepill_f103c8")
        self.stm32_pin_var = tk.StringVar(value="PA8")
        self.pio_var = tk.StringVar(value="pio")
        self.upload_protocol_var = tk.StringVar(value="stlink")
        self.upload_var = tk.BooleanVar(value=False)
        self.pitch_mode_var = tk.StringVar(value="完整歌曲（人声＋乐器）")
        self.candidate_count_var = tk.StringVar(value="5")
        self.tracking_min_frequency_var = tk.StringVar(value="140")
        self.pin_var = tk.StringVar(value="8")
        self.sample_rate_var = tk.StringVar(value="16000")
        self.frame_ms_var = tk.StringVar(value="40")
        self.hop_ms_var = tk.StringVar(value="20")
        self.speed_var = tk.StringVar(value="1.0")
        self.min_note_ms_var = tk.StringVar(value="80")
        self.max_notes_var = tk.StringVar(value="0")
        self.gap_ms_var = tk.StringVar(value="6")
        self.sound_mode_var = tk.StringVar(value="柔和发声")
        self.active_low_var = tk.BooleanVar(value=False)
        self.arrange_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="就绪")
        self.start_button: ttk.Button | None = None
        self.log_text: tk.Text | None = None
        self.preview_path: Path | None = None
        self.preview_button: ttk.Button | None = None
        self._build_widgets()
        self.root.after(100, self._poll_messages)

    def _build_widgets(self) -> None:
        root = self.root
        root.columnconfigure(1, weight=1)
        root.rowconfigure(8, weight=1)

        def add_row(row: int, label: str, variable: tk.StringVar, browse: str | None = None) -> None:
            ttk.Label(root, text=label).grid(row=row, column=0, padx=8, pady=6, sticky="w")
            ttk.Entry(root, textvariable=variable).grid(row=row, column=1, padx=8, pady=6, sticky="ew")
            if browse:
                ttk.Button(
                    root,
                    text=browse,
                    command=(self._choose_input if browse == "选择文件" else self._choose_output),
                ).grid(row=row, column=2, padx=8, pady=6)

        add_row(0, "输入 MP3/音频", self.input_var, "选择文件")
        add_row(1, "输出目录", self.output_var, "选择目录")
        add_row(2, "ffmpeg", self.ffmpeg_var)

        target_frame = ttk.LabelFrame(root, text="输出目标与烧录")
        target_frame.grid(row=3, column=0, columnspan=3, padx=8, pady=8, sticky="ew")
        for column in range(6):
            target_frame.columnconfigure(column, weight=1 if column in (1, 3, 5) else 0)
        ttk.Label(target_frame, text="目标").grid(row=0, column=0, padx=4, pady=6, sticky="w")
        ttk.Combobox(
            target_frame,
            textvariable=self.target_var,
            values=("Arduino", "STM32 PlatformIO"),
            state="readonly",
            width=18,
        ).grid(row=0, column=1, padx=4, pady=6, sticky="ew")
        ttk.Label(target_frame, text="board").grid(row=0, column=2, padx=4, pady=6, sticky="w")
        ttk.Entry(target_frame, textvariable=self.board_var, width=20).grid(
            row=0, column=3, padx=4, pady=6, sticky="ew"
        )
        ttk.Label(target_frame, text="STM32 pin").grid(row=0, column=4, padx=4, pady=6, sticky="w")
        ttk.Entry(target_frame, textvariable=self.stm32_pin_var, width=12).grid(
            row=0, column=5, padx=4, pady=6, sticky="ew"
        )
        ttk.Label(target_frame, text="pio").grid(row=1, column=0, padx=4, pady=6, sticky="w")
        ttk.Entry(target_frame, textvariable=self.pio_var).grid(
            row=1, column=1, columnspan=3, padx=4, pady=6, sticky="ew"
        )
        ttk.Checkbutton(
            target_frame,
            text="立即 ST-Link 上传",
            variable=self.upload_var,
        ).grid(row=1, column=4, columnspan=2, padx=4, pady=6, sticky="w")
        ttk.Label(target_frame, text="upload protocol").grid(row=2, column=0, padx=4, pady=6, sticky="w")
        ttk.Entry(target_frame, textvariable=self.upload_protocol_var).grid(
            row=2, column=1, columnspan=2, padx=4, pady=6, sticky="ew"
        )
        ttk.Label(target_frame, text="分析模式").grid(row=2, column=3, padx=4, pady=6, sticky="w")
        ttk.Combobox(
            target_frame,
            textvariable=self.pitch_mode_var,
            values=("完整歌曲（人声＋乐器）", "主旋律跟踪", "逐帧旧算法"),
            state="readonly",
            width=16,
        ).grid(row=2, column=4, columnspan=2, padx=4, pady=6, sticky="ew")
        ttk.Label(target_frame, text="候选数").grid(row=3, column=0, padx=4, pady=6, sticky="w")
        ttk.Entry(target_frame, textvariable=self.candidate_count_var, width=12).grid(
            row=3, column=1, padx=4, pady=6, sticky="ew"
        )
        ttk.Label(target_frame, text="主旋律最低 Hz").grid(row=3, column=2, padx=4, pady=6, sticky="w")
        ttk.Entry(target_frame, textvariable=self.tracking_min_frequency_var, width=12).grid(
            row=3, column=3, padx=4, pady=6, sticky="ew"
        )
        ttk.Combobox(target_frame, textvariable=self.sound_mode_var,
                     values=("柔和发声", "丰满音色", "音符PWM", "原有方波"), state="readonly", width=16).grid(
                         row=3, column=4, padx=4, pady=6, sticky="ew")
        ttk.Checkbutton(target_frame, text="整理旋律节奏", variable=self.arrange_var).grid(
            row=3, column=5, padx=4, pady=6, sticky="w")
        ttk.Checkbutton(target_frame, text="低电平驱动模块（HIGH关闭）", variable=self.active_low_var).grid(
            row=4, column=0, columnspan=4, padx=4, pady=4, sticky="w")

        parameter_frame = ttk.LabelFrame(root, text="参数")
        parameter_frame.grid(row=5, column=0, columnspan=3, padx=8, pady=8, sticky="ew")
        for column in range(8):
            parameter_frame.columnconfigure(column, weight=1)
        fields = [
            ("Arduino 引脚", self.pin_var),
            ("采样率", self.sample_rate_var),
            ("帧 ms", self.frame_ms_var),
            ("跳步 ms", self.hop_ms_var),
            ("速度", self.speed_var),
            ("最短事件 ms", self.min_note_ms_var),
            ("事件上限 0=全曲", self.max_notes_var),
            ("音内停顿 ms", self.gap_ms_var),
        ]
        for column, (label, variable) in enumerate(fields):
            ttk.Label(parameter_frame, text=label).grid(row=0, column=column, padx=4, pady=(6, 2))
            ttk.Entry(parameter_frame, textvariable=variable, width=12).grid(
                row=1, column=column, padx=4, pady=(2, 6)
            )

        self.start_button = ttk.Button(root, text="开始处理", command=self._start)
        self.start_button.grid(row=6, column=0, padx=8, pady=8, sticky="w")
        ttk.Label(root, textvariable=self.status_var).grid(row=6, column=1, padx=8, pady=8, sticky="w")
        self.preview_button = ttk.Button(root, text="试听旋律", command=self._play_preview, state="disabled")
        self.preview_button.grid(row=6, column=2, padx=8, pady=8)

        ttk.Label(root, text="日志").grid(row=7, column=0, columnspan=3, padx=8, pady=(8, 2), sticky="w")
        self.log_text = tk.Text(root, height=12, state="disabled", wrap="word")
        self.log_text.grid(row=8, column=0, columnspan=3, rowspan=3, padx=8, pady=(2, 8), sticky="nsew")
        scrollbar = ttk.Scrollbar(root, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=8, column=3, rowspan=3, pady=(2, 8), sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def _choose_input(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择音频文件",
            filetypes=[("音频文件", "*.mp3 *.wav *.m4a *.flac *.ogg"), ("所有文件", "*.*")],
        )
        if selected:
            self.input_var.set(selected)

    def _choose_output(self) -> None:
        selected = filedialog.askdirectory(title="选择输出目录")
        if selected:
            self.output_var.set(selected)

    def _append_log(self, message: str) -> None:
        if self.log_text is None:
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _selected_target(self) -> str:
        return "platformio-stm32" if self.target_var.get() == "STM32 PlatformIO" else "arduino"

    def _play_preview(self) -> None:
        if self.preview_path is not None and self.preview_path.is_file():
            try:
                os.startfile(str(self.preview_path.resolve()))
            except OSError as exc:
                messagebox.showerror("试听失败", str(exc))

    def _selected_pitch_mode(self) -> str:
        return {"逐帧旧算法": "legacy", "主旋律跟踪": "tracked"}.get(self.pitch_mode_var.get(), "song")

    def _start(self) -> None:
        if not self.input_var.get().strip():
            messagebox.showwarning("缺少输入", "请选择输入音频文件。")
            return
        target = self._selected_target()
        pitch_mode = self._selected_pitch_mode()
        board = self.board_var.get().strip()
        stm32_pin = self.stm32_pin_var.get().strip()
        pio = self.pio_var.get().strip() or "pio"
        try:
            values = {
                "pin": int(self.pin_var.get()),
                "sample_rate": int(self.sample_rate_var.get()),
                "frame_ms": int(self.frame_ms_var.get()),
                "hop_ms": int(self.hop_ms_var.get()),
                "speed": float(self.speed_var.get()),
                "candidate_count": int(self.candidate_count_var.get()),
                "tracking_min_frequency_hz": float(self.tracking_min_frequency_var.get()),
                "min_note_ms": int(self.min_note_ms_var.get()),
                "max_notes": int(self.max_notes_var.get()),
                "gap_ms": int(self.gap_ms_var.get()),
            }
            if values["pin"] < 0 or values["pin"] > 255:
                raise ValueError("引脚必须在 0 到 255 之间")
            if values["min_note_ms"] < 0:
                raise ValueError("最短事件不能为负数")
            if not math.isfinite(values["speed"]) or values["speed"] <= 0:
                raise ValueError("速度必须为正数")
            if values["candidate_count"] <= 0:
                raise ValueError("候选数必须为正数")
            if not math.isfinite(values["tracking_min_frequency_hz"]) or values["tracking_min_frequency_hz"] <= 0:
                raise ValueError("主旋律最低频率必须为正数")
            if values["max_notes"] < 0:
                raise ValueError("事件上限不能为负数，0 表示全曲")
            if values["gap_ms"] < 0:
                raise ValueError("音符间隔不能为负数")
            validate_board(board)
            validate_stm32_pin(stm32_pin)
            validate_upload_protocol(self.upload_protocol_var.get())
            config = AnalysisConfig(
                sample_rate=values["sample_rate"],
                frame_ms=values["frame_ms"],
                hop_ms=values["hop_ms"],
                pitch_mode=pitch_mode,
                candidate_count=values["candidate_count"],
                tracking_min_frequency_hz=values["tracking_min_frequency_hz"],
            )
            config.validate()
        except (TypeError, ValueError) as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        assert self.start_button is not None
        self.start_button.configure(state="disabled")
        if getattr(self, "preview_button", None) is not None:
            self.preview_button.configure(state="disabled")
        self.status_var.set("处理中…")
        input_path = self.input_var.get().strip()
        output_dir = self.output_var.get().strip()
        if not output_dir:
            self.start_button.configure(state="normal")
            self.status_var.set("就绪")
            messagebox.showerror("缺少输出目录", "请选择输出目录。")
            return
        ffmpeg = self.ffmpeg_var.get().strip() or "ffmpeg"
        upload = bool(self.upload_var.get())
        upload_protocol = self.upload_protocol_var.get().strip()
        self._append_log(f"输入: {input_path}")
        self._append_log(f"目标: {target}")
        self._append_log("后台线程已启动。")
        thread = threading.Thread(
            target=self._worker,
            args=(
                input_path,
                output_dir,
                ffmpeg,
                config,
                values["min_note_ms"],
                values["max_notes"],
                values["speed"],
                values["pin"],
                values["gap_ms"],
                target,
                board,
                stm32_pin,
                pio,
                upload_protocol,
                upload,
                {"原有方波":"classic", "丰满音色":"harmonic", "音符PWM":"pulse"}.get(
                    self.sound_mode_var.get() if getattr(self, "sound_mode_var", None) else "", "auto"),
                "raw" if getattr(self, "arrange_var", None) and not self.arrange_var.get() else "score",
                bool(getattr(self, "active_low_var", None) and self.active_low_var.get()),
            ),
            daemon=True,
        )
        thread.start()

    def _worker(
        self,
        input_path: str,
        output_dir: str,
        ffmpeg: str,
        config: AnalysisConfig,
        min_note_ms: int,
        max_notes: int,
        speed: float,
        pin: int,
        gap_ms: int,
        target: str,
        board: str,
        stm32_pin: str,
        pio: str,
        upload_protocol: str,
        upload: bool,
        sound_mode: str = "auto",
        arrangement: str = "score",
        active_low: bool = False,
    ) -> None:
        try:
            paths = process_file(
                input_path,
                output_dir,
                config=config,
                min_note_ms=min_note_ms,
                max_notes=max_notes or None,
                speed=speed,
                pin=pin,
                gap_ms=gap_ms,
                ffmpeg=ffmpeg,
                target=target,
                board=board,
                stm32_pin=stm32_pin,
                upload_protocol=upload_protocol,
                progress=lambda message: self.messages.put(("progress", message)),
                sound_mode=sound_mode,
                arrangement=arrangement,
                active_low=active_low,
            )
            self.messages.put(("generated", paths))
            if upload and target == "platformio-stm32":
                result = upload_platformio(output_dir, pio_command=pio)
                self.messages.put(("upload", result))
            elif upload:
                self.messages.put(("upload-skipped", None))
            self.messages.put(("success", None))
        except Exception as exc:  # surface errors in the UI instead of killing the worker silently
            self.messages.put(("error", exc))

    def _log_paths(self, paths: object) -> None:
        self.preview_path = paths.preview_path
        if self.preview_button is not None and self.preview_path is not None and self.preview_path.is_file():
            self.preview_button.configure(state="normal")
        self._append_log(f"JSON: {paths.json_path}")
        self._append_log(f"CSV:  {paths.csv_path}")
        self._append_log(f"INO:  {paths.ino_path}")
        self._append_log(f"试听: {paths.preview_path}")
        self._append_log(f"MIDI: {paths.midi_path}")
        self._append_log(f"简谱音符表: {paths.score_path}")
        if paths.platformio_ini_path is not None:
            self._append_log(f"PlatformIO 工程: {paths.platformio_ini_path.parent}")
            self._append_log(f"platformio.ini: {paths.platformio_ini_path}")
        if paths.main_cpp_path is not None:
            self._append_log(f"STM32 main.cpp: {paths.main_cpp_path}")

    def _poll_messages(self) -> None:
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "progress":
                    self.status_var.set(str(payload))
                    self._append_log(str(payload))
                elif kind == "generated":
                    self._log_paths(payload)
                    self.status_var.set("生成完成，准备上传" if self.upload_var.get() else "生成完成")
                elif kind == "upload":
                    self.status_var.set("ST-Link 上传完成")
                    self._append_log("PlatformIO 上传成功。")
                    self._append_log(" ".join(payload.command))
                    if payload.output:
                        self._append_log(payload.output)
                elif kind == "upload-skipped":
                    self._append_log("未执行 PlatformIO 上传：当前目标为 Arduino。")
                elif kind == "success":
                    self.status_var.set("完成")
                else:
                    self.status_var.set("失败")
                    self._append_log(f"错误: {payload}")
                    messagebox.showerror("处理失败", str(payload))
                if self.start_button is not None and kind in ("success", "error"):
                    self.start_button.configure(state="normal")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_messages)



def main() -> None:
    root = tk.Tk()
    AudioTransferApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
