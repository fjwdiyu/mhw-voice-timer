# -*- coding: utf-8 -*-
"""
怪物猎人世界（Monster Hunter: World / Iceborne）竞速语音计时器

功能：
  * 自动读取游戏内存，识别任务开始 / 结束（含 IN-Q 快速重置/重新开始）
  * 任务进行中按设定间隔语音播报已用时；任务完成时语音播报总用时
  * 语音默认微软晓晓（zh-CN-XiaoxiaoNeural，甜美少女音，需联网），离线自动回退内置中文语音
  * 打包为 exe 后无黑窗，驻留系统托盘，右键托盘图标退出

用法：
  python mhw_voice_timer.py                 # 计时 + 语音播报（装了 pystray 则驻留托盘）
  python mhw_voice_timer.py --console       # 强制控制台模式（调试用）
  python mhw_voice_timer.py --diagnose      # 打印内存字段
  python mhw_voice_timer.py --test-tts 你好    # 测试语音
  python mhw_voice_timer.py --config 自定义.json

只支持 PC Steam 版 MHW（v15.20+）。
"""

import argparse
import asyncio
import ctypes
import json
import os
import queue
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes

try:
    import pystray
    from PIL import Image as _PILImage
    _HAS_TRAY = True
except Exception:
    pystray = None
    _HAS_TRAY = False

PROCESS_NAME = "MonsterHunterWorld.exe"

# 特征码：名称 -> (字节串, RIP 相对位移在模式内的偏移)
SIGNATURES = {
    "sMhGUI":     ("48 8B 05 ?? ?? ?? ?? 0F 28 74 24 40 48 8B B4 24 ?? ?? ?? ?? 8B 98", 3),
    "sQuest":     ("48 83 EC 48 48 8B 0D ?? ?? ?? ?? E8 ?? ?? ?? ?? 3C 01 0F 84 E0 00 00 00 48 8B 0D ?? ?? ?? ?? E8", 7),
    "sEventDemo": ("48 8B 05 ?? ?? ?? ?? 83 78 58 01 77 11", 3),
    "sMhArea":    ("48 8B 05 ?? ?? ?? ?? 0F B6 80 EB D2 00 00 C3", 3),
}

# 字段的指针链（相对各自全局指针；最后一个元素是字段偏移，前面的都是逐级解引用）
FIELDS = {
    "quest_id":    ("sQuest",     [0x4C]),           # int32   当前任务 ID（>0 表示有任务）
    "obj1_state":  ("sQuest",     [0x9B]),           # uint8   主目标1状态（5 = 完成）
    "load_state":  ("sMhGUI",     [0x13F28, 0x1D04]),# uint8   0=非载入，1/2/3=载入中
    "cutscene":    ("sEventDemo", [0x58]),           # int32   !=0 = 过场 CG 中
    "area_id":     ("sMhArea",    [0x8058, 0xCC]),   # int32   当前区域 ID（诊断用）
}

DEFAULT_CONFIG = {
    "interval_seconds": 60,      # 每隔多少秒播报一次进行时长
    "precision": 1,              # 播报时间的小数位数（0 或 1）
    "announce_start": True,      # 任务（重新）开始时播报“任务开始”
    "announce_abandon": False,   # 中途放弃/失败时是否播报
    "reset_on_load": True,       # 任务中读条结束即视为“新的一局”（IN-Q 快速重置依赖此开关；多区任务换区也会读条，可设为 false）
    "completion_offset_seconds": 0.40,  # 完成时间补偿：目标完成到游戏计时器冻结之间的秒数，用于对齐结算画面时间
    "reset_hotkey": "F11",       # 触发 IN-Q 重置的按键（F10/F11/...）
    "auto_reset_seconds": 0,     # 定时重置：任务进行到该秒数自动触发重置（0=关闭）
    "tts": {
        "engine": "edge",                          # edge = 晓晓神经网络音(甜美少女,需联网) | sapi = Windows内置
        "edge_voice": "zh-CN-XiaoxiaoNeural",      # 也可试 zh-CN-XiaoyiNeural(小伊,活泼)
        "edge_rate": "+0%",                        # 语速，如 +10% / -10%
        "edge_pitch": "+0Hz",                      # 音调，如 +20Hz 更尖细
        "sapi_voice": "Microsoft Huihui Desktop",  # 离线回退语音
        "sapi_rate": 0,                            # 离线回退语速 -10 ~ 10
    },
    "texts": {
        "start": "任务开始",
        "elapsed": "已进行 {time}",
        "complete_head": "任务完成",
        "complete_time": "用时 {time}",
        "abandon": "任务结束",
        "auto_reset": "超时，自动重置",
    },
}

