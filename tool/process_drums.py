# -*- coding: utf-8 -*-
"""把鼓采样处理成"老味"版本（22 kHz + 4.5 kHz 低通 + 8-bit 降比特）。

移植自主干 GAI 版的 drum_samples/process_drums.py，改成可以指定目录：

    python tool/process_drums.py                    # 默认处理 ./drum_samples
    python tool/process_drums.py D:\\my\\samples     # 指定目录
    python tool/process_drums.py --inplace          # 原地覆盖（先备份！）

输出在 <目录>/lofi/ 下。播放时把「PCM 目录」指向 lofi/ 即可
（加载器只扫单层目录，不会递归进子文件夹）。

需要 ffmpeg 在 PATH 里。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(os.path.dirname(HERE), "drum_samples")

# 当年硬件 DAC 的听感：22 kHz、4.5 kHz 以上砍掉、8-bit 颗粒
FFMPEG_ARGS = [
    "-ar", "22050",
    "-af", "lowpass=f=4500, acrusher=bits=8:mode=log:aa=1",
    "-c:a", "pcm_s16le",
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?", default=DEFAULT_DIR, help="采样目录（默认 ./drum_samples）")
    ap.add_argument("--inplace", action="store_true", help="直接覆盖原文件（会先备份到 .bak/）")
    args = ap.parse_args(argv)

    if not shutil.which("ffmpeg"):
        print("找不到 ffmpeg，请先安装并加入 PATH", file=sys.stderr)
        return 2

    src = os.path.abspath(args.src)
    if not os.path.isdir(src):
        print(f"目录不存在: {src}", file=sys.stderr)
        return 2

    wavs = sorted(f for f in os.listdir(src) if f.lower().endswith(".wav"))
    if not wavs:
        print(f"{src} 里没有 WAV。先把 KORG M1 波形的鼓组 WAV 放进去。")
        return 1

    out_dir = src if args.inplace else os.path.join(src, "lofi")
    os.makedirs(out_dir, exist_ok=True)
    if args.inplace:
        bak = os.path.join(src, ".bak")
        os.makedirs(bak, exist_ok=True)
        for f in wavs:
            shutil.copy2(os.path.join(src, f), os.path.join(bak, f))

    print(f"处理 {len(wavs)} 个文件 → {out_dir}")
    ok = 0
    for name in wavs:
        cmd = ["ffmpeg", "-y", "-i", os.path.join(src, name), *FFMPEG_ARGS,
               os.path.join(out_dir, name)]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            ok += 1
            print(f"  [OK] {name}")
        except subprocess.CalledProcessError:
            print(f"  [失败] {name}")
    print(f"完成 {ok}/{len(wavs)}。")
    if not args.inplace:
        print(f"播放时把「PCM 目录」指向：{out_dir}")
    return 0 if ok == len(wavs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
