# rtplayer.py
# -*- coding: utf-8 -*-
"""OPM 实时播放器 v0 —— MIDI 直接出声，不落盘。

    python rtplayer.py <某个.mid>
    python rtplayer.py <某个.mid> --duration 20 --blocksize 128
    python rtplayer.py <某个.mid> --device "WASAPI"

当前版本只做：从头播放 → 播完退出 / Ctrl+C 停止。
暂停、跳转、进度条是下一步（见 README 路线图）。
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

import numpy as np
import sounddevice as sd

from core import OpmCore
from driver import OpmSystem
from rt_engine import Engine, Renderer, SR_OUT_DEFAULT
from smf import parse_smf
from voice import load_opm_text

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BANK = os.path.join(HERE, "NECOPN_GM.opm")


def pick_device(rule):
    """挑一个输出设备。

    rule 为 None 时优先选 WASAPI（延迟最低、能开 48 kHz）；
    否则按索引或名字子串匹配。
    """
    devs = sd.query_devices()
    if rule is not None:
        try:
            idx = int(rule)
            return idx, devs[idx]
        except ValueError:
            pass
        for i, d in enumerate(devs):
            if d["max_output_channels"] > 0 and rule.lower() in d["name"].lower():
                return i, d
        raise SystemExit(f"找不到匹配 '{rule}' 的输出设备")

    default_name = ""
    try:
        default_name = sd.query_devices(sd.default.device[1])["name"]
    except Exception:
        pass

    # 1) WASAPI 里同名的设备
    for i, d in enumerate(devs):
        if d["max_output_channels"] >= 2 and sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI":
            if default_name and default_name == d["name"]:
                return i, d
    # 2) 任意 WASAPI 输出
    for i, d in enumerate(devs):
        if d["max_output_channels"] >= 2 and sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI":
            return i, d
    # 3) 系统默认
    idx = sd.default.device[1]
    return idx, devs[idx]


class Stats:
    __slots__ = ("underrun", "xrun", "played")

    def __init__(self):
        self.underrun = 0
        self.xrun = 0
        self.played = 0


def resolve_voice(voices, query):
    """把 '名字/index' 解析成库里的音色下标。名字支持去空格后的全等或唯一子串。"""
    q = query.strip()
    if q.lstrip("-").isdigit():
        idx = int(q)
        if not (0 <= idx < len(voices)):
            raise SystemExit(f"音色下标 {idx} 越界（库里有 {len(voices)} 个）")
        return idx

    lower = q.lower()
    exact = [i for i, v in enumerate(voices) if v.name.strip().lower() == lower]
    if len(exact) == 1:
        return exact[0]
    sub = [i for i, v in enumerate(voices) if lower in v.name.strip().lower()]
    if len(sub) == 1:
        return sub[0]
    if not sub:
        raise SystemExit(f"找不到名字包含 '{query}' 的音色（用 --list-voices 看清单）")
    cand = "、".join(f"[{i}] {voices[i].name.strip() or '(无名)'}" for i in sub[:10])
    raise SystemExit(f"'{query}' 匹配到 {len(sub)} 个音色，请写得更具体：{cand}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="OPM 实时播放器 v0")
    ap.add_argument("midi", nargs="?", help="标准 MIDI 文件")
    ap.add_argument("--bank", default=DEFAULT_BANK, help=".opm 音色库路径")
    ap.add_argument("--core", default="opm", choices=["opm", "null"])
    ap.add_argument("--chip", default="opm", choices=["opm", "opp", "opz"])
    ap.add_argument("--clock", type=int, default=3_579_545)
    ap.add_argument("--device", default=None, help="输出设备索引或名字子串（默认自动选 WASAPI）")
    ap.add_argument("--blocksize", type=int, default=256, help="音频块大小（帧）")
    ap.add_argument("--sr", type=int, default=SR_OUT_DEFAULT, help="输出采样率")
    ap.add_argument("--duration", type=float, default=None, help="只播这么多秒")
    ap.add_argument("--tail", type=float, default=1.0, help="尾部余响秒数")
    ap.add_argument("-r", "--raw", action="store_true", help="关闭 OPN→OPM 翻译")
    ap.add_argument("--list-devices", action="store_true", help="列出输出设备后退出")
    ap.add_argument("--list-voices", action="store_true", help="列出音色库里的音色后退出")
    ap.add_argument("--voice", default=None,
                    help="锁定单个音色试听：名字或下标（忽略 MIDI 的 program change）")
    ap.add_argument("--ssg", action="store_true",
                    help="用 SSG(YM2149) 演奏底鼓/军鼓/踩镲（GAI2 方案）")
    ap.add_argument("--ssg-clean", action="store_true",
                    help="SSG 鼓改用修正后的混音位值（真噪声军鼓/踩镲）")
    ap.add_argument("--ssg-gain", type=float, default=2.0,
                    help="SSG 混音增益，默认 2.0（等价 GAI2 的 (opm + ssg*4)>>1）")
    ap.add_argument("--ssg-nodc", action="store_true",
                    help="关闭 SSG 隔直（默认开启，模拟真机输出耦合电容）")
    ap.add_argument("--pcm", action="store_true",
                    help="用 PCM 采样鼓（GAI 方案，X68K 致敬）")
    ap.add_argument("--pcm-gain", type=float, default=0.5,
                    help="PCM 混音增益（默认 0.5；实测 1.0 会让总线顶到满幅削波）")
    ap.add_argument("--pcm-voices", type=int, default=8,
                    help="PCM 复音数（默认 8；X68K 靠 CPU 软混做到多路，不做单声道阉割）")
    ap.add_argument("--pcm-lofi", action="store_true",
                    help="PCM 走 12-bit 降比特 + 低通（X68K 味道）")
    ap.add_argument("--drum-dir", default=None, help="鼓采样目录（默认 ./drum_samples）")
    ap.add_argument("--export", default=None,
                    help="不播放，直接把这一场渲染成 WAV（与实时播放同一条链）")
    ap.add_argument("--export-sr", type=int, default=48000,
                    help="导出采样率：48000 与播放一致；55930 为芯片原生（跳过重采样）")
    ap.add_argument("--loop", action="store_true", help="播完自动从头循环")
    args = ap.parse_args(argv)

    if args.list_devices:
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0:
                api = sd.query_hostapis(d["hostapi"])["name"]
                print(f"[{i:2d}] {d['name']}  ch={d['max_output_channels']}  "
                      f"sr={d['default_samplerate']:.0f}  {api}")
        return 0

    if not os.path.exists(args.bank):
        print(f"找不到音色库: {args.bank}", file=sys.stderr)
        return 2

    voices = load_opm_text(args.bank)

    if args.list_voices:
        print(f"{os.path.basename(args.bank)}：{len(voices)} 个音色")
        for i, v in enumerate(voices):
            print(f"[{i:4d}] {v.name.strip() or '(无名)'}")
        return 0

    if not args.midi:
        ap.error("需要 MIDI 文件（除非用 --list-voices / --list-devices）")

    # ---- 导出模式：走和播放完全相同的渲染链（SSG/PCM/芯片/混音/隔直都一致）----
    if args.export:
        from player_session import Config, render_wav
        drums = "fm"
        if args.ssg:
            drums = "ssg-clean" if args.ssg_clean else "ssg"
        if args.pcm:
            drums = ("ssg-clean+pcm" if args.ssg_clean
                     else ("ssg+pcm" if args.ssg else "pcm"))
        cfg = Config(midi=args.midi, bank=args.bank, chip=args.chip, drums=drums,
                     drum_dir=args.drum_dir or os.path.join(HERE, "drum_samples"),
                     blocksize=args.blocksize, pcm_voices=args.pcm_voices,
                     pcm_lofi=args.pcm_lofi, pcm_gain=args.pcm_gain,
                     ssg_gain=args.ssg_gain, raw=args.raw, tail=args.tail, volume=1.0)
        print(f"导出: {args.export}  ({args.export_sr} Hz, 芯片={args.chip}, 鼓={drums})")
        info = render_wav(cfg, args.export, sr_out=args.export_sr)
        print(f"完成: {info['frames']} 帧 = {info['seconds']:.2f}s  "
              f"peak={info['peak']}/32767")
        return 0

    while True:
        rc = _play_once(args, voices)
        if rc != 0 or not args.loop:
            return rc
        print("\n[循环] 从头再来…")


def _play_once(args, voices) -> int:
    """完整播放一次。循环模式下会被反复调用，每次都重建芯片。"""
    # ---- 构建合成器（与 player.py 完全一致）----
    data = open(args.midi, "rb").read()
    events, tpq, total_sec = parse_smf(data)

    if args.core == "opm":
        flags = 1 if args.chip == "opp" else 0
        core = OpmCore(clock=args.clock, flags=flags)
    else:
        from core import NullCore
        core = NullCore(clock=args.clock)

    system = OpmSystem(core, chip_mode=args.chip)
    system.load_bank(voices)
    system.translate_pitch = not args.raw
    system.translate_env = not args.raw

    locked = None
    if args.voice is not None:
        locked = resolve_voice(voices, args.voice)
        system.lock_program = True
        system.programs = [locked] * 16

    # ---- 可选：SSG 声源 ----
    ssg = None
    if args.ssg:
        from ssg_core import SsgCore
        ssg = SsgCore()
        system.ssg = ssg
        system.ssg_drums = True
        system.ssg_recipe = "clean" if args.ssg_clean else "legacy"

    # ---- 可选：PCM 采样鼓 ----
    pcm = None
    n_pcm = 0
    if args.pcm:
        from pcm_core import PcmCore, load_drum_dir
        drum_dir = args.drum_dir or os.path.join(HERE, "drum_samples")
        pcm = PcmCore(core.sample_rate(), max_voices=args.pcm_voices, lofi=args.pcm_lofi)
        n_pcm = load_drum_dir(pcm, drum_dir)
        system.pcm = pcm

    renderer = Renderer(core, system, events, total_sec, tail=args.tail,
                        ssg=ssg, ssg_gain=args.ssg_gain,
                        ssg_dc_block=not args.ssg_nodc,
                        pcm=pcm, pcm_gain=args.pcm_gain)
    engine = Engine(renderer, sr_out=args.sr, blocksize=args.blocksize, target_blocks=6)

    dev_idx, dev = pick_device(args.device)
    api = sd.query_hostapis(dev["hostapi"])["name"]

    print(f"音色库: {len(voices)} 条 ({os.path.basename(args.bank)})")
    if locked is not None:
        nm = voices[locked].name.strip() or "(无名)"
        print(f"音色:   锁定为 [{locked}] {nm}（忽略 MIDI 里的 program change）")
    if ssg is not None:
        print(f"SSG:    emu2149 已挂载，鼓配方={system.ssg_recipe}，增益={args.ssg_gain}，"
              f"隔直={'关' if args.ssg_nodc else '开'}")
    if pcm is not None:
        print(f"PCM:    {n_pcm} 个采样，复音={args.pcm_voices}，"
              f"lofi={'开' if args.pcm_lofi else '关'}，增益={args.pcm_gain}")
    print(f"MIDI:   {len(events)} 事件  {total_sec:.2f}s  tpq={tpq}")
    print(f"芯片:   {system.chip_mode}  {renderer.sr} Hz  -> 重采样 -> {args.sr} Hz")
    print(f"设备:   [{dev_idx}] {dev['name']}  ({api})  block={args.blocksize}")

    stats = Stats()
    stop = threading.Event()

    def callback(outdata, frames, time_info, status):
        if status:
            stats.xrun += 1
        nonlocal_played[0] += frames
        outdata[:] = engine.pull(frames)
        if engine.finished():
            stop.set()

    nonlocal_played = [0]

    engine.start()
    # 先攒一点缓冲，避免一开头就 underrun（6 块 ≈ 32ms @48k/256）
    prefill = args.blocksize * 6
    deadline = time.time() + 2.0
    while engine.buffered_frames() < prefill and time.time() < deadline:
        time.sleep(0.005)

    t_start = time.time()
    try:
        with sd.OutputStream(device=dev_idx, samplerate=args.sr, channels=2,
                             dtype="int16", blocksize=args.blocksize,
                             callback=callback) as stream:
            print(f"延迟:   {stream.latency * 1000:.1f} ms   [Ctrl+C 停止]")
            last_report = 0.0
            while not stop.is_set():
                time.sleep(0.2)
                el = time.time() - t_start
                if args.duration and el >= args.duration:
                    break
                if el - last_report >= 1.0:
                    last_report = el
                    print(f"  {el:6.1f}s  缓冲 {engine.buffered_frames():4d} 帧  "
                          f"underrun={stats.underrun + engine.underruns}  xrun={stats.xrun}")
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        engine.stop()
        if ssg is not None:
            ssg.close()
        if pcm is not None:
            pcm.close()
        core.close()

    played_s = nonlocal_played[0] / args.sr
    print(f"\n共输出 {played_s:.2f}s / {stats.underrun + engine.underruns} 次 underrun / "
          f"{stats.xrun} 次设备 xrun")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
