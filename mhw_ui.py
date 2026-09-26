# -*- coding: utf-8 -*-
"""MHW 竞速语音计时器 · 图形界面（毛玻璃 + 渐变质感）

功能：
  1. 可视化调整所有配置
  2. 一键触发 IN-Q 重置（模拟按键），并检测 IN-Q 是否在运行
  3. 定时重置：任务进行到设定秒数自动重置
"""
import json
import os
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageDraw, ImageTk

import mhw_voice_timer as core

# ---------------- 配色 ----------------
BG_A = (8, 11, 22)        # 渐变起点（深蓝黑）
BG_B = (26, 18, 54)       # 渐变中段（深紫）
BG_C = (10, 26, 48)       # 渐变终点（深蓝）
GLOW = (109, 139, 255)    # 顶部柔光
CARD_FILL_A = 18          # 玻璃卡片白 alpha
CARD_LINE_A = 34          # 玻璃卡片描边 alpha
ACCENT = "#E8A33D"        # 主色（金）
ACCENT_HOVER = "#F4B75C"
ACCENT_DARK = "#B87A22"
BLUE = "#6D8BFF"
TEXT = "#E8ECF4"
TEXT_DIM = "#8B95A8"
TEXT_FAINT = "#5F6980"
FIELD_BG = "#0D1320"
FIELD_LINE = "#2A3550"
OFF_BG = "#2A3550"
OK_GREEN = "#3DD68C"
BAD_RED = "#F2545B"

FONT = "Microsoft YaHei UI"
F_TITLE = (FONT, 15, "bold")
F_SUB = (FONT, 9)
F_LABEL = (FONT, 10)
F_VAL = (FONT, 10, "bold")
F_SMALL = (FONT, 8)
F_BTN = (FONT, 10, "bold")

HOTKEYS = ["F%d" % i for i in range(1, 13)]
ENGINES = [("edge（晓晓 · 需联网）", "edge"), ("sapi（系统内置）", "sapi")]

