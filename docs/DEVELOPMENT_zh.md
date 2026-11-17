# OPMMIDI-RT

实时播放器实验分支。上游是 `D:\music\OPMMIDI-GAI3(OPZ＆OPP)`（离线 MIDI → WAV 渲染器）。

**上游目录不动，所有实验只在这里改。**

## 现状

| 部分 | 状态 |
|---|---|
| Nuked OPM 核心（`core/` + `opm.dll`） | 原样复制，未修改 |
| 离线渲染（`player.py`） | 原样复制，作为**参考实现 / 回归基准**保留 |
| 实时播放器 | 未开始 |
| 可选核心（ymfm，为真 OPZ） | 规划中 |

`player.py` 不要删——实时路径要拿它的输出做逐秒 RMS 对比（`tool/rms_diff.py`）。

## 目录

```
core/            Nuked OPM 源码（opm.c / opm.h / opm_wrapper.c）
core.py          ChipCore 抽象 + OpmCore（ctypes 绑定）+ NullCore
driver.py        OpmSystem：通道分配、音色加载、TL/音高翻译、鼓组
smf.py           SMF type 0/1/2 解析（输出以秒为单位的事件表）
voice.py         .opm 音色库解析
translator.py    MIDI 音高 / 包络 / 反馈 → OPM 寄存器参数
player.py        离线渲染 CLI（参考实现）
tool/rms_diff.py 两个 WAV 的逐秒 RMS 对比
docs/            上游 README 存档
*.opm            音色库（NECOPN_GM 为默认；FB01_GM 对应 OPP/FB-01）
opm.dll          预编译核心（gcc 构建，55930 Hz = 3.579545 MHz / 64）
```

## 验证拷贝是否完好

```powershell
python player.py <某个.mid> -o out.wav
```

应与上游逐字节一致（已用 `5.mid` 验证：`peak=11296/32767`，WAV 哈希相同）。

## 构建与发布

| 文档 | 内容 |
|---|---|
| [BUILD.md](BUILD.md) | 从源码运行 / 重编 OPM·SSG 核心 / 打包单文件 EXE / 排错表 |
| [NOTICE.md](NOTICE.md) | 第三方组件许可证（LGPL-2.1 的 Nuked OPM、MIT 的 emu2149）+ 数据文件说明 |

**仓库随带 `.opm` 音色库**（作者自行从 `NECOPN.DRV` 提取、及从自建音色池筛选的产物），
但**不含鼓采样（`.wav`）**——那是录音制品，受版权保护。程序在采样目录缺失时会
自动退回 SSG/FM 合成鼓，不会崩。

生成一份可直接公开的干净副本（剔除数据文件、保留源码与 DLL）：

```powershell
.\tool\make_public_copy.ps1 -Out ..\OPMMIDI-RT-public
```

## 实测基线（本机）

- 核心吞吐：**3.15x 实时**（317 ms CPU / 音频秒，约 1/3 个核心）
- `gcc -O2` 重编：3.50x；再砍掉 TimerA/B + IRQ + CSM：**4.10x**
- `-O3` / `-Ofast -march=native` 反而更慢
- Python 边界（struct/numpy 转换）开销 **0–2%**，不是瓶颈

结论：实时播放（需要 1.0x）余量充足，**不换核心**。优化顺序是
预渲染 > 砍 timer + `-O2` > 加 ymfm（可选）> 换 HLE（不推荐，会丢掉 OPP/OPZ 精度）。

## 实时播放器（v0 已可用）

```powershell
python rtplayer.py D:\music\5.mid
python rtplayer.py D:\music\5.mid --duration 5 --blocksize 128
python rtplayer.py --list-devices
```

本机实测（Realtek / WASAPI 共享，block=256）：

- 延迟 **22.0 ms**，5 秒播放 **0 underrun / 0 xrun**
- 生产者吞吐 **2.19x 实时**（已含重采样）
- 事件落点 **310/310 精确到采样点**；旧的块量化平均迟到 4.49 ms、最差 9.05 ms