# ---------------- Windows API ----------------

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x02
TH32CS_SNAPMODULE = 0x08
TH32CS_SNAPMODULE32 = 0x10
STILL_ACTIVE = 259
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", ctypes.c_void_p),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    ]


kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32FirstW.restype = wintypes.BOOL
kernel32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32NextW.restype = wintypes.BOOL


def find_pid(name=PROCESS_NAME):
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return None
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    pid = None
    if kernel32.Process32FirstW(snap, ctypes.byref(entry)):
        while True:
            if entry.szExeFile.lower() == name.lower():
                pid = entry.th32ProcessID
                break
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break
    kernel32.CloseHandle(snap)
    return pid


def get_module(pid, name=PROCESS_NAME):
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == INVALID_HANDLE_VALUE:
        return None, None
    entry = MODULEENTRY32W()
    entry.dwSize = ctypes.sizeof(MODULEENTRY32W)
    base = size = None
    if kernel32.Module32FirstW(snap, ctypes.byref(entry)):
        while True:
            if entry.szModule.lower() == name.lower():
                base = entry.modBaseAddr or 0
                size = entry.modBaseSize
                break
            if not kernel32.Module32NextW(snap, ctypes.byref(entry)):
                break
    kernel32.CloseHandle(snap)
    return base, size


def open_process(pid):
    return kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)


def process_alive(hproc):
    code = wintypes.DWORD(0)
    if kernel32.GetExitCodeProcess(hproc, ctypes.byref(code)):
        return code.value == STILL_ACTIVE
    return False


def read_bytes(hproc, address, size):
    buf = ctypes.create_string_buffer(size)
    nread = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(
        hproc, ctypes.c_void_p(address), buf, size, ctypes.byref(nread))
    if not ok:
        return None
    return buf.raw[:nread.value]


def read_u8(hproc, addr):
    d = read_bytes(hproc, addr, 1)
    return None if (d is None or len(d) < 1) else d[0]


def read_i32(hproc, addr):
    d = read_bytes(hproc, addr, 4)
    return None if (d is None or len(d) < 4) else struct.unpack("<i", d[:4])[0]


def read_u64(hproc, addr):
    d = read_bytes(hproc, addr, 8)
    return None if (d is None or len(d) < 8) else struct.unpack("<Q", d[:8])[0]


def read_f32(hproc, addr):
    d = read_bytes(hproc, addr, 4)
    return None if (d is None or len(d) < 4) else struct.unpack("<f", d[:4])[0]


# ---------------- 触发按键（用于触发 IN-Q 重置） ----------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
user32.keybd_event.restype = None
KEYEVENTF_KEYUP = 0x0002

VK_CODES = {}
for _i in range(1, 13):
    VK_CODES["F%d" % _i] = 0x6F + _i   # F1=0x70 ... F12=0x7B
VK_CODES.update({"SPACE": 0x20, "ENTER": 0x0D, "TAB": 0x09, "ESC": 0x1B,
                 "INSERT": 0x2D, "DELETE": 0x2E, "HOME": 0x24, "END": 0x23,
                 "NUM0": 0x60, "NUM1": 0x61, "NUM2": 0x62, "NUM3": 0x63})


def send_key(vk, hold=0.04):
    """向系统注入一次按键（IN-Q 用的低级键盘钩子能收到合成按键）。"""
    user32.keybd_event(vk, 0, 0, None)
    time.sleep(hold)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, None)