W, H = 520, 740
CARDS = [
    (16, 66, 504, 212, 14),    # 状态
    (16, 224, 504, 390, 14),   # 播报设置
    (16, 402, 504, 532, 14),   # 语音
    (16, 544, 504, 672, 14),   # IN-Q 重置
]


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def lerp(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def lerp3(c1, c2, c3, t):
    if t < 0.5:
        return lerp(c1, c2, t * 2)
    return lerp(c2, c3, (t - 0.5) * 2)


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
        self.inq_pid = None
        self._last_inq = 0.0

        self.root = tk.Tk()
        self.root.title("MHW 竞速语音计时器")
        self.root.geometry("%dx%d" % (W, H))
        self.root.resizable(False, False)
        self.root.configure(bg="#%02x%02x%02x" % BG_A)

        self.canvas = tk.Canvas(self.root, width=W, height=H, highlightthickness=0, bd=0,
                                bg="#%02x%02x%02x" % BG_A)
        self.canvas.pack(fill="both", expand=True)

        self._style()
        self._render_bg()
        self._build()
        self._apply_to_widgets(cfg)
        self._start_timer()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(150, self.refresh)

    # ---------------- 样式 ----------------
    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("G.TEntry", fieldbackground=FIELD_BG, background=FIELD_BG,
                     foreground=TEXT, bordercolor=FIELD_LINE, lightcolor=FIELD_BG,
                     darkcolor=FIELD_BG, insertcolor=ACCENT, relief="flat", padding=4)
        st.map("G.TEntry", bordercolor=[("focus", ACCENT)])
        st.configure("G.TCombobox", fieldbackground=FIELD_BG, background=FIELD_BG,
                     foreground=TEXT, arrowcolor=TEXT_DIM, bordercolor=FIELD_LINE,
                     lightcolor=FIELD_BG, darkcolor=FIELD_BG, relief="flat", padding=4)
        st.map("G.TCombobox", fieldbackground=[("readonly", FIELD_BG)],
               foreground=[("readonly", TEXT)], bordercolor=[("focus", ACCENT)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD_BG)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#2A3550")
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        self.root.option_add("*TCombobox*Listbox.font", F_LABEL)

    # ---------------- 背景（渐变 + 玻璃卡片）----------------
    def _render_bg(self):
        s = 110
        small = Image.new("RGB", (s, s))
        px = small.load()
        for y in range(s):
            for x in range(s):
                px[x, y] = lerp3(BG_A, BG_B, BG_C, (x + y) / (2 * (s - 1)))
        bg = small.resize((W, H), Image.BICUBIC).convert("RGBA")

        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gd.ellipse([-260, -300, W + 160, 210], fill=GLOW + (30,))
        gd.ellipse([W - 240, H - 200, W + 200, H + 240], fill=(232, 163, 61, 18))
        bg = Image.alpha_composite(bg, glow)

        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        for (x1, y1, x2, y2, r) in CARDS:
            od.rounded_rectangle([x1, y1, x2, y2], radius=r,
                                 fill=(255, 255, 255, CARD_FILL_A),
                                 outline=(255, 255, 255, CARD_LINE_A), width=1)
        bg = Image.alpha_composite(bg, ov)
        self.bg_img = ImageTk.PhotoImage(bg.convert("RGB"))
        self.canvas.create_image(0, 0, anchor="nw", image=self.bg_img)

    # ---------------- 控件 ----------------
    def _txt(self, x, y, text, fill=TEXT, font=F_LABEL, anchor="w"):
        return self.canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor)

    def _entry(self, x, y, var, width, height=28, center=True):
        e = ttk.Entry(self.canvas, style="G.TEntry", textvariable=var,
                      justify="center" if center else "left")
        self.canvas.create_window(x, y, window=e, anchor="w", width=width, height=height)
        return e

    def _combo(self, x, y, var, values, width, height=28):
        c = ttk.Combobox(self.canvas, style="G.TCombobox", textvariable=var,
                         values=values, state="readonly", justify="center")
        self.canvas.create_window(x, y, window=c, anchor="w", width=width, height=height)
        return c

    def _toggle(self, x, y, text, var):
        w, h = 36, 19
        pill = round_rect(self.canvas, x, y, x + w, y + h, h / 2, fill=OFF_BG, outline="")
        knob = self.canvas.create_oval(x + 3, y + 3, x + h - 3, y + h - 3, fill="#94A0B8", outline="")
        label = self._txt(x + w + 9, y + h / 2, text, fill=TEXT, font=F_LABEL)

        def redraw(*_):
            on = bool(var.get())
            self.canvas.itemconfig(pill, fill=ACCENT if on else OFF_BG)
            self.canvas.itemconfig(knob, fill="#20160A" if on else "#94A0B8")
            if on:
                self.canvas.coords(knob, x + w - h + 3, y + 3, x + w - 3, y + h - 3)
            else:
                self.canvas.coords(knob, x + 3, y + 3, x + h - 3, y + h - 3)

        def click(_e):
            var.set(not bool(var.get()))

        for it in (pill, knob, label):
            self.canvas.tag_bind(it, "<Button-1>", click)
            self.canvas.tag_bind(it, "<Enter>", lambda e: self.canvas.config(cursor="hand2"))
            self.canvas.tag_bind(it, "<Leave>", lambda e: self.canvas.config(cursor=""))
        var.trace_add("write", redraw)
        redraw()

    def _button(self, x1, y1, x2, y2, text, command, primary=False, small=False):
        fill = ACCENT if primary else "#1C2437"
        hover = ACCENT_HOVER if primary else "#26304A"
        line = "" if primary else FIELD_LINE
        fg = "#1A1206" if primary else TEXT
        rect = round_rect(self.canvas, x1, y1, x2, y2, 9, fill=fill,
                          outline=line, width=1)
        lbl = self.canvas.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=text, fill=fg,
                                      font=(FONT, 9 if small else 10, "bold" if primary else "normal"))

        def run(_e):
            command()

        def enter(_e):
            self.canvas.itemconfig(rect, fill=hover)
            self.canvas.config(cursor="hand2")

        def leave(_e):
            self.canvas.itemconfig(rect, fill=fill)
            self.canvas.config(cursor="")

        for it in (rect, lbl):
            self.canvas.tag_bind(it, "<Button-1>", run)
            self.canvas.tag_bind(it, "<Enter>", enter)
            self.canvas.tag_bind(it, "<Leave>", leave)

    def _build(self):
        # 标题
        self._txt(30, 30, "MHW 竞速语音计时器", fill=TEXT, font=F_TITLE)
        self._txt(30, 55, "v1.2  ·  竞速语音计时 + IN-Q 重置", fill=TEXT_DIM, font=F_SUB)

        # ---- 状态卡 ----
        self._txt(34, 92, "游戏连接", fill=TEXT_DIM, font=F_SMALL)
        self.v_conn = self._txt(120, 92, "未连接", fill=TEXT, font=F_VAL)
        self._txt(34, 118, "任务状态", fill=TEXT_DIM, font=F_SMALL)
        self.v_quest = self._txt(120, 118, "未开始", fill=TEXT, font=F_VAL)
        self._txt(34, 144, "当前用时", fill=TEXT_DIM, font=F_SMALL)
        self.v_elapsed = self._txt(120, 144, "0.0 秒", fill=ACCENT, font=(FONT, 12, "bold"))
        self._txt(34, 170, "IN-Q", fill=TEXT_DIM, font=F_SMALL)
        self.inq_dot = self.canvas.create_oval(118, 165, 130, 177, fill=BAD_RED, outline="")
        self.v_inq = self._txt(138, 171, "检测中…", fill=TEXT, font=F_VAL)
        self.v_event = self._txt(34, 194, "启动中", fill=TEXT_FAINT, font=F_SMALL)

        # ---- 播报设置 ----
        self._txt(34, 250, "播报间隔(秒)", fill=TEXT_DIM, font=F_SMALL)
        self.v_interval = tk.StringVar()
        self._entry(136, 250, self.v_interval, 74)
        self._txt(246, 250, "小数位", fill=TEXT_DIM, font=F_SMALL)
        self.v_precision = tk.StringVar()
        self._combo(302, 250, self.v_precision, ["0", "1", "2"], 70)

        self._txt(34, 288, "完成补偿(秒)", fill=TEXT_DIM, font=F_SMALL)
        self.v_offset = tk.StringVar()
        self._entry(136, 288, self.v_offset, 74)

        self.v_start = tk.BooleanVar()
        self._toggle(34, 318, "任务开始时播报", self.v_start)
        self.v_abandon = tk.BooleanVar()
        self._toggle(272, 318, "放弃/失败时播报", self.v_abandon)
        self.v_resetload = tk.BooleanVar()
        self._toggle(34, 352, "读条结束算新一局（IN-Q 重置依赖）", self.v_resetload)

        # ---- 语音 ----
        self._txt(34, 428, "引擎", fill=TEXT_DIM, font=F_SMALL)
        self.v_engine = tk.StringVar()
        self._combo(110, 428, self.v_engine, [n for n, _ in ENGINES], 210)
        self._txt(34, 462, "晓晓语音名", fill=TEXT_DIM, font=F_SMALL)
        self.v_voice = tk.StringVar()
        self._entry(150, 462, self.v_voice, 300, center=False)
        self._txt(34, 496, "音调", fill=TEXT_DIM, font=F_SMALL)
        self.v_pitch = tk.StringVar()
        self._entry(110, 496, self.v_pitch, 130)

        # ---- IN-Q 重置 ----
        self._txt(34, 570, "重置按键", fill=TEXT_DIM, font=F_SMALL)
        self.v_hotkey = tk.StringVar()
        self._combo(112, 570, self.v_hotkey, HOTKEYS, 78)
        self._txt(226, 570, "定时重置(秒, 0=关)", fill=TEXT_DIM, font=F_SMALL)
        self.v_autoreset = tk.StringVar()
        self._entry(386, 570, self.v_autoreset, 74)
        self._txt(34, 602, "手动重置 / 定时重置需要 IN-Q 正在运行", fill=TEXT_FAINT, font=F_SMALL)
        self._button(34, 620, 214, 654, "手动重置任务", self.on_manual_reset, primary=True)

        # ---- 底部按钮 ----
        self._button(16, 686, 192, 722, "保存并应用", self.on_save, primary=True)
        self._button(200, 686, 370, 722, "测试语音", self.on_test_tts)
        self._button(378, 686, 504, 722, "退出", self.on_close)

    # ---------------- 配置 <-> 控件 ----------------
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
        cfg = json.loads(json.dumps(core.DEFAULT_CONFIG))
        cfg.update(json.loads(json.dumps(self.cfg)))

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
        self.thread = threading.Thread(target=core.run_timer,
                                       args=(self.cfg, self.stop_event, self.status), daemon=True)
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
        self.canvas.itemconfig(self.v_event, text="配置已保存并应用")

    def on_manual_reset(self):
        if not self.inq_pid:
            self.canvas.itemconfig(self.v_event, text="IN-Q 未运行，无法重置")
            messagebox.showwarning("IN-Q 未运行", "重置功能需要 IN-Q 正在运行。\n请先启动 IN-Q 再试。")
            return
        cfg = self._collect()
        if core.send_reset_key(cfg):
            self.canvas.itemconfig(self.v_event, text="已发送重置按键 %s" % cfg.get("reset_hotkey", ""))
        else:
            self.canvas.itemconfig(self.v_event, text="发送重置按键失败")

    def on_test_tts(self):
        cfg = self._collect()
        threading.Thread(target=lambda: core.TTS(cfg.get("tts", {})).speak("任务开始，计时器正常"),
                         daemon=True).start()
        self.canvas.itemconfig(self.v_event, text="已测试语音")

    def on_close(self):
        if self.stop_event:
            self.stop_event.set()
        self.root.destroy()

    # ---------------- 刷新 ----------------
    def refresh(self):
        s = self.status
        c = self.canvas
        c.itemconfig(self.v_conn, text=("已连接 (PID %s)" % s.get("pid")) if s.get("connected") else "未连接")
        if s.get("loading"):
            q = "读条中…"
        elif s.get("completed"):
            q = "已完成"
        elif s.get("in_quest"):
            q = "进行中 (ID %s)" % s.get("quest_id")
        else:
            q = "未开始"
        c.itemconfig(self.v_quest, text=q)
        c.itemconfig(self.v_elapsed, text=fmt_time(s.get("elapsed", 0.0)))

        now = time.time()
        if now - self._last_inq > 1.0:
            self._last_inq = now
            try:
                self.inq_pid = core.find_pid("IN-Q.exe")
            except Exception:
                self.inq_pid = None
            if self.inq_pid:
                c.itemconfig(self.inq_dot, fill=OK_GREEN)
                c.itemconfig(self.v_inq, text="已运行 (PID %s) · 重置可用" % self.inq_pid, fill=TEXT)
            else:
                c.itemconfig(self.inq_dot, fill=BAD_RED)
                c.itemconfig(self.v_inq, text="未运行 · 重置不可用", fill=BAD_RED)

        ev = s.get("last_event")
        if ev:
            c.itemconfig(self.v_event, text=str(ev)[:44])
        try:
            self.root.after(150, self.refresh)
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
