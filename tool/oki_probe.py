# -*- coding: utf-8 -*-
"""OKI ADPCM 探针：听 8 kHz / 15.6 kHz 的镲片能不能忍。

不做芯片仿真，只做"数据准备 + 播放"这条链的模拟：

    原始 48 kHz 采样
      → 降到芯片采样率（15 625 = 4 MHz/256，X68K；8 000 = 1.056 MHz/132，街机）
      → 编成 OKI 4-bit ADPCM 再解回 PCM（ffmpeg 的 adpcm_oki）
      → 升回 48 kHz 试听

产出三个可 A/B 的 WAV：原始 / X68K / 街机。

    python tool/oki_probe.py
    python tool/oki_probe.py --src D:\\my\\samples --out D:\\DSH\\out
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_SRC = os.path.join(ROOT, "drum_samples")

# 挑四个有代表性的：低频（底鼓）、中频瞬态（军鼓）、高频噪声（踩镲）、长尾（吊镲）
PICKS = [("Kick 1", "kick"), ("Snare 1", "snare"),
         ("Closed HH", "hihat"), ("Crash", "crash")]

SR_OUT = 48_000
VARIANTS = [("raw", None), ("x68k", 15_625), ("arcade", 8_000)]


def read_wav(path):
    with wave.open(path, "rb") as w:
        nch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        raise ValueError(f"{path}: 只支持 16-bit")
    a = np.frombuffer(raw, dtype="<i2").astype(np.float64)
    if nch == 2:
        a = a.reshape(-1, 2).mean(axis=1)
    return a, sr


def resample(x, sr_in, sr_out):
    """频域带限重采样。

    线性插值会产生混叠（高频折回来），探针要模拟的是"降到 8 kHz 会丢掉什么"，
    所以必须在频域截断，不能直接插值。
    """
    if sr_in == sr_out or x.size < 8:
        return x
    n_in = x.size
    n_out = max(8, int(round(n_in * sr_out / sr_in)))
    spec = np.fft.rfft(x)
    if sr_out < sr_in:
        keep = max(1, int(len(spec) * sr_out / sr_in))
        spec = spec[:keep]
    out = np.fft.irfft(spec, n_out)
    return out * (n_out / n_in)


# ---------------------------------------------------------------------------
# OKI 4-bit ADPCM（MSM6295 / MSM6258 家族）
#   49 级步长表 + 8 项索引调整表；nibble 的 bit3 = 符号，bit2..0 = 幅度
#   解码输出是 12-bit 有符号（-2048..2047）
# ---------------------------------------------------------------------------
STEP_TABLE = np.array([
    16, 17, 19, 21, 23, 25, 28, 31, 34, 37,
    41, 45, 50, 55, 60, 66, 73, 80, 88, 97,
    107, 118, 130, 143, 157, 173, 190, 209, 230, 253,
    279, 307, 337, 371, 408, 449, 494, 544, 598, 658,
    724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552,
], dtype=np.int32)
INDEX_TABLE = np.array([-1, -1, -1, -1, 2, 4, 6, 8], dtype=np.int32)


def _delta(step_idx, nib):
    s = int(STEP_TABLE[step_idx])
    d = 0
    if nib & 4:
        d += s
    if nib & 2:
        d += s >> 1
    if nib & 1:
        d += s >> 2
    return -d if (nib & 8) else d


def oki_decode(nibbles):
    """nibble 序列 → 12-bit PCM（int32 数组）。"""
    out = np.empty(len(nibbles), dtype=np.int32)
    sig, step = 0, 0
    for i, nib in enumerate(nibbles):
        sig += _delta(step, int(nib))
        if sig > 2047:
            sig = 2047
        elif sig < -2048:
            sig = -2048
        out[i] = sig
        step += int(INDEX_TABLE[int(nib) & 7])
        if step < 0:
            step = 0
        elif step > 48:
            step = 48
    return out


def oki_encode(x):
    """16-bit PCM → nibble 序列（贪心量化，和多数 WAV→OKI 工具一致）。"""
    target = np.clip(x / 16.0, -2048, 2047).astype(np.int32)
    n = len(target)
    out = np.empty(n, dtype=np.uint8)
    sig, step = 0, 0
    for i in range(n):
        t = int(target[i])
        base = int(STEP_TABLE[step])
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
        sig = max(-2048, min(2047, best_pred))
        step = max(0, min(48, step + int(INDEX_TABLE[best_nib & 7])))
    return out


def oki_roundtrip(x, sr, _tmpdir=None):
    """nibble 量化一遍再解回来，模拟 4-bit ADPCM 的损失。"""
    return oki_decode(oki_encode(x)) * 16.0


def write_wav(path, x, sr=SR_OUT):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(np.clip(x, -32768, 32767).astype("<i2").tobytes())


def centroid(x, sr):
    if x.size < 64:
        return 0.0
    sp = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    f = np.fft.rfftfreq(x.size, 1 / sr)
    return float((f * sp).sum() / (sp.sum() + 1e-9))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default=os.path.join(os.environ.get("TEMP", "."), "oki_probe"))
    ap.add_argument("--gap", type=float, default=0.6, help="每击间隔秒数")
    args = ap.parse_args(argv)

    if not shutil.which("ffmpeg"):
        print("找不到 ffmpeg", file=sys.stderr)
        return 2
    os.makedirs(args.out, exist_ok=True)
    tmpdir = os.path.join(args.out, "_tmp")
    os.makedirs(tmpdir, exist_ok=True)

    # 找样本
    files = sorted(f for f in os.listdir(args.src) if f.lower().endswith(".wav"))
    chosen = []
    for key, label in PICKS:
        hit = next((f for f in files if key.lower() in f.lower()), None)
        if hit:
            chosen.append((label, os.path.join(args.src, hit)))
    if not chosen:
        print(f"{args.src} 里找不到可用的样本", file=sys.stderr)
        return 1
    print("样本: " + ", ".join(f"{lbl}={os.path.basename(p)}" for lbl, p in chosen))

    gap = int(args.gap * SR_OUT)
    results = {}
    for name, sr_chip in VARIANTS:
        track = np.zeros(0)
        for label, path in chosen:
            x, sr = read_wav(path)
            x = resample(x, sr, SR_OUT)                 # 先统一到 48k
            if sr_chip is not None:
                x = resample(x, SR_OUT, sr_chip)        # 降到芯片速率
                x = oki_roundtrip(x, sr_chip, tmpdir)   # 4-bit OKI 量化
                x = resample(x, sr_chip, SR_OUT)        # 播出来再升到 48k
            seg = np.zeros(gap)
            seg[:min(len(x), gap)] = x[:gap]
            track = np.concatenate([track, seg])

        # 归一化到同一峰值，方便只比较"音色"而不是"响度"
        peak = np.abs(track).max()
        if peak > 0:
            track *= 32000.0 / peak
        out = os.path.join(args.out, f"probe_{name}.wav")
        write_wav(out, track)
        results[name] = (track, centroid(track, SR_OUT))
        print(f"  {name:8s} → {os.path.basename(out)}  "
              f"{track.size / SR_OUT:5.2f}s  频谱重心 {results[name][1]:6.0f} Hz")

    # 差异报告
    raw = results["raw"][0]
    n = min(len(raw), min(len(v[0]) for v in results.values()))
    print("\n相对原始的逐点差异（越小越接近）：")
    for name in ("x68k", "arcade"):
        d = results[name][0][:n] - raw[:n]
        rms_rel = float(np.sqrt((d ** 2).mean()) / (np.sqrt((raw[:n] ** 2).mean()) + 1e-9))
        print(f"  {name:8s} 相对 RMS 误差 {rms_rel * 100:5.1f}%  "
              f"高频损失 {results['raw'][1] - results[name][1]:5.0f} Hz")

    print(f"\n输出目录: {args.out}")
    print("建议先听 probe_arcade.wav 的镲片——如果糊得不能忍，这条路就不用走了。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
