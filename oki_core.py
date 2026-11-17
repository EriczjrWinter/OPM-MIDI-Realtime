# oki_core.py
"""OKI 4-bit ADPCM —— MSM6258（X68000，单通道）与 MSM6295（街机，四通道）。

纯 Python 实现。解码是整数运算，4 通道 × 8 kHz 只有 3.2 万步/秒，
不需要 C 核心（对比：Nuked OPM 约 300 ms/音频秒）。

采样率由外部时钟决定：
    MSM6258:  rate = clock / 256      X68K 标准 4 MHz  → 15 625 Hz
    MSM6295:  rate = clock / 132      街机 1.056 MHz  →  8 000 Hz

硬件的两个"缺陷"是特性，不是 bug：
    - 没有力度：音符只决定触发，响度固定（可按需用软件增益补偿）
    - 通道数有限：6258 只有 1 路，6295 只有 4 路，新鼓会掐掉旧鼓
"""
from __future__ import annotations

import wave

import numpy as np

# 49 级步长表（OKI 家族，注意和 IMA ADPCM 不是同一张）
STEP_TABLE = (
    16, 17, 19, 21, 23, 25, 28, 31, 34, 37,
    41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
    107, 118, 130, 143, 157, 173, 190, 209, 230, 253,
    279, 307, 337, 371, 408, 449, 494, 544, 598, 658,
    724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552,
)
# nibble 的幅度位 → 步长索引调整（符号位不参与）
INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8)

CHIPS = {
    "6258": {"divisor": 256, "channels": 1, "label": "MSM6258 · X68000"},
    "6295": {"divisor": 132, "channels": 4, "label": "MSM6295 · arcade"},
}

X68K_CLOCK = 4_000_000          # → 15 625 Hz
ARCADE_CLOCK = 1_056_000        # → 8 000 Hz


