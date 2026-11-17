# -*- coding: utf-8 -*-
"""从 OPM 音色池按 GM 槽位检索并组装音色库。

和旧 gm_auto_builder 的根本区别：**先有槽位，再去检索**。
旧版是"每个 patch 打分归桶 → 桶内按分数取前 N"，桶里没有槽位概念，
所以 Vibraphone 和 Marimba 拿到谁全凭运气。

    python tool/build_gm_from_pool.py --pool "D:\\gf\\OPM FB01BANK" --out FB01_GM_v2.opm

血统分层（高到低）：
    FB01(OPP)   YM2164，最正统
    TX81Z(OPZ)  YM2414，同系
    街机OST(OPM) YM2151 游戏 dump，真 OPM 但曲目专用
    MDX(OPN)    PC-98 MXDRV 标准音色，OPN 血统
    其他
每个槽位在输出里带一行注释 `// lineage=... src=... name=...`，
播放器会忽略它，但你可以随时看出某个槽位是什么血统。
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pool_scan import SLOT_KEYWORDS, family_of

BLOCK_RE = re.compile(r"(@:?\d+[\s\S]*?)(?=\n@:?\d+|\Z)")

LINEAGE_WEIGHT = {
    "FB01(OPP)": 40,
    "TX81Z(OPZ)": 30,
    "X68K(MDX)": 26,
    "街机OST(OPM)": 20,
    "MDX(OPN)": 12,
    "其他": 8,
}

# mdx2opm 会把 .mdx 的**全局 LFO** 抄进每个音色块（实测 6732/6732 都是 128/64/64）。
# 直接导入 = 每个音都带满档颤音，所以导入时归一化。
MDX_LFO = "LFO: 0 0 0 0 0"

CARRIER = {
    0: (0, 0, 0, 1), 1: (0, 0, 0, 1), 2: (0, 0, 0, 1), 3: (0, 0, 1, 1),
    4: (0, 1, 0, 1), 5: (0, 1, 1, 1), 6: (0, 1, 1, 1), 7: (1, 1, 1, 1),
}
PERCUSSIVE_FAMS = {"piano", "chroma", "guitar", "perc"}

# 只取可跨芯片移植的包络字段（OPM 与 OPN 的这些字段语义相同）
PROFILE_FIELDS = ("ar", "d1r", "d2r", "sl", "rr")
PROFILE_SCALE = {"ar": 31.0, "d1r": 31.0, "d2r": 31.0, "sl": 15.0, "rr": 15.0}


def env_features(block: str):
    """载波的平均包络特征，用于和家族画像比距离。"""
    alg, fb, ops = parse_ops(block)
    if len(ops) != 4:
        return None
    car = [o for o, c in zip(ops, CARRIER.get(alg & 7, CARRIER[0])) if c]
    if not car:
        return None
    return {
        "ar": max(o[0] for o in car),
        "d1r": sum(o[1] for o in car) / len(car),
        "d2r": sum(o[2] for o in car) / len(car),
        "sl": max(o[4] for o in car),
        "rr": max(o[3] for o in car),
    }


def load_profiles(path: str):
    """从参照库统计每个家族的包络中位数。"""
    import statistics as st
    from voice import load_opm_text
    vs = load_opm_text(path)
    out = {}
    for f in range(16):
        rows = []
        for v in vs[f * 8:(f + 1) * 8]:
            if len(v.ops) != 4:
                continue
            alg = v.ch[2] & 7
            car = [o for o, c in zip(v.ops, CARRIER[alg]) if c]
            rows.append({
                "ar": max(o["ar"] for o in car),
                "d1r": sum(o["dr"] for o in car) / len(car),
                "d2r": sum(o["sr"] for o in car) / len(car),
                "sl": max(o["sl"] for o in car),
                "rr": max(o["rr"] for o in car),
            })
        if rows:
            out[f] = {k: st.median([r[k] for r in rows]) for k in PROFILE_FIELDS}
    return out


def profile_distance(feat, target) -> float:
    if feat is None:
        return 9.9
    return sum(abs(feat[k] - target[k]) / PROFILE_SCALE[k] for k in PROFILE_FIELDS) / len(PROFILE_FIELDS)


def parse_ops(block: str):
    """返回 (alg, fb, [(ar,dr,sr,rr,sl,tl,ks,mul,dt1,dt2), ...])"""
    alg = fb = 0
    m = re.search(r"CH:\s*(\d+)\s+(\d+)\s+(\d+)", block)
    if m:
        fb, alg = int(m.group(2)), int(m.group(3))
    ops = []
    for op_str in re.findall(r"(?:M1|C1|M2|C2):\s*([\d\s]+)", block):
        v = list(map(int, op_str.split()))
        if len(v) >= 10:
            ops.append(tuple(v[:10]))
        elif len(v) >= 8:
            ops.append(tuple(v[:8]) + (0, 0))
    return alg, fb, ops


def reject_reason(block: str, fam: str):
    """硬性否决：这类音色不可能对。返回原因字符串，或 None 表示通过。"""
    alg, fb, ops = parse_ops(block)
    if len(ops) != 4:
        return "算子数异常"
    car = [o for o, c in zip(ops, CARRIER.get(alg & 7, CARRIER[0])) if c]
    if not car:
        return "无载波"
    if all(o[5] >= 120 for o in car):
        return "载波静音"
    max_ar = max(o[0] for o in car)
    min_ar_all = min(o[0] for o in ops)      # 调制器慢起音同样会让声音"涨"起来
    if fam in PERCUSSIVE_FAMS:
        if min_ar_all < 18:
            return f"敲击类有算子慢起音 AR={min_ar_all}"
        # 敲击类必须会衰减
        if max(o[1] for o in car) == 0 and max(o[4] for o in car) == 0:
            return "敲击类无衰减（D1R=SL=0）"
    if fam == "bass" and min_ar_all < 20:
        return f"贝斯有算子慢起音 AR={min_ar_all}"
    if fam in ("lead", "brass") and min_ar_all < 12:
        return f"{fam}有算子慢起音 AR={min_ar_all}"
    return None


FAMILY_FALLBACK = {    "piano": ["piano", "grand", "ep", "rhodes", "e.piano"],
    "chroma": ["bell", "chime", "vibe", "marimba", "xylo", "celesta", "glocken", "box"],
    "organ": ["organ", "drawbar", "pipe organ"],
    "guitar": ["guitar", "gt", "pluck", "nylon", "steel"],
    "bass": ["bass", "bas", "ebass", "sub"],
    "strings": ["strings", "str", "violin", "vln", "cello", "bow", "pizz"],
    "ensemble": ["strings", "ensemble", "choir", "vox", "voice"],
    "brass": ["brass", "trumpet", "trp", "trombone", "horn", "tuba"],
    "reed": ["sax", "reed", "clarinet", "cla", "oboe", "acc"],
    "pipe": ["flute", "flu", "pipe", "whistle", "ocarina"],
    "lead": ["lead", "solo", "square", "saw", "syn"],
    "pad": ["pad", "poly", "warm", "syn"],
    "fx": ["fx", "wind", "echo", "crystal", "atmo"],
    "ethnic": ["sitar", "koto", "shak", "ethnic", "fiddle"],
    "perc": ["perc", "drum", "tom", "taiko", "clav"],
    "sfx": ["sfx", "se", "noise"],
}


def lineage_of(fn: str) -> str:
    up = fn.upper()
    if up.startswith("FB01ROM") or re.match(r"^[A-Z]{3}-[A-Z]{3}_\d\.OPM$", up):
        return "FB01(OPP)"
    if "TX81Z" in up:
        return "TX81Z(OPZ)"
    if "MDX" in up:
        return "MDX(OPN)"
    if re.match(r"^([A-Z]+)_-_(.+?)_-", up) or re.match(r"^\d\d[ .]", fn):
        return "街机OST(OPM)"
    return "其他"


def fingerprint(text: str) -> str:
    keep = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("LFO:", "CH:", "M1:", "C1:", "M2:", "C2:")):
            keep.append(" ".join(s.split()))
    return hashlib.md5("\n".join(keep).encode()).hexdigest()[:12]


def clean_name(block: str) -> str:
    first = block.splitlines()[0]
    return re.sub(r"^@:?\d+", "", first).strip()


def rebuild_block(block: str, slot: int, fam: str, idx: int, lineage: str, src: str, orig: str,
                  normalize_lfo: bool = False) -> str:
    lines = block.splitlines()
    lines[0] = f"@:{slot} GM_{fam.capitalize()}_{idx}"
    note = f"// lineage={lineage} src={src} name={orig}"
    if normalize_lfo:
        lines = [MDX_LFO if l.strip().startswith("LFO:") else l for l in lines]
    return "\n".join([lines[0], note] + lines[1:])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default=None)
    ap.add_argument("--max-per-src", type=int, default=2, help="同一来源文件最多贡献几个槽位")
    ap.add_argument("--allow", default="FB01(OPP),TX81Z(OPZ),街机OST(OPM),MDX(OPN),其他",
                    help="允许的血统，逗号分隔，按优先级从高到低")
    ap.add_argument("--no-strict", action="store_true",
                    help="关闭硬性否决（载波静音 / 慢起音等）")
    ap.add_argument("--profile-from", default=os.path.join(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "NECOPN_GM.opm"),
                    help="家族包络画像的参照库（默认 NECOPN_GM.opm）")
    args = ap.parse_args(argv)

    allow = [x.strip() for x in args.allow.split(",") if x.strip()]

    # ---------- 1. 扫描池子 ----------
    voices = []
    seen = set()
    files = [f for f in glob.glob(os.path.join(args.pool, "*.opm"))
             if os.path.basename(f).upper() != "OPM_ULTIMATE_GM.OPM"]
    for path in files:
        fn = os.path.basename(path)
        try:
            txt = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        # 产地判定：MDX 派生（mdx2opm 转换的 X68000 曲目）优先于文件名规则
        if "mdx2opm" in txt or "MDX Instrument" in txt:
            lin = "X68K(MDX)"
        else:
            lin = lineage_of(fn)
        if lin not in allow:
            continue
        for blk in BLOCK_RE.findall(txt):
            blk = blk.strip()
            if len(blk) < 10:
                continue
            fp = fingerprint(blk)
            if fp in seen:
                continue
            seen.add(fp)
            voices.append({
                "text": blk, "name": clean_name(blk).lower(),
                "src": fn, "lineage": lin, "fp": fp,
            })
    print(f"池子：{len(files)} 个文件 → 去重后 {len(voices)} 个候选")

    by_lin = collections.Counter(v["lineage"] for v in voices)
    print("血统：" + "，".join(f"{k} {n}" for k, n in by_lin.most_common()))

    profiles = {}
    if os.path.exists(args.profile_from):
        profiles = load_profiles(args.profile_from)
        print(f"家族画像：取自 {os.path.basename(args.profile_from)}")
    else:
        print(f"⚠ 找不到画像参照 {args.profile_from}，兜底将退化为血统优先")

    # ---------- 2. 按槽位检索 ----------
    used_fp = set()
    src_count = collections.Counter()
    chosen = [None] * 128
    report = []
    rejected = collections.Counter()
    strict = not args.no_strict

    def pick(slot, keywords, strict=True):
        best = None
        fam_ = family_of(slot)
        for v in voices:
            if v["fp"] in used_fp:
                continue
            if src_count[v["src"]] >= args.max_per_src:
                continue
            name = v["name"]
            hit = 0
            for kw in keywords:
                if name == kw or name.strip() == kw:
                    hit = max(hit, 60)
                elif kw in name:
                    hit = max(hit, 30)
            if hit == 0:
                continue
            if strict:
                why = reject_reason(v["text"], fam_)
                if why:
                    rejected[why] = rejected.get(why, 0) + 1
                    continue
            score = hit + LINEAGE_WEIGHT.get(v["lineage"], 0)
            if best is None or score > best[0]:
                best = (score, v)
        return best

    def pick_by_profile(slot):
        """没有名字命中时，按包络贴合度在所有允许血统里挑（MDX 全部参战）。"""
        target = profiles.get(slot // 8)
        if target is None:
            return None
        fam_ = family_of(slot)
        best = None
        for v in voices:
            if v["fp"] in used_fp:
                continue
            if src_count[v["src"]] >= args.max_per_src:
                continue
            if strict:
                why = reject_reason(v["text"], fam_)
                if why:
                    rejected[why] = rejected.get(why, 0) + 1
                    continue
            dist = profile_distance(env_features(v["text"]), target)
            score = -dist * 100 + LINEAGE_WEIGHT.get(v["lineage"], 0) * 0.01
            if best is None or score > best[0]:
                best = (score, v, dist)
        return best

    for slot in range(128):
        fam = family_of(slot)
        got = pick(slot, SLOT_KEYWORDS[slot])
        guessed = False
        dist = 0.0
        if got is None:
            got2 = pick_by_profile(slot)
            guessed = True
            if got2 is not None:
                _s, v, dist = got2
                got = (_s, v)
        if got is None:
            chosen[slot] = None
            report.append([slot, fam, "EMPTY", "", "", 0, "无候选"])
            continue
        score, v = got
        used_fp.add(v["fp"])
        src_count[v["src"]] += 1
        chosen[slot] = (v, guessed)
        report.append([slot, fam, v["lineage"], v["src"], v["name"], round(score),
                       "名字命中" if not guessed else f"包络匹配 d={dist:.2f}"])

    # ---------- 3. 输出 ----------
    blocks = []
    for slot in range(128):
        item = chosen[slot]
        fam = family_of(slot)
        if item is None:
            blocks.append(f"@:{slot}\n// EMPTY {fam} SLOT\nLFO: 0 0 0 0 0\n"
                          f"CH: 64 0 7 0 0 120 0\nM1: 31 0 0 15 0 0 0 1 0 0\n"
                          f"C1: 31 0 0 15 0 0 0 1 0 0\nM2: 31 0 0 15 0 0 0 1 0 0\n"
                          f"C2: 31 0 0 15 0 0 0 1 0 0")
            continue
        v, guessed = item
        blocks.append(rebuild_block(v["text"], slot, fam, slot % 8 + 1,
                                    v["lineage"], v["src"], v["name"],
                                    normalize_lfo=(v["lineage"] == "X68K(MDX)")))

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n\n".join(blocks) + "\n")

    rep_path = args.report or (os.path.splitext(args.out)[0] + "_report.csv")
    with open(rep_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["GM#", "Family", "Lineage", "Source", "Patch", "Score", "How"])
        w.writerows(report)

    # ---------- 4. 总结 ----------
    filled = sum(1 for c in chosen if c is not None)
    lin_sel = collections.Counter(r[2] for r in report if r[2] != "EMPTY")
    guessed_n = sum(1 for r in report if r[6] == "家族推测")
    guessed_n = sum(1 for r in report if r[2] != "EMPTY" and r[6] != "名字命中")
    print(f"\n生成 {args.out}：{filled}/128 槽位")
    print("入选血统：" + "，".join(f"{k} {n}" for k, n in lin_sel.most_common()))
    print(f"名字命中 {filled - guessed_n}，包络兜底 {guessed_n}，空槽 {128 - filled}")

    empty = [r[0] for r in report if r[2] == "EMPTY"]
    if empty:
        print(f"空槽：{empty}")
    if rejected:
        print("被硬性否决的候选：" + "，".join(f"{k} ×{n}" for k, n in rejected.most_common(6)))
    multi = {s: n for s, n in src_count.items() if n >= args.max_per_src}
    if multi:
        print("贡献最多的来源：" + "，".join(f"{s}({n})" for s, n in
                                        sorted(multi.items(), key=lambda x: -x[1])[:6]))
    print(f"报表：{rep_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