def send_reset_key(cfg):
    """按配置的快捷键触发 IN-Q 重置；成功返回 True。"""
    name = str(cfg.get("reset_hotkey", "F11") or "F11").upper()
    vk = VK_CODES.get(name)
    if vk is None:
        print("[重置] 未知按键：%s" % name, flush=True)
        return False
    try:
        send_key(vk)
        return True
    except Exception as e:
        print("[重置] 发送按键失败：%s" % e, flush=True)
        return False


# ---------------- 特征码扫描 ----------------

CHUNK = 0x100000  # 1 MiB


def parse_pattern(sig):
    pat = bytearray()
    mask = bytearray()
    for tok in sig.split():
        if tok in ("??", "?"):
            pat.append(0)
            mask.append(1)
        else:
            pat.append(int(tok, 16))
            mask.append(0)
    return bytes(pat), bytes(mask)


def _build_regex(pat, mask):
    parts = []
    for b, m in zip(pat, mask):
        parts.append("." if m else "\\x%02x" % b)
    return re.compile(("".join(parts)).encode("latin-1"), re.DOTALL)


def scan_module(hproc, base, size, pat, mask):
    if base is None or not size:
        return None
    rx = _build_regex(pat, mask)
    overlap = len(pat) - 1
    off = 0
    while off < size:
        want = min(CHUNK + overlap, size - off)
        data = read_bytes(hproc, base + off, want)
        if data:
            m = rx.search(data)
            new_region = min(CHUNK, size - off)
            if m and m.start() < new_region:
                return base + off + m.start()
        off += CHUNK
    return None


def resolve_base_ptr(hproc, base, size, sig, offset):
    pat, mask = parse_pattern(sig)
    p = scan_module(hproc, base, size, pat, mask)
    if p is None:
        return None
    disp = read_i32(hproc, p + offset)
    if disp is None:
        return None
    return p + offset + 4 + disp


def resolve_bases(hproc, base, size):
    bases = {}
    if base is None or not size:
        return bases
    for name, (sig, off) in SIGNATURES.items():
        addr = resolve_base_ptr(hproc, base, size, sig, off)
        if addr is None:
            continue
        val = read_u64(hproc, addr)
        if val is None or val == 0:
            continue
        bases[name] = addr
    return bases


def deref(hproc, global_addr, offsets):
    """按 LiveSplit DeepPointer 语义解析：先解引用全局指针，逐级 +offset 解引用，最后加字段偏移。"""
    ptr = read_u64(hproc, global_addr)
    if ptr is None or ptr == 0:
        return None
    for i in range(len(offsets) - 1):
        ptr = read_u64(hproc, ptr + offsets[i])
        if ptr is None or ptr == 0:
            return None
    return ptr + offsets[-1]


def resolve_fields(hproc, bases):
    fields = {}
    for fname, (bname, offsets) in FIELDS.items():
        if bname not in bases:
            continue
        fields[fname] = deref(hproc, bases[bname], offsets)
    return fields


# ---------------- 语音 ----------------

_temp_counter = 0
_temp_lock = threading.Lock()


def _unique_mp3_path():
    global _temp_counter
    with _temp_lock:
        _temp_counter += 1
        return os.path.join(tempfile.gettempdir(), "mhw_tts_%d_%d.mp3" % (os.getpid(), _temp_counter))


def _edge_tts_to_file(text, voice, rate, pitch):
    """用 edge-tts 生成 mp3 到临时文件，返回路径；失败返回 None。"""
    try:
        import edge_tts
    except ImportError:
        return None
    path = _unique_mp3_path()

    async def _run():
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        with open(path, "wb") as f:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])

    try:
        asyncio.run(_run())
        return path if os.path.getsize(path) > 0 else None
    except Exception as e:
        print("[TTS] edge-tts 生成失败（将回退内置语音）：%s" % e, flush=True)
        return None