def chip_rate(kind: str, clock: int) -> int:
    return max(1, clock // CHIPS[kind]["divisor"])


# ---------------------------------------------------------------------------
# 编解码
# ---------------------------------------------------------------------------
def encode(x: np.ndarray) -> np.ndarray:
    """16-bit PCM → nibble 序列（贪心量化）。"""
    target = np.clip(np.asarray(x) / 16.0, -2048, 2047).astype(np.int32)
    out = np.empty(target.size, dtype=np.uint8)
    sig = step = 0
    for i in range(target.size):
        t = int(target[i])
        base = STEP_TABLE[step]
        best_err, best_nib, best_pred = 1 << 30, 0, sig
        for nib in range(16):
            d = 0
            if nib & 4:
                d += base
            if nib & 2:
                d += base >> 1
            if nib & 1:
                d += base >> 2
            if nib & 8:
                d = -d
            pred = sig + d
            err = abs(t - pred)
            if err < best_err:
                best_err, best_nib, best_pred = err, nib, pred
        out[i] = best_nib
        sig = -2048 if best_pred < -2048 else (2047 if best_pred > 2047 else best_pred)
        step += INDEX_TABLE[best_nib & 7]
        step = 0 if step < 0 else (48 if step > 48 else step)
    return out


def decode(nibs) -> np.ndarray:
    """nibble 序列 → 12-bit 有符号 PCM。"""
    n = len(nibs)
    out = np.empty(n, dtype=np.int32)
    sig = step = 0
    for i in range(n):
        nib = int(nibs[i])
        s = STEP_TABLE[step]
        d = 0
        if nib & 4:
            d += s
        if nib & 2:
            d += s >> 1
        if nib & 1:
            d += s >> 2
        sig += -d if (nib & 8) else d
        sig = -2048 if sig < -2048 else (2047 if sig > 2047 else sig)
        out[i] = sig
        step += INDEX_TABLE[nib & 7]
        step = 0 if step < 0 else (48 if step > 48 else step)
    return out


# ---------------------------------------------------------------------------
# 芯片
# ---------------------------------------------------------------------------
class OkiChip:
    """一颗 OKI 芯片。`generate()` 输出芯片原生采样率下的单声道 int16。"""

    def __init__(self, kind: str = "6295", clock: int = ARCADE_CLOCK,
                 velocity: bool = False, max_voices: int | None = None):
        if kind not in CHIPS:
            raise ValueError(f"不认识的芯片: {kind}")
        self.kind = kind
        self.clock = clock
        self.rate = chip_rate(kind, clock)
        self.n_channels = CHIPS[kind]["channels"]
        self.max_voices = max_voices or self.n_channels
        self.use_velocity = velocity

        self.samples: dict[int, np.ndarray] = {}      # slot → nibbles
        self.names: dict[int, str] = {}
        self._voices: list[dict] = []
        self.steals = 0
        self.underruns = 0

    # ---------- 加载 ----------
    def _encode_file(self, path: str, max_seconds: float) -> np.ndarray | None:
        with wave.open(path, "rb") as w:
            nch, sr, n = w.getnchannels(), w.getframerate(), w.getnframes()
            if w.getsampwidth() != 2:
                return None
            raw = w.readframes(n)
        a = np.frombuffer(raw, dtype="<i2").astype(np.float64)
        if nch == 2:
            a = a.reshape(-1, 2).mean(axis=1)
        a = a[: int(max_seconds * sr)]
        return encode(self._bandlimit(a, sr, self.rate))

    def load_wav_many(self, slots, path: str, max_seconds: float = 2.0) -> int:
        """一个文件注册到多个槽位（只编码一次）。返回注册了几个槽。"""
        nibs = self._encode_file(path, max_seconds)
        if nibs is None:
            return 0
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        for slot in slots:
            self.samples[slot] = nibs
            self.names[slot] = name
        return len(slots)

    def load_wav(self, slot: int, path: str, max_seconds: float = 2.0) -> bool:
        return self.load_wav_many([slot], path, max_seconds) > 0

    @staticmethod
    def _bandlimit(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
        """频域带限重采样（降采样必须截断频谱，否则混叠）。"""
        if sr_in == sr_out or x.size < 8:
            return x
        n_out = max(8, int(round(x.size * sr_out / sr_in)))
        spec = np.fft.rfft(x)
        if sr_out < sr_in:
            spec = spec[: max(1, int(len(spec) * sr_out / sr_in))]
        return np.fft.irfft(spec, n_out) * (n_out / x.size)

    # ---------- 触发 ----------
    def trigger(self, slot: int, velocity: int = 127) -> bool:
        data = self.samples.get(slot)
        if data is None or data.size < 2:
            return False
        gain = (min(127, max(0, velocity)) / 127.0) if self.use_velocity else 1.0
        if len(self._voices) >= self.max_voices:
            self._voices.pop(0)          # 硬件行为：最老的被掐掉
            self.steals += 1
        self._voices.append({"slot": slot, "pos": 0, "gain": gain, "sig": 0, "step": 0})
        return True

    def active(self) -> int:
        return len(self._voices)

    def silence(self) -> None:
        self._voices.clear()

    # ---------- 渲染 ----------
    def generate(self, nframes: int) -> np.ndarray:
        """输出 nframes 个单声道 int16（芯片原生采样率）。"""
        acc = np.zeros(nframes, dtype=np.float32)
        alive = []
        for v in self._voices:
            data = self.samples[v["slot"]]
            pos, sig, step, gain = v["pos"], v["sig"], v["step"], v["gain"]
            total = data.size
            i = 0
            while i < nframes and pos < total:
                nib = int(data[pos])
                pos += 1
                s = STEP_TABLE[step]
                d = 0
                if nib & 4:
                    d += s
                if nib & 2:
                    d += s >> 1
                if nib & 1:
                    d += s >> 2
                sig += -d if (nib & 8) else d
                if sig < -2048:
                    sig = -2048
                elif sig > 2047:
                    sig = 2047
                step += INDEX_TABLE[nib & 7]
                step = 0 if step < 0 else (48 if step > 48 else step)
                acc[i] += sig * gain
                i += 1
            v["pos"], v["sig"], v["step"] = pos, sig, step
            if pos < total:
                alive.append(v)
        self._voices = alive
        np.clip(acc, -2048, 2047, out=acc)
        return (acc * 16).astype(np.int16)       # 12-bit → 16-bit

    # ---------- 信息 ----------
    def info(self) -> str:
        return (f"{CHIPS[self.kind]['label']}  clock={self.clock} Hz  "
                f"rate={self.rate} Hz  ch={self.n_channels}")
