# app.py
# -*- coding: utf-8 -*-
"""OPM 实时播放器 —— 图形界面版（tkinter，零额外依赖）。

    python app.py                     # 打开窗口
    python app.py --lang en           # 英文界面
    python app.py --selfcheck         # 不开窗口，检查运行环境（EXE 打包后可用）

EXE 里**不包含任何 PCM 鼓采样**（第三方录音版权）。要听 PCM 鼓，
在界面上指定自己的采样目录；没有就自动退回 SSG / FM 鼓。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from player_session import BASE, Config, DRUM_MODES, SEARCH_DIRS, Session

APP_TITLE = "OPM Player"

# ---------------------------------------------------------------- 文案
STRINGS = {
    "zh": {
        "title": "OPM 实时播放器",
        "midi": "MIDI",
        "bank": "音色库",
        "chip": "芯片模式",
        "drums": "鼓模式",
        "drum_dir": "PCM 目录",
        "volume": "音量",
        "volume_hint": "（播放中实时生效）",
        "browse": "浏览…",
        "play": "▶ 播放",
        "stop": "■ 停止",
        "lang": "语言",
        "drum_fm": "FM 鼓（DRUM_BANK 硬编码）",
        "drum_ssg": "SSG 鼓（GAI2 原配方）",
        "drum_ssg_clean": "SSG 鼓（修正配方）",
        "drum_pcm": "PCM 采样鼓（需指定目录）",
        "drum_ssg_pcm": "SSG + PCM 混合（SSG 主鼓 / PCM 其余）",
        "drum_ssg_clean_pcm": "SSG(修正) + PCM 混合",
        "drum_oki": "OKI ADPCM 鼓（6258 / 6295）",
        "oki": "OKI 芯片",
        "oki_hint": "6258: 4000000 · 6295: 1056000",
        "export": "导出 WAV…",
        "export_t": "导出 WAV",
        "export_done": "已导出：\n{path}\n\n{sr} Hz / {sec:.1f}s / peak {peak}/32767",
        "export_cancel": "导出已取消",
        "export_busy": "正在导出 {pct}%…",
        "loop": "循环播放",
        "translate": "翻译层",
        "translate_hint": "（X68K 模式建议关闭）",
        "no_bank": "（没有找到 .opm）",
        "env_missing_pcm": "PCM 采样目录缺失 → 会自动退回 SSG/FM 鼓",
        "err_no_midi": "先选一个 MIDI 文件。",
        "err_no_midi_t": "缺少 MIDI",
        "err_no_bank": "先选一个 .opm 音色库。",
        "err_no_bank_t": "缺少音色库",
        "err_play": "播放失败",
        "warn_pcm_t": "PCM 鼓",
        "warn_pcm": ("还没指定可用的采样目录。\n\n"
                     "播放器不会自带任何鼓采样（版权原因），\n"
                     "请指向你自己的 drum_samples 目录；\n"
                     "不指定的话会自动退回 SSG / FM 鼓。"),
        "status_playing": "音色库 {bank} | 芯片 {chip} | 鼓 {drums} | {n} 事件 {sec:.1f}s",
        "xrun": "underrun={u}  xrun={x}",
        "ready": "就绪",
    },
    "en": {
        "title": "OPM Realtime Player",
        "midi": "MIDI",
        "bank": "Voice bank",
        "chip": "Chip mode",
        "drums": "Drum engine",
        "drum_dir": "PCM folder",
        "volume": "Volume",
        "volume_hint": "(applies live)",
        "browse": "Browse…",
        "play": "▶ Play",
        "stop": "■ Stop",
        "lang": "Language",
        "drum_fm": "FM drums (hard-coded DRUM_BANK)",
        "drum_ssg": "SSG drums (GAI2 recipe)",
        "drum_ssg_clean": "SSG drums (corrected)",
        "drum_pcm": "PCM samples (needs a folder)",
        "drum_ssg_pcm": "SSG + PCM hybrid (SSG keeps kick/snare/hat)",
        "drum_ssg_clean_pcm": "SSG (corrected) + PCM hybrid",
        "drum_oki": "OKI drums (ADPCM 6258/6295)",
        "oki": "OKI chip",
        "oki_hint": "6258: 4000000 · 6295: 1056000",
        "export": "Export WAV…",
        "export_t": "Export WAV",
        "export_done": "Exported:\n{path}\n\n{sr} Hz / {sec:.1f}s / peak {peak}/32767",
        "export_cancel": "Export cancelled",
        "export_busy": "Exporting {pct}%…",
        "loop": "Loop playback",
        "translate": "Translation layer",
        "translate_hint": "(turn off for X68K)",
        "no_bank": "(no .opm found)",
        "env_missing_pcm": "PCM sample folder missing → falls back to SSG/FM drums",
        "err_no_midi": "Pick a MIDI file first.",
        "err_no_midi_t": "Missing MIDI",
        "err_no_bank": "Pick an .opm voice bank first.",
        "err_no_bank_t": "Missing voice bank",
        "err_play": "Playback failed",
        "warn_pcm_t": "PCM drums",
        "warn_pcm": ("No usable sample folder selected.\n\n"
                     "This player ships no drum samples (copyright),\n"
                     "so point it at your own drum_samples folder.\n"
                     "Otherwise it falls back to SSG / FM drums."),
        "status_playing": "bank {bank} | chip {chip} | drums {drums} | {n} events {sec:.1f}s",
        "xrun": "underrun={u}  xrun={x}",
        "ready": "Ready",
    },
}

DRUM_KEYS = {"fm": "drum_fm", "ssg": "drum_ssg", "ssg-clean": "drum_ssg_clean",
             "pcm": "drum_pcm", "ssg+pcm": "drum_ssg_pcm",
             "ssg-clean+pcm": "drum_ssg_clean_pcm", "oki": "drum_oki"}


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lang = cfg.lang if cfg.lang in STRINGS else "zh"
        self.session: Session | None = None
        self._exp = {"running": False, "frac": 0.0, "done": None}
        self._exp_cancel = threading.Event()
        self.root = tk.Tk()
        self.root.geometry("760x460")
        self.root.minsize(700, 430)
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._poll()

    # ---------------- i18n ----------------
    def t(self, key: str) -> str:
        return STRINGS[self.lang].get(key, key)

    def drum_label(self, mode: str) -> str:
        return self.t(DRUM_KEYS.get(mode, "drum_fm"))

    def mode_from_label(self, label: str) -> str:
        for mode, key in DRUM_KEYS.items():
            if STRINGS[self.lang][key] == label:
                return mode
        return "fm"

    # ---------------- UI ----------------
    def _build(self):
        self.root.title(f"{self.t('title')}  ·  {os.path.basename(self.cfg.bank) or ''}")
        if getattr(self, "frm", None) is not None:
            self.frm.destroy()
        if getattr(self, "status_bar", None) is not None:
            self.status_bar.destroy()

        pad = {"padx": 6, "pady": 4}
        frm = ttk.Frame(self.root, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        self.frm = frm

        def row(r, text):
            ttk.Label(frm, text=text).grid(row=r, column=0, sticky="w", **pad)

        # MIDI
        row(0, self.t("midi"))
        self.v_midi = tk.StringVar(value=self.v_midi.get() if hasattr(self, "v_midi") else self.cfg.midi)
        ttk.Entry(frm, textvariable=self.v_midi).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(frm, text=self.t("browse"), command=self.pick_midi).grid(row=0, column=2, **pad)

        # 音色库
        row(1, self.t("bank"))
        banks = self._list_banks()
        cur = self.cfg.bank or (banks[0] if banks else "")
        self.v_bank = tk.StringVar(value=cur)
        cb = ttk.Combobox(frm, textvariable=self.v_bank, state="readonly", values=banks)
        cb.grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(frm, text=self.t("browse"), command=self.pick_bank).grid(row=1, column=2, **pad)

        # 芯片
        row(2, self.t("chip"))
        self.v_chip = tk.StringVar(value=self.cfg.chip)
        bar = ttk.Frame(frm)
        bar.grid(row=2, column=1, columnspan=2, sticky="w", **pad)
        for key, label in (("opm", "OPM · YM2151"), ("opp", "OPP · YM2164"), ("opz", "OPZ · YM2414")):
            ttk.Radiobutton(bar, text=label, value=key, variable=self.v_chip,
                            command=self.on_chip).pack(side="left", padx=6)

        # 鼓
        row(3, self.t("drums"))
        self.v_drums = tk.StringVar(value=self.drum_label(self.cfg.drums))
        dcb = ttk.Combobox(frm, textvariable=self.v_drums, state="readonly",
                           values=[self.drum_label(m) for m in DRUM_MODES])
        dcb.grid(row=3, column=1, sticky="ew", **pad)
        dcb.bind("<<ComboboxSelected>>", lambda _e: self.on_drums())

        # 采样目录
        row(4, self.t("drum_dir"))
        self.v_dir = tk.StringVar(value=self.cfg.drum_dir)
        ttk.Entry(frm, textvariable=self.v_dir).grid(row=4, column=1, sticky="ew", **pad)
        ttk.Button(frm, text=self.t("browse"), command=self.pick_dir).grid(row=4, column=2, **pad)

        # OKI 芯片 / 时钟
        row(5, self.t("oki"))
        oki_bar = ttk.Frame(frm)
        oki_bar.grid(row=5, column=1, columnspan=2, sticky="w", **pad)
        self.v_oki_chip = tk.StringVar(value=self.cfg.oki_chip)
        cb_oki = ttk.Combobox(oki_bar, textvariable=self.v_oki_chip, state="readonly",
                              values=["6295", "6258"], width=6)
        cb_oki.pack(side="left")
        cb_oki.bind("<<ComboboxSelected>>", lambda _e: self.on_oki())
        ttk.Label(oki_bar, text="clock").pack(side="left", padx=(10, 4))
        self.v_oki_clock = tk.StringVar(value=str(self.cfg.oki_clock))
        e_clock = ttk.Entry(oki_bar, textvariable=self.v_oki_clock, width=10)
        e_clock.pack(side="left")
        e_clock.bind("<FocusOut>", lambda _e: self.on_oki())
        ttk.Label(oki_bar, text=self.t("oki_hint")).pack(side="left", padx=(10, 0))

        # 音量
        row(6, self.t("volume"))
        self.v_vol = tk.DoubleVar(value=self.cfg.volume)
        ttk.Scale(frm, from_=0.0, to=1.5, variable=self.v_vol,
                  command=self.on_volume).grid(row=6, column=1, sticky="ew", **pad)
        ttk.Label(frm, text=self.t("volume_hint")).grid(row=6, column=2, sticky="w", **pad)

        # 语言
        row(7, self.t("lang"))
        self.v_lang = tk.StringVar(value="中文" if self.lang == "zh" else "English")
        lcb = ttk.Combobox(frm, textvariable=self.v_lang, state="readonly",
                           values=["中文", "English"], width=10)
        lcb.grid(row=7, column=1, sticky="w", **pad)
        lcb.bind("<<ComboboxSelected>>", lambda _e: self.on_lang())

        # 控制
        ctl = ttk.Frame(frm)
        ctl.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(10, 2))
        self.btn_play = ttk.Button(ctl, text=self.t("play"), command=self.on_play)
        self.btn_play.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(ctl, text=self.t("stop"), command=self.on_stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        self.btn_export = ttk.Button(ctl, text=self.t("export"), command=self.on_export)
        self.btn_export.pack(side="left", padx=4)

        # 循环 / 翻译层
        loop_bar = ttk.Frame(frm)
        loop_bar.grid(row=9, column=0, columnspan=3, sticky="w", **pad)
        self.v_loop = tk.BooleanVar(value=self.cfg.loop)
        ttk.Checkbutton(loop_bar, text=self.t("loop"), variable=self.v_loop,
                        command=self.on_loop).pack(side="left")
        self.v_trans = tk.BooleanVar(value=not self.cfg.raw)
        ttk.Checkbutton(loop_bar, text=self.t("translate"), variable=self.v_trans,
                        command=self.on_translate).pack(side="left", padx=24)
        ttk.Label(loop_bar, text=self.t("translate_hint")).pack(side="left")

        # 进度
        self.v_prog = tk.DoubleVar(value=0.0)
        ttk.Progressbar(frm, variable=self.v_prog, maximum=100).grid(
            row=10, column=0, columnspan=3, sticky="ew", **pad)
        self.v_time = tk.StringVar(value="0.0 / 0.0 s")
        ttk.Label(frm, textvariable=self.v_time).grid(row=11, column=0, columnspan=2, sticky="w", **pad)

        # 状态栏
        self.v_status = tk.StringVar(value=self._env_status())
        bar2 = ttk.Label(self.root, textvariable=self.v_status, relief="sunken",
                         anchor="w", padding=4)
        bar2.pack(fill="x", side="bottom")
        self.status_bar = bar2

    def _list_banks(self):
        paths = []
        for d in SEARCH_DIRS:
            paths += glob.glob(os.path.join(d, "*.opm"))
        return sorted(dict.fromkeys(paths)) or [self.t("no_bank")]

    def _env_status(self) -> str:
        parts = [f"{BASE}"]
        if not os.path.isdir(self.cfg.drum_dir or ""):
            parts.append(self.t("env_missing_pcm"))
        return " | ".join(parts)

    # ---------------- 交互 ----------------
    def pick_midi(self):
        p = filedialog.askopenfilename(filetypes=[("MIDI", "*.mid *.midi"), ("All", "*.*")])
        if p:
            self.v_midi.set(p)

    def pick_bank(self):
        p = filedialog.askopenfilename(initialdir=BASE, filetypes=[("OPM bank", "*.opm")])
        if p:
            self.v_bank.set(p)
            self.cfg.bank = p

    def pick_dir(self):
        p = filedialog.askdirectory(initialdir=self.v_dir.get() or BASE)
        if p:
            self.v_dir.set(p)
            self.on_drums()

    def on_chip(self):
        self.cfg.chip = self.v_chip.get()

    def on_drums(self):
        self.cfg.drums = self.mode_from_label(self.v_drums.get())
        if self.cfg.drums == "pcm" and not os.path.isdir(self.v_dir.get() or ""):
            messagebox.showwarning(self.t("warn_pcm_t"), self.t("warn_pcm"))

    def on_oki(self):
        self.cfg.oki_chip = self.v_oki_chip.get() or "6295"
        try:
            self.cfg.oki_clock = max(100_000, int(float(self.v_oki_clock.get())))
        except (TypeError, ValueError):
            self.v_oki_clock.set(str(self.cfg.oki_clock))
        self.cfg.save()

    def on_volume(self, _v=None):
        self.cfg.volume = float(self.v_vol.get())
        if self.session and self.session.renderer:
            self.session.renderer.master = self.cfg.volume

    def on_lang(self):
        self.lang = "en" if self.v_lang.get() == "English" else "zh"
        self.cfg.lang = self.lang
        self.cfg.save()
        self._build()

    # ---------------- 播放 ----------------
    def _cfg_from_ui(self) -> Config:
        c = Config(**{k: getattr(self.cfg, k) for k in self.cfg.__dataclass_fields__})
        c.midi = self.v_midi.get().strip()
        c.bank = self.v_bank.get().strip()
        c.chip = self.v_chip.get()
        c.drums = self.mode_from_label(self.v_drums.get())
        c.drum_dir = self.v_dir.get().strip()
        c.volume = float(self.v_vol.get())
        c.lang = self.lang
        c.raw = not bool(self.v_trans.get())
        c.oki_chip = self.v_oki_chip.get() or "6295"
        try:
            c.oki_clock = max(100_000, int(float(self.v_oki_clock.get())))
        except (TypeError, ValueError):
            c.oki_clock = self.cfg.oki_clock
        return c

    def on_play(self):
        cfg = self._cfg_from_ui()
        if not cfg.midi or not os.path.exists(cfg.midi):
            messagebox.showerror(self.t("err_no_midi_t"), self.t("err_no_midi"))
            return
        if not cfg.bank or not os.path.exists(cfg.bank):
            messagebox.showerror(self.t("err_no_bank_t"), self.t("err_no_bank"))
            return
        self.on_stop()
        try:
            self.session = Session(cfg)
            self.session.build()
            self.session.start()
        except Exception as e:
            self.session = None
            messagebox.showerror(self.t("err_play"),
                                 f"{type(e).__name__}: {e}\n\n{traceback.format_exc(limit=2)}")
            return
        self.cfg = cfg
        cfg.save()
        self.btn_play["state"] = "disabled"
        self.btn_stop["state"] = "normal"
        s = self.session
        pcm = f" (PCM {s.pcm_notes})" if s.pcm else ""
        self.v_status.set(self.t("status_playing").format(
            bank=os.path.basename(cfg.bank), chip=cfg.chip,
            drums=cfg.drums + pcm, n=s.n_events, sec=s.total_sec))

    def on_stop(self):
        if self._exp["running"]:
            self._exp_cancel.set()
        if self.session:
            self.session.close()
            self.session = None
        self.btn_play["state"] = "normal"
        self.btn_stop["state"] = "disabled"
        self.v_prog.set(0.0)

    # ---------------- 导出 WAV ----------------
    def on_export(self):
        cfg = self._cfg_from_ui()
        if not cfg.midi or not os.path.exists(cfg.midi):
            messagebox.showerror(self.t("err_no_midi_t"), self.t("err_no_midi"))
            return
        if not cfg.bank or not os.path.exists(cfg.bank):
            messagebox.showerror(self.t("err_no_bank_t"), self.t("err_no_bank"))
            return
        if self._exp["running"]:
            return
        default = os.path.splitext(os.path.basename(cfg.midi))[0] + ".wav"
        path = filedialog.asksaveasfilename(
            defaultextension=".wav", initialfile=default,
            filetypes=[("WAV", "*.wav")], initialdir=os.path.dirname(cfg.midi) or BASE)
        if not path:
            return

        self.on_stop()
        self.cfg = cfg
        cfg.save()
        self._exp.update(running=True, frac=0.0, done=None)
        self._exp_cancel.clear()
        self.btn_play["state"] = "disabled"
        self.btn_export["state"] = "disabled"
        threading.Thread(target=self._export_worker, args=(cfg, path), daemon=True).start()

    def _export_worker(self, cfg, path):
        from player_session import render_wav
        try:
            info = render_wav(cfg, path,
                              progress=lambda f: self._exp.__setitem__("frac", f),
                              cancelled=self._exp_cancel.is_set)
            self._exp["done"] = info
        except Exception as e:
            self._exp["done"] = {"error": f"{type(e).__name__}: {e}"}
        finally:
            self._exp["running"] = False

    # ---------------- 循环 ----------------
    def on_loop(self):
        self.cfg.loop = bool(self.v_loop.get())
        self.cfg.save()

    def on_translate(self):
        self.cfg.raw = not bool(self.v_trans.get())
        self.cfg.save()

    def _poll_export(self):
        if self._exp["running"]:
            self.v_prog.set(self._exp["frac"] * 100)
            self.v_status.set(self.t("export_busy").format(pct=int(self._exp["frac"] * 100)))
            return
        info = self._exp.get("done")
        if not info:
            return
        self._exp["done"] = None
        self.btn_play["state"] = "normal"
        self.btn_export["state"] = "normal"
        if "error" in info:
            messagebox.showerror(self.t("export_t"), info["error"])
        elif not info.get("complete"):
            messagebox.showinfo(self.t("export_t"), self.t("export_cancel"))
        else:
            messagebox.showinfo(self.t("export_t"), self.t("export_done").format(
                path=info["path"], sr=info["sr"], sec=info["seconds"], peak=info["peak"]))
        self.v_status.set(self._env_status())

    def _poll(self):
        self._poll_export()
        s = self.session
        if s and s.total_sec > 0 and not self._exp["running"]:
            pos = s.position
            self.v_prog.set(min(100.0, pos / s.total_sec * 100))
            self.v_time.set(f"{pos:.1f} / {s.total_sec:.1f} s   "
                            + self.t("xrun").format(u=s.underruns, x=s.xruns))
            if s.finished:
                if self.cfg.loop:
                    try:
                        s.seek(0.0)      # 重建芯片状态后从头再来
                    except Exception:
                        self.on_stop()
                else:
                    self.on_stop()
        self.root.after(150, self._poll)

    def on_close(self):
        self.on_stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ---------------- 自检（EXE 打包后也能跑）----------------
def selfcheck() -> int:
    lines = []

    def log(s):
        lines.append(str(s))

    ok = True
    log(f"frozen={getattr(sys, 'frozen', False)}")
    log(f"BASE={BASE}")
    from player_session import app_dir as _ad
    log(f"APP_DIR={_ad()}")
    log(f"python={sys.version.split()[0]}")
    for name in ("opm.dll", "ssg.dll"):
        p = os.path.join(_ad(), name)
        log(f"{name}: {'OK' if os.path.exists(p) else 'MISSING'} ({p})")
        ok &= os.path.exists(p)

    try:
        from core import OpmCore
        c = OpmCore()
        log(f"OPM core: OK sr={c.sample_rate()}")
        c.close()
    except Exception as e:
        log(f"OPM core: FAIL {e}")
        ok = False

    try:
        from ssg_core import SsgCore
        s = SsgCore()
        s.generate(64)
        s.close()
        log("SSG core: OK")
    except Exception as e:
        log(f"SSG core: FAIL {e}")
        ok = False

    try:
        from pcm_core import load_drum_dir, PcmCore
        cfg = Config.load()
        p = PcmCore(55930)
        n = load_drum_dir(p, cfg.drum_dir) if os.path.isdir(cfg.drum_dir or "") else 0
        log(f"PCM samples: {n} (dir {cfg.drum_dir or 'unset'})")
        p.close()
    except Exception as e:
        log(f"PCM: {e}")

    try:
        import oki_core
        c = oki_core.OkiChip("6295", oki_core.ARCADE_CLOCK)
        log(f"OKI core: OK rate={c.rate} Hz ch={c.n_channels}")
    except Exception as e:
        log(f"OKI core: 不可用（{type(e).__name__}）— OKI 鼓将不可选")

    try:
        import sounddevice as sd
        outs = [d for d in sd.query_devices() if d["max_output_channels"] > 0]
        log(f"sounddevice: OK, {len(outs)} output devices")
    except Exception as e:
        log(f"sounddevice: FAIL {e}")
        ok = False

    text = "\n".join(lines)
    p = os.path.join(BASE, "selfcheck.log")
    try:
        open(p, "w", encoding="utf-8").write(text + "\n")
    except OSError:
        pass
    try:
        print(text)
    except Exception:
        pass
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--lang", choices=["zh", "en"], default=None)
    args = ap.parse_args(argv)
    if args.selfcheck:
        return selfcheck()
    cfg = Config.load()
    if args.lang:
        cfg.lang = args.lang
    App(cfg).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