分层：

- `rt_engine.py` —— 与设备无关。`Renderer`（采样级事件调度）、`Resampler`
  （55 930 → 48 000，4 点 Catmull-Rom）、`Engine`（生产者线程 + 环形缓冲）
- `rtplayer.py` —— 只负责开声卡、喂回调、打统计
- `tool/rt_selftest.py` —— 脱机自检，不开声卡

自检：

```powershell
python tool/rt_selftest.py --midi D:\music\5.mid --limit 8
```

七项检查：quantize 模式与 `player.py` **逐字节一致**、事件落点精确、
块大小无关性、重采样长度与分块无关性、生产者吞吐、输出长度。

## SSG 打击乐（`--ssg`）

移植自 `OPMMIDI-GAI2(SSG)`。emu2149 @ 1 789 772 Hz 分频输出 **55 930 Hz**——和 OPM 同采样率，
混音时不需要重采样。底鼓 → Tone A，军鼓 → Tone B(+Noise)，踩镲 → Noise C，
衰减靠 YM2149 硬件包络（R11=0 / R12=4 / R13=0）。这三类鼓完全不占用 OPM 通道。

```powershell
.\play.cmd "xxx.mid" --ssg                  # GAI2 原配方
.\play.cmd "xxx.mid" --ssg --ssg-clean      # 修正后的混音位值
.\play.cmd "xxx.mid" --ssg --ssg-gain 3.0   # 调混音增益（默认 2.0）
```

实测（`【灵长新益京】...FM.mid` 前 15 s）：132 击打击乐中 **126 击走 SSG（95%）**，
峰值 12288 → 18752，RMS 2066 → 2586，无削波。SSG 核心本身只花 **7.5 ms / 音频秒**（OPM 的 0.1%）。

### 顺带发现的一个 bug

GAI2 那三个 R7（混音开关）位值是错的。AY 的 R7 是**低有效**（1 = 关闭该声源），
原配方 `0x01 / 0x12 / 0x20` 的实际效果是：

| 鼓 | GAI2 写的 | 实际发声 | 频谱平坦度 |
|---|---|---|---|
| 底鼓 | 0x01 | 噪声（Tone A 的噪声位） | 0.65 |
| 军鼓 | 0x12 | B 的 tone+noise 全关 → **直流包络** | 0.06 |
| 踩镲 | 0x20 | C 的 **tone**，噪声被关 → 13.9 kHz 单音 | 0.07 |

它能听，是因为音量和包络被耳朵调准了。`--ssg-clean` 用正确位值（`0x3E / 0x2D / 0x1B`），
军鼓和踩镲变成真正的噪声（平坦度 0.53 / 0.82）。**默认仍是 legacy**，保持与你 GAI2 一致。

## PCM 采样鼓（`--pcm`）

移植自 `OPMMIDI-GAI(PCM)`，重写成向量化（GAI 原版是逐采样 Python 双循环，实时跑不动）。
硬件原型是 Sharp X68000 的 MSM6258（4-bit ADPCM）——但**不做单声道阉割**：
X68K 自己就用 68k 定时器中断软混来加 PCM 声道，我们保持 8 复音，只留它的音色性格。

```powershell
.\play.cmd "xxx.mid" --pcm                    # 采样鼓
.\play.cmd "xxx.mid" --pcm --pcm-lofi         # 12-bit 降比特 + 低通（X68K 味道）
.\play.cmd "xxx.mid" --pcm --pcm-voices 16    # 放宽复音
.\play.cmd "xxx.mid" --ssg --pcm              # SSG 拿 7 个核心鼓，PCM 接剩下的
```

- `drum_samples/` 里 **26 个 wav → 覆盖全部 39 个 GM 打击乐音符**（一个采样注册到多个别名音符：
  Crash 同时覆盖 49/52/55/57）
