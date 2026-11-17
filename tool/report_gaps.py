# -*- coding: utf-8 -*-
"""列出"凑合填补"的槽位 —— 哪些 GM 槽位不是名字命中，而是靠家族兜底塞进去的。

    python tool/report_gaps.py --bank-report FB01_GM_v4_report.csv

输出按风险排序：名字里连相关词都没有的排最前，那些就是你最该试听的。
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_gm_from_pool import FAMILY_FALLBACK, family_of

GM_NAMES = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet",
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone",
    "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)", "Electric Guitar (jazz)",
    "Electric Guitar (clean)", "Electric Guitar (muted)", "Overdriven Guitar",
    "Distortion Guitar", "Guitar Harmonics",
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
    "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
    "Violin", "Viola", "Cello", "Contrabass", "Tremolo Strings",
    "Pizzicato Strings", "Orchestral Harp", "Timpani",
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2",
    "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn", "Brass Section",
    "Synth Brass 1", "Synth Brass 2",
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe", "English Horn",
    "Bassoon", "Clarinet",
    "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi",
    "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)",
    "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass + lead)",
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)",
    "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
    "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bag pipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko Drum", "Melodic Tom",
    "Synth Drum", "Reverse Cymbal", "Guitar Fret Noise", "Breath Noise", "Seashore",
    "Bird Tweet", "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]


def risk_of(slot: int, patch: str, lineage: str) -> tuple[int, str]:
    """0=低 1=中 2=高。看填入的音色名和 GM 目标名还剩多少关联。"""
    fam = family_of(slot)
    target = GM_NAMES[slot].lower()
    name = patch.lower()
    words = [w for w in re.findall(r"[a-z]+", target) if len(w) > 2
             and w not in ("strings", "synth", "electric", "acoustic", "guitar", "bass",
                           "lead", "pad", "fx", "organ", "piano", "drum")]
    if any(w in name for w in words):
        return 0, "名字里还带目标词"

    kws = [k for k in FAMILY_FALLBACK.get(fam, []) if len(k) >= 3]   # 太短的词会误匹配
    hit = [k for k in kws if k in name]

    # 音效/民族打击这类"具体物件"，沾点家族词根本不算数
    concrete = fam == "sfx" or slot in (111, 114, 115, 116, 117)
    if concrete:
        return 2, f"具体物件，只沾 {hit[0]}" if hit else "具体物件，毫不相关"
    if hit:
        return 1, f"只沾家族词 {hit[0]}"
    return 2, "毫不相关"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank-report", required=True)
    args = ap.parse_args(argv)

    rows = list(csv.DictReader(open(args.bank_report, encoding="utf-8")))
    gaps = [r for r in rows if r["How"] != "名字命中"]

    print(f"凑合填补的槽位：{len(gaps)}/{len(rows)}\n")
    out = []
    for r in gaps:
        slot = int(r["GM#"])
        lvl, why = risk_of(slot, r["Patch"], r["Lineage"])
        out.append((lvl, slot, r, why))
    out.sort(key=lambda x: (-x[0], x[1]))

    label = {2: "高", 1: "中", 0: "低"}
    last = None
    for lvl, slot, r, why in out:
        if lvl != last:
            print(f"\n===== 风险 {label[lvl]} =====")
            print(f"{'槽':>3} {'GM 目标名':<24} {'实际填入':<20} {'血统':<12} 说明")
            last = lvl
        print(f"{slot:3d} {GM_NAMES[slot][:24]:<24} {r['Patch'][:20]:<20} "
              f"{r['Lineage']:<12} {why}")

    hi = [s for l, s, _r, _w in out if l == 2]
    print(f"\n高风险 {len(hi)} 个：{hi}")
    print("\n试听某个槽位（该音色全通道锁定）：")
    print(f'  .\\play.cmd "D:\\music\\5.mid" --bank {os.path.basename(args.bank_report).replace("_report.csv", ".opm")} --voice {hi[0] if hi else 0} --duration 6')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
