# pcm_core.py
"""PCM 采样鼓核心 —— 移植自 OPMMIDI-GAI(PCM)，重写为向量化。

硬件原型是 Sharp X68000 的 MSM6258（4-bit ADPCM）。但要注意：**X68K 自己
就用 CPU 软混把 PCM 声道数翻倍**（68k 在定时器中断里手动混多路），所以这里
不做单声道阉割——默认 8 复音，`--pcm-voices N` 可调。致敬保留的是音色性格
（降比特 + 低通），不是那 1 路的限制。
"""
from __future__ import annotations

import os
import wave

import numpy as np


class PcmCore:
    name = "pcm"

    def __init__(self, sample_rate: int = 55_930, max_voices: int = 8, lofi: bool = False):
        self.sr = sample_rate
        self.max_voices = max(1, max_voices)
        self.lofi = lofi
        self.samples: dict[int, np.ndarray] = {}
        self.gain = 1.0
        self._voices = []          # [[data, pos:int, gain:float], ...]
        self.steals = 0

    # ---------- 加载 ----------
    def _load(self, path: str):
        """读 wav → 单声道 → 重采样到芯片采样率 → 可选 lo-fi。返回 int16 数组或 None。"""
        if not os.path.exists(path):
            return None
        with wave.open(path, "rb") as w:
            nch = w.getnchannels()
            sr_in = w.getframerate()
            raw = w.readframes(w.getnframes())

        data = np.frombuffer(raw, dtype="<i2").astype(np.float64)
        if nch == 2:
            data = data.reshape(-1, 2).mean(axis=1)

        # 重采样到芯片采样率（MSM6258 原生 15.6 kHz，这里是把它搬进 OPM 的时基）
        if sr_in != self.sr and data.size > 1:
            n_out = max(2, int(round(data.size * self.sr / sr_in)))
            data = np.interp(np.arange(n_out) * (sr_in / self.sr),
                             np.arange(data.size), data)

        if self.lofi:
            data = self._lofi(data)

        return np.clip(data, -32768, 32767).astype(np.int16)

    def register(self, notes, data: np.ndarray) -> None:
        for n in notes:
            self.samples[n] = data

    def load_sample(self, note: int, path: str) -> bool:
        data = self._load(path)
        if data is None:
            return False
        self.samples[note] = data
        return True

    @staticmethod
    def _lofi(x: np.ndarray) -> np.ndarray:
        """12-bit 降比特 + 一阶低通（α=0.45）。按脉冲响应截断成 FIR，避免逐点循环。"""
        x = np.floor(x / 16.0) * 16.0          # 等效 & 0xFFF0，对负数也正确
        alpha, beta = 0.45, 0.55
        n_taps = 32                            # 0.55^32 ≈ 1e-8，足够
        h = alpha * np.power(beta, np.arange(n_taps))
        return np.convolve(x, h)[:x.size]

    # ---------- 播放 ----------
    def play(self, note: int, velocity: int) -> None:
        data = self.samples.get(note)
        if data is None:
            return
        v = min(127, max(0, velocity))
        vol = (v / 127.0) ** 1.5               # GAI 的力度曲线
        if len(self._voices) >= self.max_voices:
            # 顶掉最老的一路（复音满了才发生，计入 steals）
            self._voices.pop(0)
            self.steals += 1
        self._voices.append([data, 0, vol * self.gain])

    def active(self) -> int:
        return len(self._voices)

    # ---------- 渲染 ----------
    def generate(self, nframes: int) -> np.ndarray:
        """返回 (nframes,) int16 单声道。"""
        acc = np.zeros(nframes, dtype=np.float32)
        alive = []
        for v in self._voices:
            data, pos, vol = v
            take = min(nframes, data.size - pos)
            if take > 0:
                acc[:take] += data[pos:pos + take].astype(np.float32) * np.float32(vol)
                v[1] = pos + take
            if v[1] < data.size:
                alive.append(v)
        self._voices = alive
        np.clip(acc, -32768.0, 32767.0, out=acc)
        return acc.astype(np.int16)

    def close(self) -> None:
        self._voices.clear()
        self.samples.clear()


# ---------------------------------------------------------------------------
# GM 35–82 → 文件名关键字（Korg M1 鼓组命名，沿用 GAI 的映射）
#
# 采样本身不随仓库分发（录音版权）。要做一套自己的，文件名里带上这些关键字即可，
# 大小写、空格都不敏感；一个采样会注册到所有匹配的音符上
# （比如 Crash 同时覆盖 49/52/55/57）。
# ---------------------------------------------------------------------------
PCM_DRUM_MAP = {
    35: "Kick 1",      36: "Kick 2",
    38: "Snare 1",     40: "Snare 2",     39: "Claps",
    42: "Closed HH",   44: "Closed HH 2", 46: "Open HH",
    41: "Tom 2",       43: "E.Tom",       45: "Tom 1",
    47: "E.Tom",       48: "Tom 1",       50: "Tom 2",
    49: "Crash",       52: "Crash",       55: "Crash",   57: "Crash",
    51: "Ride",        59: "Ride",        53: "Bell Ring",
    60: "Conga 1",     61: "Conga 2",     62: "Conga 1", 63: "Conga 1",
    64: "Conga 2",     65: "Timbales 1",  66: "Timbales 2",
    54: "Tambourine",  56: "Cowbell",     58: "Vibe Hit",
    69: "Shaker",      70: "Shaker",
    75: "Block",       76: "Block",       77: "Block",
    80: "Tubular",     81: "Tubular",     82: "Whip",
}


def load_drum_dir(pcm: "PcmCore", drum_dir: str) -> int:
    """把目录里的 wav 按关键字映射到 GM 音符。返回映射到的音符数。

    几条规则（相对 GAI 原版的改进）：
      1. 关键字按**长度降序**匹配，`Closed HH 2` 不会被 `Closed HH` 抢走；
      2. **去掉空格**再比较——文件名 `Closed HH2` 和关键字 `Closed HH 2` 能对上；
      3. 一个采样文件注册到**所有**匹配的音符上（Crash 同时覆盖 49/52/55/57），
         这样 26 个文件就能盖住整套 GM 打击乐。
    """
    import glob
    import re

    if not os.path.isdir(drum_dir):
        return 0

    def norm(s: str) -> str:
        return re.sub(r"\s+", "", s).lower()

    pairs = sorted(((norm(kw), note) for note, kw in PCM_DRUM_MAP.items()),
                   key=lambda p: -len(p[0]))
    for path in sorted(glob.glob(os.path.join(drum_dir, "*.wav"))):
        base = norm(os.path.basename(path))
        notes = [note for kw, note in pairs if kw in base]
        if not notes:
            continue
        data = pcm._load(path)
        if data is not None:
            pcm.register(notes, data)
    return len(pcm.samples)
