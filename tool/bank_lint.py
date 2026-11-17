# -*- coding: utf-8 -*-
"""音色库体检 —— 找出"结构上不可能好听"的槽位。

针对 .opm（VOPM 格式）音色库做静态检查，输出一个按可疑度排序的清单。
不需要听，也不需要渲染，纯参数分析。

    python tool/bank_lint.py --bank FB01_GM.opm
    python tool/bank_lint.py --bank FB01_GM.opm --ref NECOPN_GM.opm
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from voice import load_opm_text

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 每个算子是不是载波（算法 0-7，顺序 M1,C1,M2,C2）
CARRIER = {
    0: (0, 0, 0, 1), 1: (0, 0, 0, 1), 2: (0, 0, 0, 1), 3: (0, 0, 1, 1),
    4: (0, 1, 0, 1), 5: (0, 1, 1, 1), 6: (0, 1, 1, 1), 7: (1, 1, 1, 1),
}

GM_FAMILY = [
    (0, "钢琴"), (8, "色彩打击"), (16, "风琴"), (24, "吉他"), (32, "贝斯"),
    (40, "弦乐"), (48, "合奏"), (56, "铜管"), (64, "簧片"), (72, "管乐"),
    (80, "合成主音"), (88, "合成铺底"), (96, "合成效果"), (104, "民族"),
    (112, "打击乐"), (120, "音效"),
]

# 这些家族里"慢起音"是可疑的（应该是敲击/拨弦类）
PERCUSSIVE = set(range(0, 8)) | set(range(8, 16)) | set(range(112, 120))
# 这些家族里"完全没用 LFO"通常意味着缺了颤音/合唱
LFO_EXPECTED = set(range(8, 16)) | set(range(40, 56)) | {56, 60, 61}


def family_of(prog: int) -> str:
    name = "?"
    for start, n in GM_FAMILY:
        if prog >= start:
            name = n
    return name


def signature(v) -> str:
    key = (v.ch[1], v.ch[2], tuple(tuple(sorted(o.items())) for o in v.ops))
    return hashlib.md5(repr(key).encode()).hexdigest()[:10]


def carriers_of(v):
    mask = CARRIER.get(v.ch[2] & 7, CARRIER[0])
    return [o for o, c in zip(v.ops, mask) if c]


def inspect(index: int, v) -> dict:
    """返回该音色的可疑点列表。"""
    bad = []
    ops = v.ops
    _, fl, con, ams, pms = v.ch[0], v.ch[1], v.ch[2], v.ch[3], v.ch[4]
    lfo_units = v.lfo

    if len(ops) != 4:
        return {"flags": ["算子数不是 4"], "score": 9}

    car = carriers_of(v)
    car_tl = sum(o["tl"] for o in car) / max(1, len(car))
    if car_tl > 96:
        bad.append(f"载波平均 TL={car_tl:.0f}，声音过轻")

    ar_all = [o["ar"] for o in ops]
    ar_car = [o["ar"] for o in car]
    if max(ar_car) == 0:
        bad.append("载波 AR=0，根本发不出声")
    elif max(ar_car) < 8:
        bad.append(f"载波 AR 最大才 {max(ar_car)}，起音极慢")

    if index in PERCUSSIVE and sum(ar_car) / len(ar_car) < 20:
        bad.append(f"敲击类却用慢起音（载波 AR 均值 {sum(ar_car)/len(ar_car):.0f}）")

    rr_all = [o["rr"] for o in ops]
    sl_all = [o["sl"] for o in ops]
    if max(rr_all) == 0 and max(sl_all) == 0:
        bad.append("RR=0 且 SL=0，松键后永不衰减（会一直挂着）")

    if index in LFO_EXPECTED and lfo_units[2] == 0 and lfo_units[1] == 0:
        bad.append("该家族通常带 LFO 颤音/合唱，但 PMD=AMD=0")

    if (fl & 7) == 0 and v.ch[2] in (0, 1, 4):
        # 算法 0/1/4 主要靠反馈做亮度，fb=0 会偏"干"
        bad.append(f"算法 {v.ch[2]} 但反馈=0（这类算法通常靠反馈提亮）")

    if max(o["mul"] for o in car) == 0 and min(o["tl"] for o in car) > 60:
        bad.append("载波 MUL=0（0.5×）且音量低，音高会低一个八度")

    return {
        "flags": bad,
        "score": len(bad),
        "family": family_of(index),
        "car_tl": car_tl,
        "ar": sum(ar_all) / 4,
        "fb": fl & 7,
        "alg": v.ch[2] & 7,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank", required=True)
    ap.add_argument("--ref", default=None, help="参照库，做逐槽位对比")
    ap.add_argument("--top", type=int, default=20, help="列出前 N 个可疑音色")
    ap.add_argument("--allow-dup", action="store_true", help="不把重复槽位算作问题")
    args = ap.parse_args(argv)

    voices = load_opm_text(args.bank)
    print(f"{os.path.basename(args.bank)}: {len(voices)} 个音色\n")

    sigs = {}
    for i, v in enumerate(voices):
        sigs.setdefault(signature(v), []).append(i)

    rows = []
    for i, v in enumerate(voices):
        info = inspect(i, v)
        flags = list(info["flags"])
        if not args.allow_dup and len(sigs[signature(v)]) > 1:
            peers = [x for x in sigs[signature(v)] if x != i]
            flags.insert(0, f"与 {peers} 完全重复")
            info["score"] += 3
        info["index"] = i
        info["name"] = v.name.strip()
        info["flags"] = flags
        rows.append(info)

    total = sum(r["score"] for r in rows)
    flagged = [r for r in rows if r["flags"]]
    print(f"可疑音色: {len(flagged)}/{len(rows)}   总问题数 {total}\n")

    rows.sort(key=lambda r: (-r["score"], r["index"]))
    print(f"{'槽':>3}  {'GM 家族':<10} {'名称':<26} {'alg':>3} {'fb':>2} {'TL':>4} {'AR':>5}  问题")
    for r in rows[:args.top]:
        if not r["flags"]:
            break
        print(f"{r['index']:3d}  {r['family']:<10} {r['name'][:26]:<26} "
              f"{r['alg']:3d} {r['fb']:2d} {r['car_tl']:4.0f} {r['ar']:5.1f}  "
              + "；".join(r["flags"]))

    if args.ref:
        ref = load_opm_text(args.ref)
        print(f"\n--- 与 {os.path.basename(args.ref)} 的逐槽位差异 ---")
        diff = 0
        for i in range(min(len(voices), len(ref))):
            if signature(voices[i]) == signature(ref[i]):
                continue
            diff += 1
            a, b = inspect(i, voices[i]), inspect(i, ref[i])
            if abs(a["car_tl"] - b["car_tl"]) > 24 or abs(a["ar"] - b["ar"]) > 8:
                print(f"  {i:3d} {a['family']:<10} TL {a['car_tl']:3.0f}→{b['car_tl']:3.0f}  "
                      f"AR {a['ar']:4.1f}→{b['ar']:4.1f}  "
                      f"alg {a['alg']}→{b['alg']}  fb {a['fb']}→{b['fb']}")
        print(f"  共 {diff}/{min(len(voices), len(ref))} 个槽位参数不同")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