- 关键字匹配改成**去空格 + 长度降序**，否则文件名 `Closed HH2` 永远抢不到 44 号音
- lo-fi 处理放在**加载时**做一次（降比特用 `floor(x/16)*16`，低通截断成 32 抽头 FIR），不占实时预算
- 增益实测：`1.0` 会让总线顶到满幅（0.064% 样本削波），**默认 0.5** → 峰值 24064，零削波
- PCM 接管打击乐后，`_allocate` 把第 8 个 OPM 通道也还给旋律

四种鼓配置实测（`【灵长新益京】..._FM.mid` 前 15 s）：

| 配置 | peak | RMS | 渲染 |
|---|---|---|---|
| 仅 OPM | 12288 | 2066 | 2.95x |
| + PCM | 24064 | 3677 | 2.96x |
| + SSG | 18349 | 2436 | 2.92x |
| + SSG + PCM | 20478 | 2534 | 2.90x |

## 怎么换音色

三层机制，从粗到细：

```powershell
.\play.cmd "xxx.mid"                              # 跟随 MIDI 自带的 program change
.\play.cmd "xxx.mid" --bank xg.opm                 # 整个库换掉
.\play.cmd "xxx.mid" --voice 0                     # 锁死单音色（下标）
.\play.cmd "xxx.mid" --voice GrandPiano            # 锁死单音色（名字，支持唯一子串）
.\play.cmd --bank xg.opm --list-voices             # 看清单
```

**MIDI 程序号 = 库里第 N 条**（`driver.py` 里就是 `programs[ch] % len(voices)`）。
这批库的前 128 条都是 GM 顺序，所以跟标准 MIDI 对得上：

| 库 | 音色数 | 第 0 条 |
|---|---|---|
| NECOPN_GM.opm | 128 | Acoustic_Grand_Piano |
| FB01_GM.opm | 128 | GM_Piano_1 |
| xg.opm | 810 | * GrandPiano |
| FMTowns.opm | 214 | A.Piano1 |
| mp_fm.opm | 256 | Trumpet |
| fmmidi.opm | 159 | (无名) |

已知限制：

- **超过 128 号的音色 MIDI 够不着** —— program change 只有 0–127。想用只能用 `--voice`。
- **鼓组不走音色库** —— 打击乐是 `driver.py` 里 `DRUM_BANK` 硬编码的（35–81 号音符），
  换 `--bank` 不影响鼓。这些库 128 号以后其实是鼓/打击乐内容，目前没接进来。
- `fmmidi.opm` 全部无名，只能按下标选。

### 两个踩过的坑

1. **设备只认 48 000。** 本机 Realtek 上 55 930 Hz 被 WASAPI / WDM-KS 全部拒绝；
   MME 那个"接受"是它内部重采样换来的（延迟上百毫秒）。所以必须自己重采样。
2. **EOF 处不要用"缓冲为空"判断结束。** v0 第一版写的是
   `if finished and buffered==0: break`，撞上生产者/消费者竞态，消费者
   无限 `append` 零块，把内存吃光（连 DSH 一起 OOM 了）。现在结束条件
   一律按**帧数**（`emitted >= total_out`），生产者 EOF 后直接退出线程。

## 音色库构建流水线

**背景**：FB-01 是 1985 年的机器，GM 是 1991 年才定的标准——**不存在"官方 FB-01 GM 库"**。
`FB01_GM.opm` 这类产物都是"拿 OPM 血统的音色凑 128 个槽位"。所以定位是
**社区 GM 库**（像 FMMIDI 那样以作曲家巧思为主），不是严格合规的 GM。

池子规模：`D:\gf\OPM FB01BANK` 下 2313 个 `.opm`、14957 个音色块、去重后 **10068 个**。

四个工具，按顺序用：

