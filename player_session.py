# player_session.py
"""一次播放所需的全部组装：核心 + 音色库 + 鼓引擎 + 渲染器 + 音频引擎。

CLI（rtplayer.py）和 GUI（app.py）都走这里，避免两份逻辑漂移。

设计上有一条硬规则：**EXE 里不带任何 PCM 采样**。
鼓采样是第三方版权素材，只能由用户指定目录；目录不存在时自动退回 SSG/FM 鼓。
"""
from __future__ import annotations

import json
import os
import sys
import wave
from dataclasses import asdict, dataclass, field

import numpy as np

from core import NullCore, OpmCore
from driver import OpmSystem
from rt_engine import Engine, Renderer, Resampler
from smf import parse_smf
from voice import load_opm_text

DRUM_MODES = ("fm", "ssg", "ssg-clean", "pcm", "ssg+pcm", "ssg-clean+pcm", "oki")


def app_dir() -> str:
    """脚本目录；PyInstaller 冻结后指向解包目录（DLL 都在那里）。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def work_dir() -> str:
    """配置/采样目录的基准：EXE 所在目录（而不是临时解包目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


HERE = app_dir()
BASE = work_dir()
# 音色库搜索路径：先 EXE/脚本旁边，再打包内的解包目录（--onefile 会把数据放进 _MEIPASS）
SEARCH_DIRS = [BASE] + ([HERE] if os.path.abspath(HERE) != os.path.abspath(BASE) else [])


