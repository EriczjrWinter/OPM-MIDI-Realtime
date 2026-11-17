# -*- coding: utf-8 -*-
"""把旧作东方音色盘补成一张 GM 库：TOUHOU_GM.opm。

思路：ZUN 那 116 个音色只覆盖 7 个家族（钢琴/吉他/贝斯/弦乐/管乐/铜管/锯齿/打击/音效），
其余 7 个家族（色彩打击/管风琴/合奏/簧片/铺底/合成效果/民族）从 NECOPN_GM 补——
两者都是 PC-98 OPNA 血统，接上去不串味。

    python tool/build_touhou_gm.py
"""
from __future__ import annotations

import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from voice import load_opm_text

TOUHOU = os.path.join(ROOT, "TOUHOU_PC98.opm")
NECOPN = os.path.join(ROOT, "NECOPN_GM.opm")
OUT = os.path.join(ROOT, "TOUHOU_GM.opm")

GM_FAMILY = ["piano", "chroma", "organ", "guitar", "bass", "strings",
             "ensemble", "brass", "reed", "pipe", "lead", "pad",
             "fx", "ethnic", "perc", "sfx"]

# 中文描述 → GM 家族
KEYWORDS = [
    ("piano", ["piano", "pia", "钢琴"]),
    ("guitar", ["吉他", "gt", "guitar"]),
    ("bass", ["贝斯", "bass", "低音"]),
    ("strings", ["弦"]),
    ("pipe", ["管类", "管乐", "flute", "sax"]),
    ("brass", ["trumpet", "brass", "小号", "铜管"]),
    ("lead", ["锯齿", "saw"]),
    ("perc", ["咚", "撞钟", "打击", "鼓", "铁片", "铁丝的", "铁片或"]),
    ("sfx", ["塑料", "汽水瓶", "声呐", "噪音", "绳断", "细绳"]),
]


def classify(name: str) -> str | None:
    low = name.lower()
    for fam, kws in KEYWORDS:
        if any(k in low for k in kws):
            return fam
    return None


def is_audible(v, note: int = 60) -> bool:
    """弹一下看有没有声。

    有些 PMD 音色（如"噪音做的打击声"）本身没有 NE 位、要靠曲目里的噪声命令
    叠加，单独拿出来是哑的；这类不该进 GM 库。
    """
    import ctypes

    import numpy as np

    from core import OpmCore
    from driver import OpmSystem

    core = OpmCore()
    try:
        sy = OpmSystem(core)
        sy.load_bank([v])
        sy.programs = [0] * 16
        sy.lock_program = True
        sy.note_on(0, note, 100)
        n = 8000
        buf = (ctypes.c_int16 * (2 * n))()
        core.generate_into(n, buf)
        return int(np.abs(np.frombuffer(buf, dtype="<i2")).max()) >= 50
    finally:
        core.close()


def to_text(v, slot: int, name: str, src: str) -> str:
    lfo = list(v.lfo) + [0] * (5 - len(v.lfo))
    ch = list(v.ch) + [0] * (7 - len(v.ch))
    lines = [f"@:{slot} {name}", f"// src={src}",
             "LFO: " + " ".join(str(x) for x in lfo[:5]),
             "CH: " + " ".join(str(x) for x in ch[:7])]
    for tag, op in zip(("M1", "C1", "M2", "C2"), v.ops):
        vals = [op.get(k, 0) for k in ("ar", "dr", "sr", "rr", "sl", "tl",
                                       "ks", "mul", "dt1", "dt2")]
        vals.append(op.get("am", 0))          # 第 11 个是 AMS-EN
        lines.append(f"{tag}: " + " ".join(str(x) for x in vals))
    return "\n".join(lines)


def main() -> int:
    touhou = load_opm_text(TOUHOU)
    necopn = load_opm_text(NECOPN)
    if len(necopn) < 128:
        print(f"NECOPN_GM 只有 {len(necopn)} 个音色，不够补 128 槽", file=sys.stderr)
        return 1

    # 按家族分桶；名字里带 Common 的排前面（通用版优先），哑音直接剔除
    pools: dict[str, list] = {}
    muted = 0
    for v in touhou:
        fam = classify(v.name)
        if not fam:
            continue
        if not is_audible(v):
            muted += 1
            continue
        pools.setdefault(fam, []).append(v)
    for fam, lst in pools.items():
        lst.sort(key=lambda x: (0 if "common" in x.name.lower() else 1))

    print("东方音色可用的家族：" + "，".join(f"{k}:{len(v)}" for k, v in sorted(pools.items())))
    if muted:
        print(f"剔除 {muted} 个单独发声为哑的音色（需噪声命令叠加）")

    blocks, rows = [], []
    cursor: dict[str, int] = {}
    for slot in range(128):
        fam = GM_FAMILY[slot // 8]
        pool = pools.get(fam, [])
        if pool:
            i = cursor.get(fam, 0)
            v = pool[i % len(pool)]
            cursor[fam] = i + 1
            name = v.name.strip().replace("TOUHOU_", "TH_")
            src = f"TOUHOU idx{cursor[fam] - 1}"
        else:
            v = necopn[slot]
            name = f"NEC_{v.name.strip()[:24]}"
            src = f"NECOPN#{slot}"
        blocks.append(to_text(v, slot, name, src))
        rows.append([slot, fam, src, name])

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n\n".join(blocks) + "\n")

    rep = os.path.splitext(OUT)[0] + "_report.csv"
    with open(rep, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["GM#", "Family", "Source", "Name"])
        w.writerows(rows)

    used_th = sum(1 for r in rows if r[2].startswith("TOUHOU"))
    print(f"\n生成 {OUT}：128 槽")
    print(f"  东方音色 {used_th} 槽，NECOPN 补位 {128 - used_th} 槽")
    by = {}
    for r in rows:
        by.setdefault(r[1], [0, 0])
        by[r[1]][0 if r[2].startswith("TOUHOU") else 1] += 1
    for fam, (a, b) in by.items():
        print(f"  {fam:9s} 东方 {a:2d} / NECOPN {b:2d}")
    print(f"报表：{rep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
