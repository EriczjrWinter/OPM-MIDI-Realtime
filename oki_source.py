# oki_source.py
"""把 OkiChip 接到 RT 播放器的混音总线上。

芯片跑在 8 000 / 15 625 Hz，混音链路是 55 930 Hz（OPM 的时钟/64），
所以这里做一次流式重采样，并保证每次 `generate(n)` 都恰好吐出 n 帧——
Renderer 的块大小逻辑依赖这个契约。
"""
from __future__ import annotations

import sys

import numpy as np

from rt_engine import Resampler          # 复用播放链路里那个 Catmull-Rom


class OkiSource:
    def __init__(self, chip, sr_out: int = 55_930):
        self.chip = chip
        self.sr_out = sr_out
        self.rs = Resampler(chip.rate, sr_out)
        self._buf = np.zeros(0, dtype=np.int16)

    # ---------- 事件 ----------
    def trigger(self, slot: int, velocity: int = 127) -> bool:
        return self.chip.trigger(slot, velocity)

    @property
    def samples(self):
        """让 driver 能像用 chip 一样用 source（`note in source.samples`）。"""
        return self.chip.samples

    @property
    def steals(self) -> int:
        return self.chip.steals

    # ---------- 渲染 ----------
    def generate(self, nframes: int) -> np.ndarray:
        while self._buf.size < nframes:
            need = int(np.ceil((nframes - self._buf.size) * self.chip.rate / self.sr_out)) + 4
            raw = self.chip.generate(need)                      # 芯片速率，单声道
            stereo = np.repeat(raw[:, None], 2, axis=1)          # Resampler 要立体声
            y = self.rs.process(stereo)[:, 0]
            if y.size == 0:
                break
            self._buf = np.concatenate([self._buf, y])
        if self._buf.size < nframes:
            pad = np.zeros(nframes - self._buf.size, dtype=np.int16)
            self._buf = np.concatenate([self._buf, pad])
        out = self._buf[:nframes].copy()
        self._buf = self._buf[nframes:]
        return out

    def close(self) -> None:
        self.chip.silence()


def load_oki_dir(chip, drum_dir: str) -> int:
    """按 GM 打击乐关键字把目录里的 WAV 装进芯片。返回注册的槽位数。

    复用 RT 的 PCM_DRUM_MAP（GM 音符 → 文件名关键字），一个文件注册到所有匹配的音符。
    """
    import glob
    import os
    import re

    from pcm_core import PCM_DRUM_MAP

    if not os.path.isdir(drum_dir):
        return 0

    def norm(s: str) -> str:
        return re.sub(r"\s+", "", s).lower()

    pairs = sorted(((norm(kw), note) for note, kw in PCM_DRUM_MAP.items()),
                   key=lambda p: -len(p[0]))
    total = 0
    for path in sorted(glob.glob(os.path.join(drum_dir, "*.wav"))):
        base = norm(os.path.basename(path))
        notes = [note for kw, note in pairs if kw in base]
        if notes:
            total += chip.load_wav_many(notes, path)
    return total