@dataclass
class Config:
    midi: str = ""
    bank: str = ""
    chip: str = "opm"          # opm | opp | opz
    drums: str = "fm"          # fm | ssg | ssg-clean | pcm
    drum_dir: str = ""
    device: int | None = None
    blocksize: int = 256
    pcm_voices: int = 8
    pcm_lofi: bool = True
    pcm_gain: float = 0.5
    ssg_gain: float = 2.0
    raw: bool = False
    volume: float = 1.0
    tail: float = 1.0
    lang: str = "zh"          # zh | en，界面语言
    loop: bool = False        # 播完自动从头循环
    oki_chip: str = "6295"    # 6258（X68000，单通道）| 6295（街机，四通道）
    oki_clock: int = 1_056_000  # 6258 建议 4_000_000 → 15 625 Hz
    oki_velocity: bool = False  # 硬件无力度，默认关
    oki_gain: float = 0.5       # 芯片输出近满幅，和 PCM 一样压一半防止削波

    # ---------- 持久化 ----------
    @staticmethod
    def path() -> str:
        return os.path.join(BASE, "player_config.json")

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        p = cls.path()
        if os.path.exists(p):
            try:
                data = json.load(open(p, encoding="utf-8"))
                for k, v in data.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
            except Exception:
                pass
        # 默认音色库：优先 v7，其次 v4
        if not cfg.bank:
            for d in SEARCH_DIRS:
                for name in ("FB01_GM_v7.opm", "FB01_GM_v4.opm", "NECOPN_GM.opm"):
                    cand = os.path.join(d, name)
                    if os.path.exists(cand):
                        cfg.bank = cand
                        break
                if cfg.bank:
                    break
        if not cfg.drum_dir:
            for d in SEARCH_DIRS:
                cand = os.path.join(d, "drum_samples")
                if os.path.isdir(cand):
                    cfg.drum_dir = cand
                    break
        return cfg

    def save(self) -> None:
        try:
            json.dump(asdict(self), open(self.path(), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
        except OSError:
            pass


class Session:
    """组装好但还没出声的一次播放。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.core = None
        self.system = None
        self.ssg = None
        self.pcm = None
        self.oki = None
        self.oki_notes = 0
        self.renderer = None
        self.engine = None
        self.total_sec = 0.0
        self.n_events = 0
        self.pcm_notes = 0
        self._events = None
        self._offset = 0.0

    # ---------- 构建 ----------
    def build(self) -> None:
        cfg = self.cfg
        if not os.path.exists(cfg.midi):
            raise FileNotFoundError(f"找不到 MIDI: {cfg.midi}")
        if not os.path.exists(cfg.bank):
            raise FileNotFoundError(f"找不到音色库: {cfg.bank}")

        voices = load_opm_text(cfg.bank)
        if not voices:
            raise ValueError(f"音色库是空的: {cfg.bank}")

        events, _tpq, total_sec = parse_smf(open(cfg.midi, "rb").read())
        self.total_sec = total_sec
        self.n_events = len(events)
        self._events = events
        self._offset = 0.0

        flags = 1 if cfg.chip == "opp" else 0
        self.core = OpmCore(clock=3_579_545, flags=flags)
        self.system = OpmSystem(self.core, chip_mode=cfg.chip)
        self.system.load_bank(voices)
        self.system.translate_pitch = not cfg.raw
        self.system.translate_env = not cfg.raw

        # ---- 鼓 ----
        drums = cfg.drums
        use_ssg = drums.startswith("ssg")
        use_pcm = "pcm" in drums
        if use_ssg:
            from ssg_core import SsgCore
            self.ssg = SsgCore()
            self.system.ssg = self.ssg
            self.system.ssg_drums = True
            self.system.ssg_recipe = "clean" if "clean" in drums else "legacy"
        if use_pcm:
            self._attach_pcm()
        if "oki" in drums:
            self._attach_oki()

        self.renderer = Renderer(self.core, self.system, events, total_sec,
                                 tail=cfg.tail, ssg=self.ssg, ssg_gain=cfg.ssg_gain,
                                 pcm=self.pcm, pcm_gain=cfg.pcm_gain,
                                 oki=self.oki, oki_gain=cfg.oki_gain,
                                 master=cfg.volume)
        self.engine = Engine(self.renderer, sr_out=48_000, blocksize=cfg.blocksize,
                             target_blocks=6)

    def _attach_oki(self) -> None:
        """挂 OKI ADPCM 鼓（MSM6258 / MSM6295）。缺采样则静默放弃。"""
        try:
            from oki_core import OkiChip
            from oki_source import OkiSource, load_oki_dir
        except ImportError:
            return
        d = self.cfg.drum_dir
        if not d or not os.path.isdir(d):
            return
        chip = OkiChip(self.cfg.oki_chip, self.cfg.oki_clock,
                       velocity=self.cfg.oki_velocity)
        n = load_oki_dir(chip, d)
        if n == 0:
            return
        self.oki = OkiSource(chip, self.core.sample_rate())
        self.oki_notes = len(chip.samples)
        self.system.oki = self.oki

    def _attach_pcm(self) -> None:
        """挂 PCM 鼓；目录不存在或没有采样就静默放弃（退回 FM/SSG 鼓）。"""
        from pcm_core import PcmCore, load_drum_dir
        d = self.cfg.drum_dir
        if not d or not os.path.isdir(d):
            return
        pcm = PcmCore(self.core.sample_rate(), max_voices=self.cfg.pcm_voices,
                      lofi=self.cfg.pcm_lofi)
        n = load_drum_dir(pcm, d)
        if n == 0:
            pcm.close()
            return
        self.pcm = pcm
        self.pcm_notes = n
        self.system.pcm = pcm

    # ---------- 播放 ----------
    def start(self, device=None) -> None:
        import sounddevice as sd
        dev = device if device is not None else self.cfg.device
        self.stream = sd.OutputStream(device=dev, samplerate=48_000, channels=2,
                                      dtype="int16", blocksize=self.cfg.blocksize,
                                      callback=self._callback)
        self.engine.start()
        # 预填缓冲，避免开局 underrun
        import time
        t0 = time.time()
        while self.engine.buffered_frames() < self.cfg.blocksize * 6 and time.time() - t0 < 2.0:
            time.sleep(0.005)
        self.stream.start()

    def _callback(self, outdata, frames, time_info, status):
        if status:
            self.xruns += 1
        outdata[:] = self.engine.pull(frames)

    xruns = 0

    def stop(self) -> None:
        try:
            if getattr(self, "stream", None):
                self.stream.stop()
                self.stream.close()
                self.stream = None
        except Exception:
            pass
        if self.engine:
            self.engine.stop()

    def close(self) -> None:
        self.stop()
        for obj in (self.pcm, self.ssg):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        if self.core is not None:
            try:
                self.core.close()
            except Exception:
                pass

    # ---------- 跳转 ----------
    def _event_index_at(self, frame: int) -> int:
        ev = self.renderer.ev
        lo, hi = 0, len(ev)
        while lo < hi:
            mid = (lo + hi) // 2
            if ev[mid][1] < frame:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def _replay_to(self, frame: int) -> None:
        """把 frame 之前的"状态类"事件重放一遍，再把仍按住的音重新起音。

        OPM 是有状态芯片（相位/包络/LFO），没法真的"跳到中间"。
        这做不到精确复原包络，但能把音高、音色、音量、弯音、踏板、按住哪些音
        全部还原——听感上就是从中间接上，代价是正在响的音重新起一次音。
        """
        sys_ = self.system
        sr = self.core.sample_rate()
        held: dict[tuple[int, int], int] = {}
        order: list[tuple[int, int]] = []

        for e in self._events:
            if int(round(e.t * sr)) > frame:
                break
            if e.kind == "prog":
                sys_.program_change(e.ch, e.d1)
            elif e.kind == "cc":
                sys_.control_change(e.ch, e.d1, e.d2)
            elif e.kind == "bend":
                sys_.pitch_bend(e.ch, e.d1, e.d2)
            elif e.kind == "on":
                key = (e.ch, e.d1)
                if e.d2 > 0:
                    if key not in held:
                        order.append(key)
                    held[key] = e.d2
                else:
                    held.pop(key, None)
            elif e.kind == "off":
                held.pop((e.ch, e.d1), None)

        sys_.time = frame / sr
        for ch, note in order:
            if (ch, note) in held:
                sys_.note_on(ch, note, held[(ch, note)])

    def seek(self, seconds: float) -> None:
        """跳到指定时刻并继续播放（保持流不关）。"""
        if not self._events:
            return
        t = max(0.0, min(float(seconds), self.total_sec))
        playing = getattr(self, "stream", None) is not None
        self.stop()

        # 彻底重建：新芯片、新系统，再重放状态
        for obj in (self.pcm, self.ssg):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        if self.core is not None:
            try:
                self.core.close()
            except Exception:
                pass
        self.core = self.ssg = self.pcm = None
        self.build()

        frame = int(t * self.core.sample_rate())
        self._replay_to(frame)
        self.renderer.frame = frame
        self.renderer.ei = self._event_index_at(frame)
        self.engine = Engine(self.renderer, sr_out=48_000,
                             blocksize=self.cfg.blocksize, target_blocks=6)
        self._offset = t
        if playing:
            self.start()

    # ---------- 状态 ----------
    @property
    def finished(self) -> bool:
        return self.engine is not None and self.engine.finished()

    @property
    def position(self) -> float:
        if not self.engine:
            return self._offset
        return self._offset + self.engine.emitted / 48_000.0

    @property
    def underruns(self) -> int:
        return self.engine.underruns if self.engine else 0


# ---------------------------------------------------------------------------
# 离线导出：走和实时播放**同一条**渲染链（SSG/PCM/芯片模式/混音/隔直全都一致）
# ---------------------------------------------------------------------------
def render_wav(cfg: Config, out_path: str, sr_out: int = 48_000,
               progress=None, cancelled=None) -> dict:
    """把配置描述的这一场播放渲染成 WAV。

    sr_out=48000 时与实时播放逐样本一致（含重采样）；
    sr_out=55930（芯片原生）可跳过重采样，用于进一步分析/后处理。
    返回统计信息 dict。
    """
    s = Session(cfg)
    s.build()
    try:
        chip_sr = s.core.sample_rate()
        res = Resampler(chip_sr, sr_out) if sr_out != chip_sr else None
        total = max(1, s.renderer.total_frames)
        peak = 0
        written = 0

        with wave.open(out_path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(sr_out)
            while not s.renderer.done:
                if cancelled is not None and cancelled():
                    break
                blk = s.renderer.render(512)
                if res is not None:
                    blk = res.process(blk)
                if blk.shape[0]:
                    w.writeframes(blk.tobytes())
                    written += blk.shape[0]
                    peak = max(peak, int(np.abs(blk).max()))
                if progress is not None:
                    progress(min(1.0, s.renderer.frame / total))

        return {
            "path": out_path,
            "sr": sr_out,
            "frames": written,
            "seconds": written / sr_out,
            "peak": peak,
            "chip_sr": chip_sr,
            "complete": bool(s.renderer.done),
            "pcm_notes": s.pcm_notes,
        }
    finally:
        s.close()
