# -*- coding: utf-8 -*-
"""实时引擎脱机自检 —— 不开声卡。

四项检查：
  1. quantize 模式下与 player.py 的离线渲染**逐字节一致**（证明接线没接错）
  2. 采样级模式下，**块大小不影响输出**（这是实时播放的正确性核心）
  3. 采样级 vs 块量化：差多少（量化到底损失了什么）
  4. 重采样器：长度比例正确、且分块方式不影响输出

用法:
    python tool/rt_selftest.py --midi D:\\music\\5.mid --limit 12
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import player as offline
from core import OpmCore
from driver import OpmSystem
from rt_engine import Engine, Renderer, Resampler
from smf import parse_smf
from voice import load_opm_text

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANK = os.path.join(HERE, "NECOPN_GM.opm")

_ok = True


def check(name, cond, detail=""):
    global _ok
    mark = "OK  " if cond else "FAIL"
    if not cond:
        _ok = False
    print(f"[{mark}] {name}{'  ' + detail if detail else ''}")


def build(events, total, tail=1.0, clock=3_579_545, chip="opm"):
    flags = 1 if chip == "opp" else 0
    core = OpmCore(clock=clock, flags=flags)
    system = OpmSystem(core, chip_mode=chip)
    system.load_bank(load_opm_text(BANK))
    return core, system


def render_blocks(renderer, block):
    out = []
    while not renderer.done:
        out.append(renderer.render(block))
    # render() 总是返回整块（末尾补零给音频回调用），这里截掉补零部分
    return np.concatenate(out)[:renderer.total_frames]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--midi", required=True)
    ap.add_argument("--limit", type=float, default=12.0, help="只测前 N 秒")
    ap.add_argument("--chip", default="opm", choices=["opm", "opp", "opz"])
    args = ap.parse_args(argv)

    events, tpq, total = parse_smf(open(args.midi, "rb").read())
    events = [e for e in events if e.t <= args.limit]
    total = min(total, args.limit)
    sr = 3_579_545 // 64
    print(f"MIDI {os.path.basename(args.midi)}: 截取前 {args.limit}s, {len(events)} 事件, "
          f"芯片 {args.chip}, SR={sr}\n")

    # ---- 1. 离线基准 ----
    core, system = build(events, total, chip=args.chip)
    pcm_off = offline.render(system, core, events, total, sr, tail=1.0)
    core.close()
    print(f"离线基准: {len(pcm_off)//4} 帧 = {len(pcm_off)/4/sr:.2f}s")

    # ---- 2. quantize 模式逐字节对比 ----
    core, system = build(events, total, chip=args.chip)
    r = Renderer(core, system, events, total, tail=1.0, quantize=True)
    pcm_q = render_blocks(r, 512).tobytes()
    core.close()
    check("quantize 模式与 player.py 逐字节一致", pcm_q == pcm_off,
          f"({len(pcm_q)} vs {len(pcm_off)} bytes)")

    # ---- 2b. 事件落点精度 ----
    # 直接看派发时的帧号，比从波形猜起点可靠
    core, system = build(events, total, chip=args.chip)
    r = Renderer(core, system, events, total, tail=1.0, quantize=False)
    render_blocks(r, 512)
    core.close()
    expect_frames = [int(round(e.t * sr)) for e in events]
    got = [f for f, _k in r.dispatch_log]
    exact = sum(1 for a, b in zip(got, expect_frames) if a == b)
    check("采样级：每个事件都落在目标采样点", exact == len(expect_frames),
          f"{exact}/{len(expect_frames)} 精确")

    core, system = build(events, total, chip=args.chip)
    rq = Renderer(core, system, events, total, tail=1.0, quantize=True)
    render_blocks(rq, 512)
    core.close()
    late = [f - int(round(e.t * sr)) for (f, _k), e in zip(rq.dispatch_log, events)]
    if late:
        print(f"块量化: 平均迟到 {sum(late)/len(late)/sr*1000:.2f} ms, "
              f"最大 {max(late)/sr*1000:.2f} ms")

    # ---- 3. 采样级：块大小无关性 ----
    results = {}
    for bs in (137, 512, 1000):
        core, system = build(events, total, chip=args.chip)
        r = Renderer(core, system, events, total, tail=1.0, quantize=False)
        results[bs] = render_blocks(r, bs)
        core.close()
    same = all(np.array_equal(results[137], results[b]) for b in (512, 1000))
    check("采样级输出与块大小无关", same,
          "" if same else f"137:{results[137].shape} 512:{results[512].shape}")

    # ---- 4. 采样级 vs 块量化 差异 ----
    a = results[512].reshape(-1).astype(np.float64)
    b = np.frombuffer(pcm_off, dtype="<i2").reshape(-1).astype(np.float64)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    rms = lambda x: float(np.sqrt(np.mean(x ** 2))) or 1.0
    rms_diff = float(np.sqrt(np.mean((a - b) ** 2)))
    print(f"采样级 vs 块量化: 逐点 RMS 差 {rms_diff:.1f} "
          f"(占信号 RMS {rms(b):.1f} 的 {rms_diff/rms(b)*100:.1f}%)")

    # ---- 5. 重采样器 ----
    sr_out = 48000
    rs = Resampler(sr, sr_out)
    out = [rs.process(results[512][i:i + 512]) for i in range(0, len(results[512]), 512)]
    rs_out = np.concatenate([o for o in out if o.shape[0]])
    expect = len(results[512]) * sr_out / sr
    check("重采样输出长度正确",
          abs(rs_out.shape[0] - expect) <= 2,
          f"得到 {rs_out.shape[0]}, 期望 {expect:.1f}")

    # 分块方式无关性
    rs2 = Resampler(sr, sr_out)
    out2 = [rs2.process(results[512][i:i + 300]) for i in range(0, len(results[512]), 300)]
    rs2_out = np.concatenate([o for o in out2 if o.shape[0]])
    check("重采样输出与分块方式无关",
          rs2_out.shape == rs_out.shape and np.array_equal(rs2_out, rs_out),
          f"{rs2_out.shape} vs {rs_out.shape}")

    # ---- 6. Engine 端到端（无设备）----
    # 先测吞吐：只在不缺数据时才拉，避免"消费者比生产者快"制造假 underrun
    core, system = build(events, total, chip=args.chip)
    r = Renderer(core, system, events, total, tail=1.0)
    eng = Engine(r, sr_out=sr_out, blocksize=256)
    eng.start()

    drained = 0
    all_blocks = []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 1.0:
        if eng.buffered_frames() >= 1024:
            all_blocks.append(eng.pull(1024))
            drained += 1024
        else:
            time.sleep(0.001)
    el = time.perf_counter() - t0
    produced = drained + eng.buffered_frames()
    ratio = produced / sr_out / el
    check("生产者吞吐 >= 实时", ratio >= 1.0,
          f"{ratio:.2f}x 实时（1s 内产出 {produced/sr_out:.3f}s 音频）")

    # 再排空剩余部分
    max_blocks = int(eng.total_out / 256) + 200      # 硬上限，防止无限循环
    guard = 0
    while not eng.finished():
        all_blocks.append(eng.pull(256))
        guard += 1
        if guard > max_blocks:
            check("Engine 在帧数上限内结束", False, f"已拉 {guard} 块 > 上限，中止")
            eng.stop()
            core.close()
            sys.exit(1)
    eng.stop()
    core.close()
    pcm_rt = np.concatenate(all_blocks)
    check("Engine 输出长度正确",
          abs(len(pcm_rt) - len(results[512]) * sr_out / sr) <= 300,
          f"{len(pcm_rt)} 帧 = {len(pcm_rt)/sr_out:.2f}s")

    peak = int(np.abs(pcm_rt).max())
    print(f"\n重采样后峰值: {peak}/32767" + ("  ← 静音！" if peak == 0 else ""))

    # ---- 7. SSG 声源 ----
    ssg_dll = os.path.join(HERE, "ssg.dll")
    if not os.path.exists(ssg_dll):
        print("[SKIP] 没找到 ssg.dll，跳过 SSG 检查")
    else:
        from ssg_core import SsgCore
        from smf import Event

        # 造一段只有打击乐的事件：底鼓 / 军鼓 / 踩镲
        beats = [Event(t=0.10 * i, kind="on", ch=9, d1=note, d2=110)
                 for i, note in enumerate([35, 38, 42, 35, 38, 42])]

        def render_drums(use_ssg, recipe="clean", block=512):
            s = SsgCore() if use_ssg else None
            c, sy = build(beats, 0.8, chip=args.chip)
            sy.ssg, sy.ssg_drums, sy.ssg_recipe = s, use_ssg, recipe
            r = Renderer(c, sy, beats, 0.8, tail=0.0, ssg=s)
            out = render_blocks(r, block)
            # SSG 接管时不该占用任何 OPM 槽位
            opm_busy = sum(1 for slot in sy.slots if slot.active)
            c.close()
            if s is not None:
                s.close()
            return out, opm_busy

        dry, dry_busy = render_drums(False)
        check("不开 SSG 时打击乐走 FM 鼓通道", int(np.abs(dry).max()) > 0,
              f"peak={int(np.abs(dry).max())}")

        results_ssg = {}
        for recipe in ("legacy", "clean"):
            wet, busy = render_drums(True, recipe)
            results_ssg[recipe] = wet
            rms_ = float(np.sqrt(np.mean(wet.astype(np.float64) ** 2)))
            check(f"SSG({recipe}) 有输出", int(np.abs(wet).max()) > 0,
                  f"peak={int(np.abs(wet).max())} rms={rms_:.0f}")
            check(f"SSG({recipe}) 不占用 OPM 槽位", busy == 0, f"占用 {busy} 个")

        check("SSG 与 FM 鼓的输出不同",
              not np.array_equal(results_ssg["clean"], dry))

        a137, _ = render_drums(True, "clean", block=137)
        check("带 SSG 混音时输出仍与块大小无关",
              np.array_equal(a137, results_ssg["clean"]),
              f"{a137.shape} vs {results_ssg['clean'].shape}")

        print(f"SSG 段峰值: clean={int(np.abs(results_ssg['clean']).max())}  "
              f"legacy={int(np.abs(results_ssg['legacy']).max())}")

    # ---- 8. PCM 采样鼓 ----
    drum_dir = os.path.join(HERE, "drum_samples")
    if not os.path.isdir(drum_dir):
        print("[SKIP] 没找到 drum_samples/，跳过 PCM 检查")
    else:
        from pcm_core import PcmCore, load_drum_dir
        from smf import Event

        # 35/38 在 SSG 名单里，49/51 不在——正好验证两者共存
        hits = [Event(t=0.12 * i, kind="on", ch=9, d1=n, d2=110)
                for i, n in enumerate([35, 49, 38, 51, 35, 49])]

        def render_pcm(ssg_on, block=512, gain=0.5):
            s = SsgCore() if ssg_on else None
            c, sy = build(hits, 1.0, chip=args.chip)
            p = PcmCore(c.sample_rate(), max_voices=8, lofi=True)
            n_map = load_drum_dir(p, drum_dir)
            sy.pcm = p
            sy.ssg, sy.ssg_drums, sy.ssg_recipe = s, ssg_on, "legacy"
            r = Renderer(c, sy, hits, 1.0, tail=0.0, ssg=s, pcm=p, pcm_gain=gain)
            out = render_blocks(r, block)
            busy = sum(1 for slot in sy.slots if slot.active)
            c.close()
            p.close()
            if s is not None:
                s.close()
            return out, busy, n_map, p.steals

        pcm_out, pcm_busy, n_map, steals = render_pcm(False)
        check("PCM 采样覆盖 GM 打击乐", n_map >= 35, f"映射 {n_map} 个音符")
        check("PCM 鼓有输出", int(np.abs(pcm_out).max()) > 0,
              f"peak={int(np.abs(pcm_out).max())}")
        check("PCM 鼓不占用 OPM 槽位", pcm_busy == 0, f"占用 {pcm_busy} 个")
        check("PCM 未触发抢占（8 复音够用）", steals == 0, f"抢占 {steals} 次")

        both, busy_both, _, _ = render_pcm(True)
        check("SSG + PCM 同时工作且都不占 OPM 槽位", busy_both == 0,
              f"占用 {busy_both} 个")
        check("SSG + PCM 混合输出不同于单 PCM",
              not np.array_equal(both, pcm_out))

        pcm137, _, _, _ = render_pcm(False, block=137)
        check("带 PCM 混音时输出仍与块大小无关",
              np.array_equal(pcm137, pcm_out), f"{pcm137.shape} vs {pcm_out.shape}")

        print(f"PCM 段峰值: 单 PCM={int(np.abs(pcm_out).max())}  "
              f"SSG+PCM={int(np.abs(both).max())}")

    print("\n" + ("全部通过" if _ok else "有失败项"))
    return 0 if _ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
