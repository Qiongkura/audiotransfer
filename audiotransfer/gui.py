"""AudioTransfer Studio: responsive desktop workflow for the STM32 player."""

from __future__ import annotations
import argparse
import math
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from . import __version__
from .desktop import (
    ROOT,
    STAGES,
    CONTENT_MODES,
    VOCAL_METHODS,
    ConversionRequest,
    WavePreview,
    audio_labels,
    convert,
    flash,
    validate_audio,
)

BG, CARD, EDGE = "#0b1020", "#131d33", "#263552"
INK, MUTED, CYAN, BLUE = "#edf4ff", "#8d9fbd", "#48e0d0", "#5987ff"
FONT = "Microsoft YaHei UI"


def duration(ms):
    return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}"


class StudioApp:
    def __init__(self, root, output=None):
        self.root = root
        root.title(f"AudioTransfer Studio · 旋律工坊 · v{__version__}")
        root.geometry("1180x860")
        root.minsize(1080, 830)
        root.configure(bg=BG)
        self.messages = queue.Queue()
        self.source = self.result = None
        self.output = output or ROOT / "out_desktop"
        self.busy = self.completed = False
        self.operation = ""
        self.stage = -1
        self.stage_percent = None
        self.started_at = 0.0
        self.ticks = 0
        self.preview = WavePreview()
        self.title = tk.StringVar()
        self.artist = tk.StringVar()
        self.controller = tk.StringVar(value="ssd1306")
        self.mode = tk.StringVar(value="song")
        self.content_mode = tk.StringVar(value="自动识别")
        self.vocal_method = tk.StringVar(value="唱音识别（试用）")
        self.status = tk.StringVar(value="准备好，把喜欢的歌放进来。")
        self.detail = tk.StringVar(value="完整歌曲 → 主旋律 → 开发板播放")
        self.counter = tk.StringVar(value="等待导入")
        self.track_info = tk.StringVar(
            value="MP3 / FLAC / WAV / M4A / AAC / OGG / OPUS / WMA"
        )
        self.preview_time = tk.StringVar(value="00:00 / 00:00")
        self.badge = tk.StringVar(value="●  待导入")
        self._build()
        for var in (
            self.title,
            self.artist,
            self.controller,
            self.mode,
            self.content_mode,
            self.vocal_method,
        ):
            var.trace_add("write", self._invalidate)
        self._enable_drop()
        root.bind("<Control-o>", lambda _: self.choose())
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(60, self._poll)
        root.after(33, self._animate)

    def label(self, parent, text="", size=11, color=INK, **kw):
        return tk.Label(
            parent, text=text, bg=parent["bg"], fg=color, font=(FONT, size), **kw
        )

    def button(self, parent, text, command, primary=False, **kw):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=BLUE if primary else "#22314e",
            fg=INK,
            activebackground="#6b99ff" if primary else "#304567",
            activeforeground=INK,
            disabledforeground="#74829b",
            font=(FONT, 11, "bold"),
            relief="flat",
            bd=0,
            padx=18,
            pady=12,
            cursor="hand2",
            highlightthickness=0,
            **kw,
        )

    def _build(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Studio.TCombobox",
            fieldbackground="#1b2942",
            background=EDGE,
            foreground=INK,
            arrowcolor=CYAN,
            bordercolor=EDGE,
            padding=7,
        )
        self.root.option_add("*TCombobox*Listbox.background", CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", INK)
        side = tk.Frame(self.root, bg="#10182a", width=210)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        for text, size, color, pady in [
            ("◈", 38, CYAN, (26, 0)),
            ("AudioTransfer", 17, INK, 0),
            (f"S T U D I O   /   {__version__}", 9, MUTED, (6, 32)),
            ("♫   旋律工坊", 12, CYAN, 0),
            ("从一首歌，到一个小舞台", 9, MUTED, (10, 32)),
        ]:
            self.label(side, text, size, color).pack(anchor="w", padx=26, pady=pady)
        tk.Frame(side, bg=EDGE, height=1).pack(fill="x", padx=24)
        self.label(side, "你的开发板", 10, MUTED).pack(
            anchor="w", padx=27, pady=(24, 10)
        )
        self.label(side, "STM32F103C8T6", 11).pack(anchor="w", padx=27)
        self.label(side, "MH-FMD · 无源蜂鸣器", 9, MUTED).pack(
            anchor="w", padx=27, pady=(6, 20)
        )
        for name, pin in [
            ("蜂鸣器", "PA8"),
            ("屏幕 SCL / SDA", "PB6 / PB7"),
            ("音量 ＋ / －", "PA0 / PA1"),
            ("播放 / 暂停", "PA2"),
        ]:
            self.label(side, name, 9, MUTED).pack(anchor="w", padx=27, pady=(8, 0))
            self.label(side, pin, 11).pack(anchor="w", padx=27, pady=(2, 0))
        bottom = tk.Frame(side, bg=side["bg"])
        bottom.pack(side="bottom", fill="x", padx=26, pady=26)
        for text, color in [
            ("按键另一端接 GND", CYAN),
            ("ST-Link · USB 烧录", MUTED),
            ("开机音量 50%", MUTED),
        ]:
            self.label(bottom, text, 9, color).pack(anchor="w", pady=(6, 0))
        body = tk.Frame(self.root, bg=BG)
        body.pack(side="left", fill="both", expand=True, padx=32, pady=25)
        header = tk.Frame(body, bg=BG)
        header.pack(fill="x")
        self.label(header, "让旋律，落在指尖。", 23).pack(side="left")
        self.label(header, textvariable=self.badge, size=10, color=CYAN).pack(
            side="right", pady=8
        )
        self.label(
            body, "拖入音频，一键转换，再把整首旋律带到开发板。", 10, MUTED
        ).pack(anchor="w", pady=(6, 19))
        self.drop = tk.Canvas(
            body,
            height=156,
            bg=CARD,
            highlightthickness=1,
            highlightbackground=EDGE,
            cursor="hand2",
        )
        self.drop.pack(fill="x")
        self.drop.bind("<Button-1>", lambda _: self.choose())
        self.label(
            body, textvariable=self.track_info, size=9, color=MUTED, anchor="w"
        ).pack(fill="x", pady=(8, 12))
        meta = tk.Frame(body, bg=BG)
        meta.pack(fill="x")
        self.entries = []
        for i, (name, var) in enumerate(
            [("屏幕歌名", self.title), ("歌手 / 作者", self.artist)]
        ):
            field = tk.Frame(meta, bg=BG)
            field.pack(
                side="left", fill="x", expand=True, padx=(0, 16 if i == 0 else 0)
            )
            self.label(field, name, 9, MUTED).pack(anchor="w", pady=(0, 6))
            entry = tk.Entry(
                field,
                textvariable=var,
                font=(FONT, 12),
                bg="#19253e",
                fg=INK,
                insertbackground=CYAN,
                readonlybackground="#19253e",
                relief="flat",
                highlightthickness=1,
                highlightbackground=EDGE,
                highlightcolor=CYAN,
                bd=10,
            )
            entry.pack(fill="x")
            self.entries.append(entry)
        types = tk.Frame(body, bg=BG)
        types.pack(fill="x", pady=(12, 0))
        self.label(types, "音频类型", 10, MUTED).pack(side="left", padx=(0, 16))
        self.content_buttons = []
        for name in CONTENT_MODES:
            button = tk.Radiobutton(
                types,
                text=name,
                variable=self.content_mode,
                value=name,
                bg=BG,
                fg=INK,
                selectcolor=CARD,
                activebackground=BG,
                activeforeground=CYAN,
                font=(FONT, 11),
                relief="flat",
                highlightthickness=0,
                cursor="hand2",
                padx=8,
            )
            button.pack(side="left", padx=(0, 14))
            self.content_buttons.append(button)
        actions = tk.Frame(body, bg=BG)
        actions.pack(fill="x", pady=(18, 17))
        self.convert_button = self.button(
            actions, "01   一键转换", self.start_convert, True
        )
        self.convert_button.pack(side="left")
        self.flash_button = self.button(
            actions, "02   一键烧录", self.start_flash, state="disabled"
        )
        self.flash_button.pack(side="left", padx=12)
        self.settings_button = self.button(actions, "设置", self.settings)
        self.settings_button.pack(side="right")
        progress = tk.Frame(
            body, bg=CARD, highlightthickness=1, highlightbackground=EDGE
        )
        progress.pack(fill="x")
        row = tk.Frame(progress, bg=CARD)
        row.pack(fill="x", padx=18, pady=(14, 0))
        self.label(row, textvariable=self.status, size=12).pack(side="left")
        self.label(row, textvariable=self.counter, size=9, color=CYAN).pack(
            side="right"
        )
        self.bar = tk.Canvas(progress, bg=CARD, height=46, highlightthickness=0)
        self.bar.pack(fill="x", padx=18)
        row = tk.Frame(progress, bg=CARD)
        row.pack(fill="x", padx=18)
        self.stage_labels = []
        for text in STAGES:
            label = self.label(row, text, 9, MUTED)
            label.pack(side="left", expand=True)
            self.stage_labels.append(label)
        self.label(
            progress,
            textvariable=self.detail,
            size=9,
            color=MUTED,
            anchor="w",
            wraplength=780,
        ).pack(fill="x", padx=18, pady=(10, 14))
        listen = tk.Frame(body, bg=BG)
        listen.pack(fill="x", pady=(17, 12))
        self.play_button = self.button(
            listen, "▶  试听旋律", self.play, state="disabled"
        )
        self.play_button.pack(side="left")
        self.pause_button = self.button(
            listen, "Ⅱ  暂停试听", self.pause, state="disabled"
        )
        self.pause_button.pack(side="left", padx=10)
        self.label(listen, textvariable=self.preview_time, size=11, color=CYAN).pack(
            side="left", padx=6
        )
        self.folder_button = self.button(
            listen, "打开结果", self.open_result, state="disabled"
        )
        self.folder_button.pack(side="right")
        foot = tk.Frame(body, bg=BG)
        foot.pack(fill="x")
        self.label(foot, "处理记录", 9, MUTED).pack(side="left")
        self.label(foot, "分析在本机进行 · 首次使用会下载模型", 9, MUTED).pack(
            side="right"
        )
        self.log = tk.Text(
            body,
            height=3,
            font=("Consolas", 9),
            bg="#0e1729",
            fg=MUTED,
            state="disabled",
            relief="flat",
            padx=12,
            pady=8,
            wrap="word",
        )
        self.log.pack(fill="both", expand=True, pady=(6, 0))

    def _enable_drop(self):
        try:
            from tkinterdnd2 import DND_FILES

            for widget in (self.root, self.drop):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._drop)
        except (ImportError, tk.TclError, AttributeError):
            self._log("拖放组件未加载，可点击导入；运行 setup.bat 安装完整桌面依赖。")

    def _drop(self, event):
        if self.busy:
            return "refuse_drop"
        try:
            files = self.root.tk.splitlist(event.data)
            if len(files) != 1:
                raise ValueError("请一次拖入一首歌。")
            self.select(Path(files[0]))
            return "copy"
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("导入失败", str(exc), parent=self.root)
            return "refuse_drop"

    def choose(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择一首完整歌曲",
            filetypes=[
                ("音频", "*.mp3 *.flac *.wav *.m4a *.aac *.ogg *.opus *.wma"),
                ("全部文件", "*.*"),
            ],
        )
        if path:
            try:
                self.select(Path(path))
            except ValueError as exc:
                messagebox.showerror("导入失败", str(exc), parent=self.root)

    def select(self, path):
        if self.busy:
            return
        path = validate_audio(path)
        self._stop_preview()
        self.source = path
        title, artist = audio_labels(path)
        self.title.set(title)
        self.artist.set(artist)
        self._invalidate()
        self.status.set("歌曲已就位，开始转换吧。")
        self.badge.set("●  已导入")
        self.track_info.set(
            f"{path.suffix[1:].upper()}   ·   {path.stat().st_size/1048576:.1f} MB   ·   {path.name}"
        )
        self._log(f"已导入：{path.name}")

    def _invalidate(self, *_):
        if self.busy:
            return
        self.result = None
        self.completed = False
        self.stage = -1
        self._stop_preview()
        for b in (
            self.flash_button,
            self.play_button,
            self.pause_button,
            self.folder_button,
        ):
            b.configure(state="disabled")
        self.badge.set("●  待转换" if self.source else "●  待导入")
        self.counter.set("等待转换" if self.source else "等待导入")
        self.preview_time.set("00:00 / 00:00")
        self.detail.set("完整歌曲 → 主旋律 → 开发板播放")
        if self.source:
            self.status.set("设置已更新，请重新转换。")
        for button in self.content_buttons:
            button.configure(
                state="normal" if self.mode.get() == "song" else "disabled"
            )

    def _set_busy(self, value):
        self.busy = value
        for b in (self.convert_button, self.settings_button):
            b.configure(state="disabled" if value else "normal")
        for e in self.entries:
            e.configure(state="readonly" if value else "normal")
        for button in self.content_buttons:
            button.configure(
                state="disabled" if value or self.mode.get() != "song" else "normal"
            )
        for b in (self.flash_button, self.play_button, self.folder_button):
            b.configure(state="normal" if self.result and not value else "disabled")

    def start_convert(self):
        if self.busy:
            return
        if not self.source:
            self.choose()
            if not self.source:
                return
        if not self.title.get().strip():
            messagebox.showerror("缺少歌名", "请填写要显示的歌名。", parent=self.root)
            return
        request = ConversionRequest(
            self.source,
            self.output,
            self.title.get(),
            self.artist.get(),
            self.controller.get(),
            self.mode.get(),
            CONTENT_MODES[self.content_mode.get()],
            VOCAL_METHODS[self.vocal_method.get()],
        )
        self.result = None
        self._begin("convert")
        self._launch(lambda: convert(request, self._report), "converted")

    def start_flash(self):
        if self.busy or self.result is None:
            return
        try:
            self.result.validate_for_flash()
        except (OSError, ValueError) as exc:
            self._invalidate()
            messagebox.showerror("请重新转换", str(exc), parent=self.root)
            return
        result = self.result
        self._begin("flash")
        self._launch(lambda: flash(result, self._report), "flashed")

    def _begin(self, operation):
        self._stop_preview()
        self.operation = operation
        self.stage = 0
        self.stage_percent = None
        self.completed = False
        self.started_at = time.monotonic()
        self._set_busy(True)
        self.pause_button.configure(state="disabled")
        self.badge.set("●  转换中" if operation == "convert" else "●  烧录中")
        names = (
            STAGES
            if operation == "convert"
            else ("准备工程", "编译程序", "生成固件", "写入开发板", "校验固件")
        )
        for w, n in zip(self.stage_labels, names):
            w.configure(text=n)
        self.status.set("开始转换" if operation == "convert" else "开始烧录")
        self.detail.set("正在准备，请稍候。")
        self._log("— 开始转换 —" if operation == "convert" else "— 开始烧录 —")

    def _launch(self, job, success):
        def worker():
            try:
                self.messages.put((success, job()))
            except Exception as exc:
                self.messages.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _report(self, progress):
        self.messages.put(("progress", progress))

    def _log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 800:
            self.log.delete("1.0", "200.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "progress":
                    self.stage = max(self.stage, payload.stage)
                    self.stage_percent = payload.percent
                    if payload.message:
                        self.detail.set(payload.message[:100])
                        self._log(payload.message)
                    self.status.set(self.stage_labels[self.stage]["text"])
                elif kind in ("converted", "flashed"):
                    if kind == "converted":
                        self.result = payload
                        self.preview_time.set(
                            f"00:00 / {duration(payload.duration_ms)}"
                        )
                        self.detail.set(
                            f"识别类型：{dict(vocal='有演唱', instrumental='纯音乐', monophonic='单旋律').get(payload.detected_content_mode, '自动')} · {payload.event_count} 个事件 · 全长 {duration(payload.duration_ms)} · 可先试听，再烧录"
                        )
                    else:
                        self.detail.set(
                            "已写入并完成校验，开发板开始播放；PA2 按键可暂停 / 继续。"
                        )
                    self.stage = 4
                    self.stage_percent = 100
                    self.completed = True
                    self._set_busy(False)
                    self.status.set("转换完成" if kind == "converted" else "烧录成功")
                    self.badge.set("●  可烧录" if kind == "converted" else "●  已烧录")
                    self.counter.set("100%  ·  完成")
                    self._log(self.detail.get())
                elif kind == "error":
                    self._set_busy(False)
                    self.completed = False
                    self.status.set("处理未完成")
                    self.badge.set("●  需要处理")
                    self.counter.set("未完成")
                    self.detail.set("可在下方记录查看原因，修正后重试。")
                    self._log(payload)
                    messagebox.showerror("处理未完成", payload[:600], parent=self.root)
        except queue.Empty:
            pass
        self.root.after(60, self._poll)

    def _animate(self):
        self.ticks += 1
        w = max(100, self.drop.winfo_width())
        self.drop.delete("all")
        self.drop.create_rectangle(10, 10, w - 11, 145, outline=EDGE, dash=(5, 5))
        self.drop.create_text(35, 36, anchor="w", text="♫", fill=CYAN, font=(FONT, 26))
        self.drop.create_text(
            85,
            40,
            anchor="w",
            text=self.source.stem[:38] if self.source else "拖入一首歌，开始创作",
            fill=INK,
            font=(FONT, 16, "bold"),
        )
        self.drop.create_text(
            35,
            88,
            anchor="w",
            text="点击这里选择文件   /   支持中文、空格和长文件名",
            fill=MUTED,
            font=(FONT, 10),
        )
        for i in range(64):
            h = 3 + abs(math.sin(i * 0.51 + self.ticks * 0.045)) * (
                5 + 10 * abs(math.sin(i * 0.2))
            )
            x = 35 + i * (w - 70) / 64
            self.drop.create_line(
                x, 126 - h / 2, x, 126 + h / 2, fill=CYAN if i < 28 else BLUE, width=3
            )
        w = max(100, self.bar.winfo_width())
        self.bar.delete("all")
        seg = (w - 24) / 5
        for i in range(5):
            x = i * (seg + 6)
            done = self.completed or i < self.stage
            self.bar.create_rectangle(
                x,
                18,
                x + seg,
                26,
                fill=CYAN if done else BLUE if self.busy and i == self.stage else EDGE,
                outline="",
            )
            self.stage_labels[i].configure(
                fg=CYAN if done else INK if i == self.stage else MUTED
            )
            if self.busy and i == self.stage:
                if self.stage_percent is not None:
                    self.bar.create_rectangle(
                        x,
                        18,
                        x + seg * self.stage_percent / 100,
                        26,
                        fill=CYAN,
                        outline="",
                    )
                else:
                    glow = x + (self.ticks % 80) / 80 * max(0, seg - 24)
                    self.bar.create_rectangle(
                        glow, 16, glow + 24, 28, fill="#9cf8ee", outline=""
                    )
        if self.busy:
            s = int(time.monotonic() - self.started_at)
            p = (
                f" · 本阶段 {self.stage_percent}%"
                if self.stage_percent is not None
                else ""
            )
            self.counter.set(f"{self.stage+1} / 5{p}  ·  {s//60:02d}:{s%60:02d}")
        if self.preview.opened and self.result and self.ticks % 15 == 0:
            try:
                pos = self.preview.position()
                self.preview_time.set(
                    f"{duration(pos)} / {duration(self.result.duration_ms)}"
                )
                if pos >= self.result.duration_ms - 30 and not self.preview.paused:
                    self._stop_preview()
            except Exception as exc:
                self._log(str(exc))
                self._stop_preview()
        self.root.after(33, self._animate)

    def _stop_preview(self):
        try:
            self.preview.close()
        except Exception as exc:
            self._log(str(exc))
        self.pause_button.configure(text="Ⅱ  暂停试听", state="disabled")

    def play(self):
        if self.busy or not self.result:
            return
        try:
            self.preview.play(self.result.preview)
            self.pause_button.configure(state="normal", text="Ⅱ  暂停试听")
        except Exception as exc:
            messagebox.showerror("无法试听", str(exc), parent=self.root)

    def pause(self):
        try:
            self.preview.toggle()
            self.pause_button.configure(
                text="▶  继续试听" if self.preview.paused else "Ⅱ  暂停试听"
            )
        except Exception as exc:
            messagebox.showerror("试听失败", str(exc), parent=self.root)

    def open_result(self):
        if self.result:
            os.startfile(str(self.result.project.resolve()))

    def settings(self):
        if self.busy:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("播放设置")
        dialog.geometry("540x440")
        dialog.configure(bg=CARD)
        dialog.transient(self.root)
        dialog.grab_set()
        self.label(dialog, "播放设置", 18).pack(anchor="w", padx=24, pady=(22, 14))
        for label, var, values in [
            ("屏幕驱动", self.controller, ("ssd1306", "sh1106")),
            (
                "分析模式：song 完整歌曲 / tracked 单旋律",
                self.mode,
                ("song", "tracked"),
            ),
            ("人声旋律识别", self.vocal_method, tuple(VOCAL_METHODS)),
        ]:
            self.label(dialog, label, 10, MUTED).pack(anchor="w", padx=24, pady=(6, 4))
            ttk.Combobox(
                dialog,
                textvariable=var,
                values=values,
                state="readonly",
                style="Studio.TCombobox",
            ).pack(fill="x", padx=24)
        folder = tk.StringVar(value=str(self.output))
        self.label(
            dialog, textvariable=folder, size=9, color=MUTED, wraplength=480
        ).pack(anchor="w", padx=24, pady=12)
        row = tk.Frame(dialog, bg=CARD)
        row.pack(fill="x", padx=24)

        def choose_output():
            selected = filedialog.askdirectory(parent=dialog, title="选择转换结果目录")
            if selected:
                self.output = Path(selected)
                folder.set(selected)

        self.button(row, "更换输出目录", choose_output).pack(side="left")
        self.button(row, "完成", dialog.destroy, True).pack(side="right")

    def close(self):
        if self.busy:
            messagebox.showinfo(
                "正在处理", "请等待本次转换或烧录结束后关闭窗口。", parent=self.root
            )
            return
        self._stop_preview()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="AudioTransfer Studio 桌面程序")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--content-mode", choices=("auto", "vocal", "instrumental"), default="auto"
    )
    parser.add_argument(
        "--advanced", action="store_true", help="打开原有 Arduino / STM32 参数界面"
    )
    args = parser.parse_args()
    if args.advanced:
        from .legacy_gui import AudioTransferApp

        root = tk.Tk()
        AudioTransferApp(root)
    else:
        try:
            from tkinterdnd2 import TkinterDnD

            root = TkinterDnD.Tk()
        except (ImportError, tk.TclError):
            root = tk.Tk()
        app = StudioApp(root, args.output)
        app.content_mode.set(
            next(
                name
                for name, mode in CONTENT_MODES.items()
                if mode == args.content_mode
            )
        )
        if args.file:
            try:
                app.select(args.file)
            except ValueError as exc:
                messagebox.showerror("导入失败", str(exc), parent=root)
    root.mainloop()


if __name__ == "__main__":
    main()
