# -*- coding: utf-8 -*-
"""OPM 音色池盘点 —— 给每个 GM 槽位数一数"有多少合格候选"。

不生成音色库，只回答三个问题：
  1. 池子里有多少**不重复**的音色（按参数指纹去重）
  2. 每个 GM 槽位有多少候选是"名字命中"的（真正对得上乐器名）
  3. 按产地分层后，各层供给是否足够

    python tool/pool_scan.py --pool "D:\\gf\\OPM FB01BANK"
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BLOCK_RE = re.compile(r"(@:?\d+[\s\S]*?)(?=\n@:?\d+|\Z)")

# 每个槽位的"名字信号"：GM 音色名里能用来检索的关键词
SLOT_KEYWORDS = {
    0: ["piano", "grand"], 1: ["piano", "bright"], 2: ["piano", "electric"],
    3: ["piano", "honky"], 4: ["ep", "rhodes", "electric piano"],
    5: ["ep", "rhodes", "electric piano"], 6: ["harpsi", "clav"], 7: ["clav"],
    8: ["celesta"], 9: ["glocken", "bell"], 10: ["music box", "box"],
    11: ["vibra", "vibe"], 12: ["marimba"], 13: ["xylo"], 14: ["tubular", "bell"],
    15: ["dulcimer", "cimbalom"], 16: ["drawbar", "organ"], 17: ["perc", "organ"],
    18: ["rock organ", "organ"], 19: ["church", "organ"], 20: ["reed organ", "organ"],
    21: ["accordion", "acc"], 22: ["harmonica", "harm"], 23: ["tango", "accordion"],
    24: ["nylon", "guitar", "gt"], 25: ["steel", "guitar"], 26: ["jazz", "guitar"],
    27: ["clean", "guitar"], 28: ["mute", "guitar"], 29: ["overdrive", "guitar"],
    30: ["distort", "guitar"], 31: ["harmonic", "guitar"],
    32: ["bass", "acoustic"], 33: ["finger", "bass"], 34: ["pick", "bass"],
    35: ["fretless", "bass"], 36: ["slap"], 37: ["slap"], 38: ["synth bass", "bass"],
    39: ["synth bass", "bass"], 40: ["violin", "vln"], 41: ["viola"],
    42: ["cello", "vcl"], 43: ["contrabass", "bass"], 44: ["tremolo"],
    45: ["pizzicato", "pizz"], 46: ["harp"], 47: ["timpani"],
    48: ["strings", "str"], 49: ["ensemble", "strings"], 50: ["synth strings"],
    51: ["synth strings"], 52: ["choir", "aah"], 53: ["voice", "ooh"],
    54: ["synth voice", "vox"], 55: ["orchestra hit", "hit"],
    56: ["trumpet", "trp"], 57: ["trombone", "tbn"], 58: ["tuba"],
    59: ["mute", "trumpet"], 60: ["french horn", "horn"], 61: ["brass"],
    62: ["synth brass"], 63: ["synth brass"], 64: ["soprano", "sax"],
    65: ["alto", "sax"], 66: ["tenor", "sax"], 67: ["baritone", "sax"],
    68: ["oboe"], 69: ["english horn"], 70: ["bassoon"], 71: ["clarinet", "cla"],
    72: ["piccolo"], 73: ["flute", "flu"], 74: ["recorder"], 75: ["pan flute", "pan"],
    76: ["bottle"], 77: ["shakuhachi"], 78: ["whistle"], 79: ["ocarina"],
    80: ["square", "lead"], 81: ["saw", "lead"], 82: ["calliope"], 83: ["chiff"],
    84: ["charang"], 85: ["voice", "lead"], 86: ["fifths"], 87: ["bass lead"],
    88: ["pad", "new age"], 89: ["warm pad", "pad"], 90: ["poly", "pad"],
    91: ["choir pad", "pad"], 92: ["bowed"], 93: ["metal", "pad"], 94: ["halo"],
    95: ["sweep"], 96: ["rain"], 97: ["soundtrack"], 98: ["crystal"],
    99: ["atmosphere"], 100: ["brightness"], 101: ["goblin"], 102: ["echo"],
    103: ["sci-fi", "scifi"], 104: ["sitar"], 105: ["banjo"], 106: ["shamisen"],
    107: ["koto"], 108: ["kalimba"], 109: ["bagpipe"], 110: ["fiddle"],
    111: ["shanai"], 112: ["tinkle", "bell"], 113: ["agogo"], 114: ["steel drum"],
    115: ["woodblock"], 116: ["taiko"], 117: ["tom"], 118: ["synth drum", "drum"],
    119: ["reverse", "cymbal"], 120: ["fret noise"], 121: ["breath"],
    122: ["seashore", "wave"], 123: ["bird"], 124: ["telephone"], 125: ["helicopter"],
    126: ["applause"], 127: ["gunshot", "gun"],
}

FAMILY = ["piano", "chroma", "organ", "guitar", "bass", "strings", "ensemble",
          "brass", "reed", "pipe", "lead", "pad", "fx", "ethnic", "perc", "sfx"]


def family_of(prog: int) -> str:
    return FAMILY[min(15, prog // 8)]


def fingerprint(text: str) -> str:
    """只取参数行（LFO/CH/M1-C2），忽略名字和注释。"""
    keep = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("LFO:", "CH:", "M1:", "C1:", "M2:", "C2:")):
            keep.append(" ".join(s.split()))
    return hashlib.md5("\n".join(keep).encode()).hexdigest()[:12]


def tier_of(fn: str) -> str:
    """按文件名判产地。这只是初筛，真伪还得靠人确认。"""
    up = fn.upper()
    if "FB01ROM" in up or re.match(r"^[A-Z]{3}-[A-Z]{3}_\d\.OPM$", up):
        return "A_fb01"          # FB-01 ROM 或 FB-01 音色库分族
    if re.match(r"^\d\d[ .]", fn):
        return "C_song"          # 街机曲目 dump（名字多为 CH1/Ins 0）
    if re.match(r"^\d+_\d\.OPM$", up):
        return "B_gamebank"      # 10_1.opm 这类游戏音色库
    return "D_other"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--show", type=int, default=8, help="每个家族显示几个样例")
    args = ap.parse_args(argv)

    files = glob.glob(os.path.join(args.pool, "*.opm"))
    files = [f for f in files if os.path.basename(f).upper() != "OPM_ULTIMATE_GM.OPM"]

    seen = set()
    by_tier = Counter()
    per_slot = defaultdict(list)     # slot -> [(tier, name, src)]
    total = 0

    for path in files:
        fn = os.path.basename(path)
        tier = tier_of(fn)
        txt = open(path, encoding="utf-8", errors="ignore").read()
        for blk in BLOCK_RE.findall(txt):
            blk = blk.strip()
            if len(blk) < 10:
                continue
            total += 1
            fp = fingerprint(blk)
            if fp in seen:
                continue
            seen.add(fp)
            by_tier[tier] += 1

            name = blk.splitlines()[0]
            name = re.sub(r"^@:?\d+", "", name).strip().lower()
            for slot, kws in SLOT_KEYWORDS.items():
                if any(kw in name for kw in kws):
                    per_slot[slot].append((tier, name, fn))

    print(f"扫描 {len(files)} 个文件，{total} 个音色块 → **{len(seen)} 个不重复音色**\n")
    print("产地分层（去重后）：")
    for t, n in by_tier.most_common():
        print(f"  {t:<11} {n:5d}  ({n/max(1,len(seen))*100:.0f}%)")

    print("\n每个 GM 槽位的『名字命中』供给：")
    print(f"{'槽':>4} {'家族':<9} {'A_fb01':>7} {'B_game':>7} {'C_song':>7}  样例")
    empty = []
    for slot in range(128):
        cand = per_slot.get(slot, [])
        c = Counter(t for t, _n, _s in cand)
        if not cand:
            empty.append(slot)
        sample = cand[0][1][:22] if cand else "—"
        print(f"{slot:4d} {family_of(slot):<9} {c['A_fb01']:7d} {c['B_gamebank']:7d} "
              f"{c['C_song']:7d}  {sample}")

    print(f"\n没有任何名字命中的槽位：{len(empty)} 个 {empty}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