```powershell
# 1. 盘点：血统分层 + 每个槽位有多少名字命中的候选
python tool/pool_scan.py --pool "D:\gf\OPM FB01BANK"

# 2. 组装：槽位级名字检索 → 血统权重 → 指纹去重 → 源头配额 → 硬性否决 → 包络兜底
python tool/build_gm_from_pool.py --pool "D:\gf\OPM FB01BANK" --out FB01_GM_v7.opm `
    --allow "FB01(OPP),TX81Z(OPZ),X68K(MDX),街机OST(OPM)" --max-per-src 1

# 3. 参数体检（重复槽位 / 静音载波 / 无效包络）
python tool/bank_lint.py --bank FB01_GM_v7.opm

# 4. 声学审计（渲染每个音色，量起音/0.55s 余量/亮度，对照 GM 家族预期）
python tool/bank_audit.py --bank FB01_GM_v7.opm --ref NECOPN_GM.opm

# 5. 列出"凑合填补"的槽位（名字没命中、靠包络兜底的）
python tool/report_gaps.py --bank-report FB01_GM_v7_report.csv
```

### 血统分层

| 层 | 来源 | 数量 | 说明 |
|---|---|---|---|
| `FB01(OPP)` | FB-01 ROM + 63 族音色库 | 2367 | 最正统（YM2164） |
| `TX81Z(OPZ)` | TX81Z 音色 | 1391 | YM2414，同系 |
| `X68K(MDX)` | `mdx2opm` 转的 X68000 曲目 | 6732 | 真 OPM，但曲目专用 |
| `街机OST(OPM)` | 街机/主机 OST dump | 4547 | 真 OPM，曲目专用 |
| `MDX(OPN)` / 其他 | MXDRV 标准音色等 | — | OPN 血统，默认不进 |

**MDX 的坑**：`.mdx` 里 LFO 是全局参数，`mdx2opm` 把它抄进了每个音色块
（实测 6732/6732 都是 `LFO: 128 64 64`）。生成器导入时会归一化成 `0 0 0 0 0`，
否则每个音都带满档颤音。

### 三个版本的成绩

| | 旧 `FB01_GM` | v4（纯血 OPP+OPZ） | v7（MDX 参战） | 对照 `NECOPN_GM` |
|---|---|---|---|---|
| 声学不符 | 42/128 | 24 | 23 | 14 |
| 参数问题 | 96 | 33 | — | — |
| 重复槽位 | 5 组 | 0 | 0 | 0 |
| 血统 | 混入 41% 游戏 dump | FB01 83 + TX81Z 45 | +X68K 18 +街机 1 | OPNA |

### 已知弱项（社区库的正常代价）

- **凑合填补 37 个槽位**（`report_gaps.py` 有全表），其中高风险 10 个：
  `108 Kalimba←fiddle1`、`110 Fiddle←sitar1`、`115 Woodblock←tom`、`116 Taiko←clav`、
  以及 SFX `120/124/125/126/127`。
- **包络兜底能保证"行为像"，保证不了"身份对"**——Celesta 会被塞进不会衰减的音色。
- 剩下 23 个声学不符大多是**家族行为**问题（风琴不持续、合奏起音慢），
  属于"该改参数而不是该换音色"，需要一次家族包络后处理。

## 路线图

- [x] 音频后端（`sounddevice` + `numpy`）+ 生产者线程 + 环形缓冲
- [x] 55 930 → 48 000 Hz 重采样（时钟不动，保音高）
- [x] 事件从"秒"改成"整数帧号"，块内切分，采样级精度
- [x] SSG（YM2149）打击乐：底鼓 / 军鼓 / 踩镲
- [x] PCM 采样鼓（X68K / MSM6258 致敬，8 复音）
- [ ] 旋律 SSG 声部（`_SSG` MIDI 里 ch7–9 那三个方波声部）
- [ ] 传输控制：暂停/恢复；跳转用"状态快进 + 重 key-on"近似
- [ ] `build.ps1`：`-O2` + 去掉 TimerA/B/IRQ/CSM（实测 3.50x → 4.10x）
- [ ] 可选 ymfm 核心（为真 OPZ / 低配机省电档）