def _mci_play(path):
    """用 Windows MCI 播放音频（同步），返回是否成功。"""
    try:
        winmm = ctypes.WinDLL("winmm")
        winmm.mciSendStringW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        winmm.mciSendStringW.restype = ctypes.c_uint

        def mci(cmd):
            buf = ctypes.create_unicode_buffer(512)
            ret = winmm.mciSendStringW(cmd, buf, 512, None)
            return ret, buf.value

        ret, _ = mci('open "%s" type mpegvideo alias mhwtts' % path)
        if ret != 0:
            ret, _ = mci('open "%s" alias mhwtts' % path)  # 自动识别类型
        if ret != 0:
            return False
        mci("play mhwtts wait")
        mci("close mhwtts")
        return True
    except Exception as e:
        print("[TTS] 播放失败（将回退内置语音）：%s" % e, flush=True)
        return False


def _speak_sapi(text, voice, rate):
    esc = text.replace("'", "''")
    rate = max(-10, min(10, int(rate)))
    if voice:
        sel = "try{$s.SelectVoice('%s')}catch{}; " % voice.replace("'", "''")
    else:
        sel = ("try{$vs=@($s.GetInstalledVoices()); "
               "if($vs.Count -gt 0){$c=$vs|Where-Object{$_.VoiceInfo.Culture.Name -like 'zh*'}|Select-Object -First 1; "
               "if(-not $c){$c=$vs[0]}; $s.SelectVoice($c.VoiceInfo.Name)}}catch{}; ")
    script = ("Add-Type -AssemblyName System.Speech; "
              "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; " +
              sel + "$s.Rate=%d; $s.Speak('%s'); $s.Dispose()" % (rate, esc))
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
    try:
        subprocess.run(
            cmd,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print("[TTS] 内置语音调用失败：%s" % e, flush=True)


class TTS:
    """带缓存的 TTS：固定/可预测文案后台预生成并缓存，触发时零生成延迟。"""

    def __init__(self, cfg):
        self.cfg = cfg or {}
        self.cache = {}
        self.lock = threading.Lock()

    def _generate(self, text):
        if (self.cfg.get("engine") or "edge").lower() != "edge":
            return None
        return _edge_tts_to_file(text,
                                 self.cfg.get("edge_voice", "zh-CN-XiaoxiaoNeural"),
                                 self.cfg.get("edge_rate", "+0%"),
                                 self.cfg.get("edge_pitch", "+0Hz"))

    def pregen(self, text):
        if not text:
            return
        with self.lock:
            p = self.cache.get(text)
            if p and os.path.exists(p):
                return
        p = self._generate(text)
        if p:
            with self.lock:
                self.cache[text] = p

    def speak(self, text):
        with self.lock:
            p = self.cache.get(text)
        if p and os.path.exists(p):
            _mci_play(p)
            return
        p = self._generate(text)
        if p:
            _mci_play(p)
            try:
                os.remove(p)
            except OSError:
                pass
            return
        _speak_sapi(text, self.cfg.get("sapi_voice", "Microsoft Huihui Desktop"), self.cfg.get("sapi_rate", 0))


def _speak_worker(q, tts):
    while True:
        text = q.get()
        if text is None:
            break
        try:
            tts.speak(text)
        except Exception as e:
            print("[TTS] 播放异常：%s" % e, flush=True)


def _pregen_worker(q, speak_q, tts):
    while True:
        item = q.get()
        if item is None:
            break
        text, then_speak = item
        try:
            tts.pregen(text)
        except Exception as e:
            print("[TTS] 预生成异常：%s" % e, flush=True)
        if then_speak:
            speak_q.put(text)  # 已入缓存，播放零延迟


def start_tts(cfg):
    tts = TTS(cfg)
    speak_q = queue.Queue()
    pregen_q = queue.Queue()
    threading.Thread(target=_speak_worker, args=(speak_q, tts), daemon=True).start()
    threading.Thread(target=_pregen_worker, args=(pregen_q, speak_q, tts), daemon=True).start()
    return speak_q, pregen_q


# ---------------- 工具 ----------------

def format_time(sec, precision=1):
    if sec is None or sec < 0:
        sec = 0
    if sec < 60:
        return ("%." + str(precision) + "f 秒") % sec
    m = int(sec // 60)
    s = sec - m * 60
    return ("%d 分 %." + str(precision) + "f 秒") % (m, s)


def _deep_update(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _deep_update(cfg, json.load(f))
        except Exception as e:
            print("[配置] 读取失败，使用默认配置：%s" % e, flush=True)
    return cfg


# ---------------- 主循环 ----------------

END_STREAK = 10         # 任务 ID 连续 <=0 达到该次数（约 1 秒）才判定任务结束


def _wait(sec, stop_event):
    """可中断的等待；返回 True 表示收到停止信号。"""
    if stop_event is None:
        time.sleep(sec)
        return False
    return stop_event.wait(sec)


def run_timer(cfg, stop_event=None, status=None):
    speak_q, pregen_q = start_tts(cfg.get("tts", {}))
    interval = max(1, int(cfg.get("interval_seconds", 60)))
    precision = max(0, min(3, int(cfg.get("precision", 1))))
    texts = cfg.get("texts", DEFAULT_CONFIG["texts"])
    reset_on_load = bool(cfg.get("reset_on_load", True))
    try:
        completion_offset = max(0.0, float(cfg.get("completion_offset_seconds", 0.45)))
    except (TypeError, ValueError):
        completion_offset = 0.45
    try:
        auto_reset_seconds = max(0.0, float(cfg.get("auto_reset_seconds", 0) or 0))
    except (TypeError, ValueError):
        auto_reset_seconds = 0.0

    def set_status(**kw):
        if status is not None:
            status.update(kw)

    # 预生成固定/首条文案，播放零生成延迟
    pregen_q.put((texts.get("start", "任务开始"), False))
    pregen_q.put((texts.get("abandon", "任务结束"), False))
    pregen_q.put((texts.get("complete_head", "任务完成"), False))
    pregen_q.put((texts.get("elapsed", "已进行 {time}").format(time=format_time(interval, precision)), False))

    pid = None
    hproc = None
    base = size = None
    bases = {}
    fields = {}

    in_quest = False
    loaded_once = False
    completed = False
    completing = False
    completion_deadline = 0.0
    ticking = False
    accumulated = 0.0
    tick_start = 0.0
    last_interval_n = 0
    prev_qid = 0
    prev_obj = 0
    prev_loading = False
    out_streak = 0
    read_fail_streak = 0
    last_field_resolve = 0.0
    auto_reset_done = False

    set_status(connected=False, resolved=False, in_quest=False, quest_id=0,
               elapsed=0.0, completed=False, auto_reset_seconds=auto_reset_seconds,
               auto_reset_triggered=False, last_event="启动中")
    print("MHW 竞速语音计时器已启动，等待游戏 %s ..." % PROCESS_NAME, flush=True)

    try:
        while True:
            if hproc is None or not process_alive(hproc):
                if hproc:
                    kernel32.CloseHandle(hproc)
                    hproc = None
                pid = find_pid()
                if pid is None:
                    bases, fields = {}, {}
                    in_quest = completed = completing = False
                    ticking = False
                    accumulated = 0.0
                    auto_reset_done = False
                    set_status(connected=False, resolved=False, in_quest=False, elapsed=0.0, last_event="等待游戏")
                    if _wait(2, stop_event):
                        break
                    continue
                hproc = open_process(pid)
                base, size = get_module(pid)
                bases, fields = {}, {}
                set_status(connected=True, pid=pid, resolved=False, last_event="已连接游戏")
                print("已附加到 %s (PID %d)" % (PROCESS_NAME, pid), flush=True)

            if not fields:
                if not base or not size:
                    base, size = get_module(pid)
                if not base or not size:
                    print("等待游戏主模块加载...", flush=True)
                    if _wait(2, stop_event):
                        break
                    continue
                bases = resolve_bases(hproc, base, size)
                if len(bases) < len(SIGNATURES):
                    print("特征码定位中 %d/%d（游戏可能仍在解包），5 秒后重试..."
                          % (len(bases), len(SIGNATURES)), flush=True)
                    if _wait(5, stop_event):
                        break
                    continue
                fields = resolve_fields(hproc, bases)
                ok = [f for f, a in fields.items() if a]
                print("特征码定位完成，字段解析 %d/%d。" % (len(ok), len(FIELDS)), flush=True)
                last_field_resolve = time.perf_counter()
                set_status(resolved=True, last_event="特征码定位完成")

            qid = read_i32(hproc, fields["quest_id"]) if fields.get("quest_id") else None
            obj = read_u8(hproc, fields["obj1_state"]) if fields.get("obj1_state") else None
            load = read_u8(hproc, fields["load_state"]) if fields.get("load_state") else None

            if qid is None:
                read_fail_streak += 1
                if read_fail_streak > 100:
                    print("字段读取持续失败，重新扫描特征码...", flush=True)
                    bases, fields = {}, {}
                    read_fail_streak = 0
                if _wait(0.02, stop_event):
                    break
                continue
            read_fail_streak = 0

            obj = 0 if obj is None else obj
            load = 0 if load is None else load
            loading = load != 0
            now = time.perf_counter()

            # 周期性重解析字段地址：游戏启动初期指针链可能尚未建全，稍后会稳定
            if fields and now - last_field_resolve > 3.0:
                new_fields = resolve_fields(hproc, bases)
                if new_fields and any(new_fields.get(f) for f in ("quest_id", "obj1_state", "load_state")):
                    fields = new_fields
                last_field_resolve = now

            if qid > 0:
                out_streak = 0
                if not in_quest:
                    in_quest = True
                    loaded_once = False
                    completed = False
                    completing = False
                    accumulated = 0.0
                    ticking = False
                    last_interval_n = 0
                    auto_reset_done = False
                    set_status(in_quest=True, quest_id=qid, completed=False,
                               auto_reset_triggered=False, last_event="检测到任务 ID=%d" % qid)
                    print("检测到任务（ID=%d）" % qid, flush=True)

                # 读条结束 → 新的一局（初次进图 / IN-Q 快速重置）
                if loading and not prev_loading:
                    print("[状态] 读条开始 (qid=%d)" % qid, flush=True)
                if not loading and prev_loading:
                    print("[状态] 读条结束 (qid=%d, reset_on_load=%s, loaded_once=%s)"
                          % (qid, reset_on_load, loaded_once), flush=True)
                    if not loaded_once or reset_on_load:
                        loaded_once = True
                        completed = False
                        completing = False
                        accumulated = 0.0
                        ticking = False
                        last_interval_n = 0
                        auto_reset_done = False
                        set_status(in_quest=True, quest_id=qid, completed=False, elapsed=0.0,
                                   auto_reset_triggered=False, last_event="任务（重新）开始")
                        if cfg.get("announce_start", True):
                            speak_q.put(texts.get("start", "任务开始"))
                            print("[语音] %s" % texts.get("start", "任务开始"), flush=True)
                        print("任务（重新）开始（任务 ID=%d）" % qid, flush=True)
            else:
                out_streak += 1
                if in_quest and out_streak >= END_STREAK:
                    if not completed and cfg.get("announce_abandon", False):
                        speak_q.put(texts.get("abandon", "任务结束"))
                        print("[语音] %s" % texts.get("abandon", "任务结束"), flush=True)
                    in_quest = False
                    completed = False
                    completing = False
                    ticking = False
                    accumulated = 0.0
                    loaded_once = False
                    last_interval_n = 0
                    auto_reset_done = False
                    set_status(in_quest=False, completed=False, elapsed=0.0, last_event="任务已结束")
                    print("任务已结束。", flush=True)

            # 秒表：任务中且非读条且未完成时走表
            should_tick = in_quest and not loading and not completed
            if should_tick and not ticking:
                ticking = True
                tick_start = now
            elif not should_tick and ticking:
                ticking = False
                accumulated += now - tick_start

            # 完成判定：主目标1状态 0/1/... -> 5 的边沿；游戏计时器要再过 offset 秒才冻结
            if in_quest and not completed and not completing and obj == 5 and prev_obj != 5:
                completing = True
                completion_deadline = now + completion_offset

            if completing and now >= completion_deadline:
                completing = False
                completed = True
                if ticking:
                    accumulated += now - tick_start
                    ticking = False
                elapsed = accumulated
                head = texts.get("complete_head", "任务完成")
                time_msg = texts.get("complete_time", "用时 {time}").format(time=format_time(elapsed, precision))
                speak_q.put(head)              # 缓存命中 → 立即播“任务完成”
                pregen_q.put((time_msg, True))  # 后台生成“用时 X”，生成完自动播
                print("[语音] %s %s" % (head, time_msg), flush=True)
                set_status(completed=True, elapsed=elapsed,
                           last_event="任务完成 %.1f 秒" % elapsed)

            # 间隔播报（整点播报，并预生成下一条）
            if should_tick and not completed and not completing:
                elapsed = accumulated + (now - tick_start if ticking else 0.0)
                n = int(elapsed // interval)
                if n > last_interval_n:
                    last_interval_n = n
                    snapped = n * interval
                    msg = texts.get("elapsed", "已进行 {time}").format(time=format_time(snapped, precision))
                    speak_q.put(msg)
                    nxt = texts.get("elapsed", "已进行 {time}").format(time=format_time((n + 1) * interval, precision))
                    pregen_q.put((nxt, False))  # 预生成下一条，到点零延迟
                    print("[语音] %s" % msg, flush=True)

            # 定时重置：任务进行到设定秒数，自动触发 IN-Q 重置
            if (auto_reset_seconds > 0 and should_tick and not completed and not completing
                    and not auto_reset_done):
                elapsed = accumulated + (now - tick_start if ticking else 0.0)
                if elapsed >= auto_reset_seconds:
                    auto_reset_done = True
                    if send_reset_key(cfg):
                        print("[重置] 已到 %.1f 秒，自动触发重置" % auto_reset_seconds, flush=True)
                        speak_q.put(texts.get("auto_reset", "超时，自动重置"))
                        set_status(auto_reset_triggered=True,
                                   last_event="已自动重置（%.0f 秒）" % auto_reset_seconds)

            # 更新状态供 UI 读取
            if status is not None:
                status["elapsed"] = accumulated + (now - tick_start if ticking else 0.0)
                status["in_quest"] = in_quest
                status["quest_id"] = qid
                status["completed"] = completed
                status["loading"] = loading

            prev_qid = qid
            prev_obj = obj
            prev_loading = loading
            if _wait(0.02, stop_event):
                break
    except KeyboardInterrupt:
        print("\n已退出。", flush=True)
    finally:
        if hproc:
            kernel32.CloseHandle(hproc)


# ---------------- 诊断 ----------------

def _wait_attach():
    while True:
        pid = find_pid()
        if pid is None:
            print("未找到 %s，等待游戏启动...（Ctrl+C 退出）" % PROCESS_NAME, flush=True)
            time.sleep(2)
            continue
        hproc = open_process(pid)
        base, size = get_module(pid)
        print("PID=%d  模块基址=0x%X  大小=0x%X" % (pid, base, size), flush=True)
        return hproc, base, size


def diagnose(cfg):
    print("诊断模式：定位特征码并实时打印字段（游戏需已运行）。", flush=True)
    hproc, base, size = _wait_attach()
    try:
        while True:
            bases = resolve_bases(hproc, base, size)
            if len(bases) < len(SIGNATURES):
                print("特征码定位中 %d/%d，等待解包..." % (len(bases), len(SIGNATURES)), flush=True)
                time.sleep(5)
                continue
            fields = resolve_fields(hproc, bases)
            print("\n== 全局指针 ==", flush=True)
            for name in SIGNATURES:
                addr = bases.get(name)
                val = read_u64(hproc, addr) if addr else None
                print("  %-12s 地址=0x%X  指向=0x%X" % (name, addr or 0, val or 0), flush=True)
            print("== 字段 ==", flush=True)
            for fname in FIELDS:
                a = fields.get(fname)
                print("  %-12s 地址=%s" % (fname, ("0x%X" % a) if a else "未解析"), flush=True)
            print("\n实时字段（每秒刷新）：", flush=True)
            break
        while True:
            qid = read_i32(hproc, fields["quest_id"]) if fields.get("quest_id") else None
            obj = read_u8(hproc, fields["obj1_state"]) if fields.get("obj1_state") else None
            load = read_u8(hproc, fields["load_state"]) if fields.get("load_state") else None
            cut = read_i32(hproc, fields["cutscene"]) if fields.get("cutscene") else None
            area = read_i32(hproc, fields["area_id"]) if fields.get("area_id") else None
            town = "（城镇）" if (area is not None and 301 <= area <= 307) else ""
            print("quest_id=%s obj1_state=%s load_state=%s cutscene=%s area_id=%s%s"
                  % (qid, obj, load, cut, area, town), flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n诊断结束。", flush=True)
    finally:
        if hproc:
            kernel32.CloseHandle(hproc)


# ---------------- 入口 ----------------

def parse_args():
    ap = argparse.ArgumentParser(description="MHW 竞速语音计时器")
    ap.add_argument("--config", default=None, help="配置文件路径（默认同目录 config.json）")
    ap.add_argument("--diagnose", action="store_true", help="诊断模式")
    ap.add_argument("--test-tts", metavar="文字", default=None, help="测试语音引擎并退出")
    ap.add_argument("--console", action="store_true", help="强制控制台模式（无界面）")
    ap.add_argument("--tray", action="store_true", help="托盘模式（无图形界面）")
    return ap.parse_args()


def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _setup_io():
    """无控制台（windowed）时把输出重定向到日志文件，避免 print 崩溃。"""
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass
    if sys.stdout is None or sys.stderr is None:
        try:
            log = open(os.path.join(_app_dir(), "mhw_timer.log"), "a", encoding="utf-8")
            if sys.stdout is None:
                sys.stdout = log
            if sys.stderr is None:
                sys.stderr = log
        except Exception:
            pass


def load_tray_image():
    if _PILImage is None:
        return None
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(getattr(sys, "_MEIPASS", ""), "app.ico"))
    candidates.append(os.path.join(_app_dir(), "app.ico"))
    for p in candidates:
        try:
            if os.path.exists(p):
                img = _PILImage.open(p).convert("RGBA")
                img.thumbnail((64, 64), _PILImage.LANCZOS)
                return img
        except Exception:
            continue
    return _PILImage.new("RGBA", (64, 64), (232, 163, 61, 255))


def run_tray(cfg):
    stop = threading.Event()
    t = threading.Thread(target=run_timer, args=(cfg, stop), daemon=True, name="mhw-timer")
    t.start()

    def on_exit(icon, item):
        stop.set()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("MHW 竞速语音计时器（运行中）", None, enabled=False),
        pystray.MenuItem("退出", on_exit),
    )
    icon = pystray.Icon("mhw_timer", load_tray_image(), "MHW 竞速语音计时器", menu)
    icon.run()
    stop.set()


def main():
    _setup_io()
    args = parse_args()
    cfg_path = args.config or os.path.join(_app_dir(), "config.json")
    cfg = load_config(cfg_path)

    if args.test_tts is not None:
        TTS(cfg.get("tts", {})).speak(args.test_tts)
        print("已尝试播报：%s" % args.test_tts, flush=True)
        return

    if args.diagnose:
        diagnose(cfg)
        return

    if args.console:
        run_timer(cfg, None)
        return

    if args.tray:
        if _HAS_TRAY:
            run_tray(cfg)
        else:
            run_timer(cfg, None)
        return

    # 默认：图形界面；失败则回退托盘/命令行
    try:
        from mhw_ui import run_ui
        run_ui(cfg, cfg_path)
        return
    except Exception as e:
        print("[UI] 图形界面启动失败，回退无界面模式：%s" % e, flush=True)

    if _HAS_TRAY:
        run_tray(cfg)
    else:
        run_timer(cfg, None)


if __name__ == "__main__":
    main()
