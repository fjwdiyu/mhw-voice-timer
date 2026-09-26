# -*- coding: utf-8 -*-
"""MHW 竞速语音计时器 · 图形界面（tkinter）

功能：
  1. 可视化调整所有配置
  2. 一键触发 IN-Q 重置（模拟按键）
  3. 定时重置：任务进行到设定秒数自动重置
"""
import json
import os
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import mhw_voice_timer as core

HOTKEYS = ["F%d" % i for i in range(1, 13)]
ENGINES = [("edge（晓晓，需联网）", "edge"), ("sapi（系统内置）", "sapi")]


def fmt_time(sec):
    if sec is None or sec < 0:
        sec = 0
    if sec < 60:
        return "%.1f 秒" % sec
    m = int(sec // 60)
    return "%d 分 %.1f 秒" % (m, sec - m * 60)


class App:
    def __init__(self, cfg, cfg_path):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.status = {}
        self.stop_event = None
        self.thread = None

        self.root = tk.Tk()
        self.root.title("MHW 竞速语音计时器")
        self.root.geometry("470x640")
        self.root.resizable(False, False)
        self._build()
        self._apply_to_widgets(cfg)
        self._start_timer()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(200, self.refresh)

    # ---------------- 界面 ----------------
    def _build(self):
        pad = {"padx": 8, "pady": 3}

        # 状态区
        f = ttk.LabelFrame(self.root, text="状态")
        f.pack(fill="x", padx=10, pady=(10, 4))
        self.v_conn = tk.StringVar(value="未连接")
        self.v_quest = tk.StringVar(value="未开始")
        self.v_elapsed = tk.StringVar(value="0.0 秒")
        self.v_event = tk.StringVar(value="启动中")
        for i, (lab, var) in enumerate([("游戏连接", self.v_conn), ("任务状态", self.v_quest),
                                        ("当前用时", self.v_elapsed), ("最近事件", self.v_event)]):
            ttk.Label(f, text=lab, width=9).grid(row=i, column=0, sticky="w", **pad)
            ttk.Label(f, textvariable=var).grid(row=i, column=1, sticky="w", columnspan=3, **pad)

        # 播报设置
        f = ttk.LabelFrame(self.root, text="播报设置")
        f.pack(fill="x", padx=10, pady=4)
        self.v_interval = tk.StringVar()
        self.v_precision = tk.StringVar()
        self.v_offset = tk.StringVar()
        self.v_start = tk.BooleanVar()
        self.v_abandon = tk.BooleanVar()
        self.v_resetload = tk.BooleanVar()
        ttk.Label(f, text="播报间隔(秒)").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(f, textvariable=self.v_interval, width=10).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(f, text="小数位").grid(row=0, column=2, sticky="w", **pad)
        ttk.Combobox(f, textvariable=self.v_precision, values=["0", "1", "2"], width=5,
                     state="readonly").grid(row=0, column=3, sticky="w", **pad)
        ttk.Label(f, text="完成补偿(秒)").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(f, textvariable=self.v_offset, width=10).grid(row=1, column=1, sticky="w", **pad)
        ttk.Checkbutton(f, text="任务开始时播报", variable=self.v_start).grid(
            row=2, column=0, columnspan=2, sticky="w", **pad)
        ttk.Checkbutton(f, text="放弃/失败时播报", variable=self.v_abandon).grid(
            row=2, column=2, columnspan=2, sticky="w", **pad)
        ttk.Checkbutton(f, text="读条结束算新一局（IN-Q 重置依赖；多区任务可关）",
                        variable=self.v_resetload).grid(row=3, column=0, columnspan=4, sticky="w", **pad)

        # 语音
        f = ttk.LabelFrame(self.root, text="语音")
        f.pack(fill="x", padx=10, pady=4)
        self.v_engine = tk.StringVar()
        self.v_voice = tk.StringVar()
        self.v_pitch = tk.StringVar()
        ttk.Label(f, text="引擎").grid(row=0, column=0, sticky="w", **pad)
        ttk.Combobox(f, textvariable=self.v_engine, values=[n for n, _ in ENGINES], width=20,
                     state="readonly").grid(row=0, column=1, columnspan=3, sticky="w", **pad)
        ttk.Label(f, text="晓晓语音名").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(f, textvariable=self.v_voice, width=28).grid(row=1, column=1, columnspan=3, sticky="w", **pad)
        ttk.Label(f, text="音调(如+20Hz)").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(f, textvariable=self.v_pitch, width=12).grid(row=2, column=1, sticky="w", **pad)

        # IN-Q 重置
        f = ttk.LabelFrame(self.root, text="IN-Q 重置")
        f.pack(fill="x", padx=10, pady=4)
        self.v_hotkey = tk.StringVar()
        self.v_autoreset = tk.StringVar()
        ttk.Label(f, text="重置按键").grid(row=0, column=0, sticky="w", **pad)
        ttk.Combobox(f, textvariable=self.v_hotkey, values=HOTKEYS, width=8,
                     state="readonly").grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(f, text="定时重置(秒,0=关)").grid(row=0, column=2, sticky="w", **pad)
        ttk.Entry(f, textvariable=self.v_autoreset, width=8).grid(row=0, column=3, sticky="w", **pad)
        ttk.Button(f, text="手动重置任务", command=self.on_manual_reset).grid(
            row=1, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(f, text="（需 IN-Q 正在运行）", foreground="#888").grid(
            row=1, column=2, columnspan=2, sticky="w", **pad)

        # 底部按钮
        bf = ttk.Frame(self.root)
        bf.pack(fill="x", padx=10, pady=(6, 10))
        ttk.Button(bf, text="保存并应用", command=self.on_save).pack(side="left", expand=True, fill="x", padx=4)
        ttk.Button(bf, text="测试语音", command=self.on_test_tts).pack(side="left", expand=True, fill="x", padx=4)
        ttk.Button(bf, text="退出", command=self.on_close).pack(side="left", expand=True, fill="x", padx=4)

    def _apply_to_widgets(self, cfg):
        self.v_interval.set(str(cfg.get("interval_seconds", 60)))
        self.v_precision.set(str(cfg.get("precision", 1)))
        self.v_offset.set(str(cfg.get("completion_offset_seconds", 0.4)))
        self.v_start.set(bool(cfg.get("announce_start", True)))
        self.v_abandon.set(bool(cfg.get("announce_abandon", False)))
        self.v_resetload.set(bool(cfg.get("reset_on_load", True)))
        tts = cfg.get("tts", {})
        eng = tts.get("engine", "edge")
        for name, val in ENGINES:
            if val == eng:
                self.v_engine.set(name)
                break
        else:
            self.v_engine.set(ENGINES[0][0])
        self.v_voice.set(tts.get("edge_voice", "zh-CN-XiaoxiaoNeural"))
        self.v_pitch.set(tts.get("edge_pitch", "+0Hz"))
        self.v_hotkey.set(cfg.get("reset_hotkey", "F11"))
        self.v_autoreset.set(str(cfg.get("auto_reset_seconds", 0)))

    def _collect(self):
        """从界面读取配置，返回完整 cfg。"""
        cfg = json.loads(json.dumps(core.DEFAULT_CONFIG))
        cfg.update(json.loads(json.dumps(self.cfg)))  # 深拷贝覆盖，避免改动 self.cfg
        def as_int(var, default, lo=None, hi=None):
            try:
                v = int(float(var.get()))
            except (TypeError, ValueError):
                v = default
            if lo is not None:
                v = max(lo, v)
            if hi is not None:
                v = min(hi, v)
            return v
        def as_float(var, default, lo=0.0):
            try:
                v = float(var.get())
            except (TypeError, ValueError):
                v = default
            return max(lo, v)
        cfg["interval_seconds"] = as_int(self.v_interval, 60, 1)
        cfg["precision"] = as_int(self.v_precision, 1, 0, 3)
        cfg["completion_offset_seconds"] = round(as_float(self.v_offset, 0.4), 2)
        cfg["auto_reset_seconds"] = round(as_float(self.v_autoreset, 0), 1)
        cfg["announce_start"] = bool(self.v_start.get())
        cfg["announce_abandon"] = bool(self.v_abandon.get())
        cfg["reset_on_load"] = bool(self.v_resetload.get())
        cfg["reset_hotkey"] = self.v_hotkey.get() or "F11"
        eng = "edge"
        for name, val in ENGINES:
            if name == self.v_engine.get():
                eng = val
                break
        cfg.setdefault("tts", {})
        cfg["tts"]["engine"] = eng
        cfg["tts"]["edge_voice"] = self.v_voice.get().strip() or "zh-CN-XiaoxiaoNeural"
        cfg["tts"]["edge_pitch"] = self.v_pitch.get().strip() or "+0Hz"
        return cfg

    # ---------------- 计时线程 ----------------
    def _start_timer(self):
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=core.run_timer, args=(self.cfg, self.stop_event, self.status), daemon=True)
        self.thread.start()

    def _restart_timer(self):
        if self.stop_event:
            self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.5)
        self._start_timer()

    # ---------------- 事件 ----------------
    def on_save(self):
        cfg = self._collect()
        try:
            with open(self.cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))
            return
        self.cfg = cfg
        self._restart_timer()
        self.v_event.set("配置已保存并应用")

    def on_manual_reset(self):
        cfg = self._collect()
        if core.send_reset_key(cfg):
            self.v_event.set("已发送重置按键 %s" % cfg.get("reset_hotkey", ""))
        else:
            self.v_event.set("发送重置按键失败")

    def on_test_tts(self):
        cfg = self._collect()
        threading.Thread(target=lambda: core.TTS(cfg.get("tts", {})).speak("任务开始，计时器正常"),
                         daemon=True).start()
        self.v_event.set("已测试语音")

    def on_close(self):
        if self.stop_event:
            self.stop_event.set()
        self.root.destroy()

    # ---------------- 刷新状态 ----------------
    def refresh(self):
        s = self.status
        self.v_conn.set("已连接（PID %s）" % s.get("pid") if s.get("connected") else "未连接")
        if s.get("loading"):
            self.v_quest.set("读条中…")
        elif s.get("completed"):
            self.v_quest.set("已完成")
        elif s.get("in_quest"):
            self.v_quest.set("进行中（ID %s）" % s.get("quest_id"))
        else:
            self.v_quest.set("未开始")
        self.v_elapsed.set(fmt_time(s.get("elapsed", 0.0)))
        ev = s.get("last_event")
        if ev:
            self.v_event.set(ev)
        try:
            self.root.after(200, self.refresh)
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


def run_ui(cfg=None, cfg_path=None):
    if cfg_path is None:
        cfg_path = os.path.join(core._app_dir(), "config.json")
    if cfg is None:
        cfg = core.load_config(cfg_path)
    App(cfg, cfg_path).run()
