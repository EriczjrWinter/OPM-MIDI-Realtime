# -*- coding: utf-8 -*-
"""音色库声学审计 —— 渲染每个音色，量它的起音/衰减/亮度，对照 GM 家族预期。

参数检查（bank_lint.py）只能看出"写错了"，这个工具看的是"听起来不对"。

    python tool/bank_audit.py --bank FB01_GM.opm
    python tool/bank_audit.py --bank FB01_GM.opm --ref NECOPN_GM.opm
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import OpmCore
from driver import OpmSystem
from voice import load_opm_text

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SR = 3_579_545 // 64

GM_FAMILY = [
    (0, "piano"), (8, "chroma"), (16, "organ"), (24, "guitar"), (32, "bass"),
    (40, "strings"), (48, "ensemble"), (56, "brass"), (64, "reed"), (72, "pipe"),
    (80, "lead"), (88, "pad"), (96, "fx"), (104, "ethnic"), (112, "perc"), (120, "sfx"),
]

# 家族预期：(最大起音毫秒, 0.55s 时的电平占比下限, 电平占比上限)
# 上限 <1 表示"应该已经衰减"；下限 >0 表示"应该还在响"
EXPECT = {
    "piano":    (40, 0.02, 0.85),   # 敲击，必须有衰减
    "chroma":   (40, 0.02, 0.90),
    "organ":    (120, 0.60, 1.10),  # 按住不掉
    "guitar":   (40, 0.02, 0.90),
    "bass":     (40, 0.10, 1.05),
    "strings":  (200, 0.50, 1.10),
    "ensemble": (200, 0.45, 1.10),
    "brass":    (150, 0.35, 1.10),
    "reed":     (150, 0.35, 1.10),
    "pipe":     (150, 0.30, 1.10),
    "lead":     (120, 0.30, 1.10),
    "pad":      (400, 0.45, 1.10),
    "fx":       (None, 0.0, 1.20),
    "ethnic":   (150, 0.02, 1.10),
    "perc":     (30, 0.0, 0.70),    # 打击乐，必须已经衰减完
    "sfx":      (None, 0.0, 1.20),
}


def family_of(prog: int) -> str:
    name = "?"
    for start, n in GM_FAMILY:
        if prog >= start:
            name = n
    return name


def measure(voice, dur=0.75, note=57, vel=100):
    """弹一个音，返回 (起音ms, 稳态电平比, 亮度Hz, 峰值)。"""
    core = OpmCore()
    sy = OpmSystem(core)
    sy.load_bank([voice])
    sy.programs = [0] * 16
    sy.lock_program = True
    sy.note_on(0, note, vel)

    n = int(dur * SR)
    buf = (ctypes.c_int16 * (2 * n))()
    core.generate_into(n, buf)
    sig = np.frombuffer(buf, dtype="<i2").reshape(n, 2).mean(axis=1)
    core.close()

    env = np.convolve(np.abs(sig.astype(np.float32)), np.ones(256, np.float32) / 256, "same")
    peak = float(env.max())
    if peak < 1.0:
        return None, 0.0, 0.0, 0.0

    above = np.nonzero(env >= peak * 0.9)[0]
    attack_ms = float(above[0]) / SR * 1000.0

    t55 = int(0.55 * SR)
    level55 = float(env[t55]) / peak

    seg = sig[int(0.10 * SR):int(0.30 * SR)].astype(np.float64)
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    f = np.fft.rfftfreq(len(seg), 1 / SR)
    centroid = float((f * sp).sum() / (sp.sum() + 1e-9)) if seg.size else 0.0

    return attack_ms, level55, centroid, peak


def audit(path, label, top=25):
    voices = load_opm_text(path)
    rows = []
    for i, v in enumerate(voices):
        atk, lvl, cen, pk = measure(v)
        fam = family_of(i)
        flags = []
        if atk is None:
            flags.append("完全没声音")
        else:
            a_max, l_min, l_max = EXPECT.get(fam, (None, 0.0, 1.2))
            if a_max is not None and atk > a_max:
                flags.append(f"起音 {atk:.0f}ms 慢于预期 {a_max}ms")
            if lvl < l_min:
                flags.append(f"0.55s 只剩 {lvl*100:.0f}%，衰减过快")
            if lvl > l_max:
                flags.append(f"0.55s 还有 {lvl*100:.0f}%，该衰减没衰减")
        rows.append({
            "i": i, "name": v.name.strip(), "fam": fam,
            "atk": atk, "lvl": lvl, "cen": cen, "pk": pk, "flags": flags,
        })

    bad = [r for r in rows if r["flags"]]
    print(f"\n=== {label} ===  {len(voices)} 个音色，与 GM 家族预期不符 {len(bad)} 个")
    for r in sorted(bad, key=lambda r: (r["fam"], r["i"]))[:top]:
        atk = f"{r['atk']:.0f}ms" if r["atk"] is not None else "-"
        print(f"  {r['i']:3d} {r['fam']:<9} {r['name'][:24]:<24} "
              f"起音={atk:>7} 余量={r['lvl']*100:5.0f}% 亮度={r['cen']:5.0f}Hz  "
              + "；".join(r["flags"]))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", required=True)
    ap.add_argument("--ref", default=None)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args(argv)

    rows = audit(args.bank, os.path.basename(args.bank), args.top)

    if args.ref:
        ref_rows = audit(args.ref, os.path.basename(args.ref), args.top)
        print(f"\n--- 逐槽位亮度/响度对比（{os.path.basename(args.bank)} vs {os.path.basename(args.ref)}）---")
        n = min(len(rows), len(ref_rows))
        worst = sorted(range(n), key=lambda i: -abs(
            np.log10((rows[i]["cen"] + 1) / (ref_rows[i]["cen"] + 1))))[:15]
        for i in worst:
            a, b = rows[i], ref_rows[i]
            print(f"  {i:3d} {a['fam']:<9} 亮度 {a['cen']:6.0f} vs {b['cen']:6.0f} Hz  "
                  f"峰值 {a['pk']:6.0f} vs {b['pk']:6.0f}  "
                  f"起音 {a['atk'] or 0:5.0f} vs {b['atk'] or 0:5.0f} ms")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
