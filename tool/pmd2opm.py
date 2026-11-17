# -*- coding: utf-8 -*-
"""把「东方FMPMD音色.txt」转成 VOPM .opm 音色库。

源文件是 GBK 编码的中文整理表，每行是 PMD 的音色定义：

    @NNN <alg> <fb>  [op1] [op2] [op3] [op4]   ;<FF序号> <描述>
    每个 op 11 个值：AR DR SR RR SL TL KS MUL DT1 DT2 AMS-EN

两个必须处理的差异：

1. **算子顺序**：PMD 按物理序列出（M1, M2, C1, C2），VOPM 的 .opm 要
   （M1, C1, M2, C2）。所以输出时要把 op2 和 op3 对调。
   —— 这个顺序是从数据本身推出来的：算法 4 的钢琴类音色里，
   op1≈op3、op2≈op4，说明 (op1,op3) 和 (op2,op4) 才是两条 2-op 链。

2. **alg / fb 的顺序**：源文件按 PMD 惯例是 `alg fb`，写进 .opm 的 CH 行时
   要变成 `FL(fb) CON(alg)`。如果听着不对劲，用 --swap 反过来。

用法：
    python tool/pmd2opm.py --src "D:\\...\\东方FMPMD音色.txt" --out TOUHOU_PC98.opm
"""
from __future__ import annotations

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

LINE = re.compile(
    r"^@(\d+)\s+(\d)\s+(\d)\s+"          # @号 alg fb
    r"([\d\s]+?)\s*(?:;(.*))?$"           # 44 个参数 + 注释
)


def parse(path: str, swap: bool, only_indexed: bool = True) -> list[dict]:
    """返回 [{'idx','alg','fb','ops'[[11]],'desc'}]。"""
    out = []
    seen = set()
    for raw in open(path, encoding="gbk", errors="ignore"):
        m = LINE.match(raw.strip())
        if not m:
            continue
        slot, a, b = m.group(1), int(m.group(2)), int(m.group(3))
        vals = [int(x) for x in m.group(4).split()]
        if len(vals) < 44:
            continue
        alg, fb = (b, a) if swap else (a, b)
        ops = [vals[i * 11:(i + 1) * 11] for i in range(4)]
        comment = (m.group(5) or "").strip()

        # 注释形如 "3 声音较短的Piano TH02_1" 或 "Common"
        ff_idx, desc = None, comment
        mm = re.match(r"^(\d+)\s*(.*)$", comment)
        if mm:
            ff_idx, desc = int(mm.group(1)), mm.group(2).strip()
        # 文件里有两段：原始版（注释只有 Common / Th02_10）和对齐版（带 FF 序号）。
        # 只要对齐版，否则同一音色会重复两次。
        if only_indexed and ff_idx is None:
            continue

        key = (ff_idx, tuple(map(tuple, ops)))
        if key in seen:
            continue
        seen.add(key)
        out.append({"slot": slot, "alg": alg, "fb": fb, "ops": ops,
                    "ff": ff_idx, "desc": desc})
    return out


def to_opm_block(e: dict, index: int) -> str:
    """物理序 M1,M2,C1,C2 → VOPM 的 M1,C1,M2,C2。"""
    m1, m2, c1, c2 = e["ops"]
    name = e["desc"] or f"PMD_{e['slot']}"
    name = re.sub(r"\s+", "_", name)[:40]
    head = f"@:{index} TOUHOU_{name}"
    ch = f"CH: 64 {e['fb']} {e['alg']} 0 0 120 0"
    lines = [head, "LFO: 0 0 0 0 0", ch]
    for tag, op in (("M1", m1), ("C1", c1), ("M2", m2), ("C2", c2)):
        v = list(op[:11]) + [0] * (11 - len(op))     # 第 11 个是 AMS-EN
        lines.append(f"{tag}: " + " ".join(str(x) for x in v))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=r"D:\GamedataUnpack\pmdconv\pmdconv-master\旧作FM音色\东方FMPMD音色.txt")
    ap.add_argument("--out", default=os.path.join(ROOT, "TOUHOU_PC98.opm"))
    ap.add_argument("--swap", action="store_true", help="alg/fb 顺序反过来")
    ap.add_argument("--index-from", choices=["ff", "seq"], default="ff",
                    help="用注释里的 FF 序号还是文件顺序作为 @ 索引")
    args = ap.parse_args(argv)

    entries = parse(args.src, args.swap)
    if not entries:
        print("没解析到任何音色", file=sys.stderr)
        return 1
    print(f"解析 {len(entries)} 条音色（alg/fb {'已交换' if args.swap else '按 PMD 惯例'}）")

    blocks = []
    for i, e in enumerate(entries):
        idx = e["ff"] if (args.index_from == "ff" and e["ff"] is not None) else i
        blocks.append(to_opm_block(e, idx))

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n\n".join(blocks) + "\n")

    idxs = sorted({(e["ff"] if args.index_from == "ff" and e["ff"] is not None else i)
                   for i, e in enumerate(entries)})
    print(f"写出 {len(blocks)} 个槽位 → {args.out}")
    print(f"索引范围 {idxs[0]}..{idxs[-1]}")
    algs = {}
    for e in entries:
        algs[e["alg"]] = algs.get(e["alg"], 0) + 1
    print("算法分布: " + "，".join(f"{k}:{v}" for k, v in sorted(algs.items())))
    print("前 5 条: " + "、".join(e["desc"][:16] or f"PMD_{e['slot']}" for e in entries[:5]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
