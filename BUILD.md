# BUILD —— 从源码到 EXE

面向 Windows 10/11 x64。实测环境：**Python 3.14.7 + gcc (w64devkit) + PyInstaller 6.22.3**。

> **仓库随带音色库，但不含鼓采样。** `.opm` 是作者自己转储/转换的参数数据，随仓库分发；
> `.wav` 鼓采样是录音制品，不随仓库也不随 EXE，需要用户自备。详见 [NOTICE.md](NOTICE.md)。

---

## 0. 两种运行方式

| 场景 | 命令 | 需要 |
|---|---|---|
| 直接跑源码 | `python app.py` | numpy、sounddevice |
| 打单文件 EXE | `.\build_exe.ps1` | 上面两个 + PyInstaller |

---

## 1. 从源码运行

```powershell
git clone <你的仓库> OPMMIDI-RT
cd OPMMIDI-RT
python -m pip install -r requirements.txt
python app.py
```

`requirements.txt` 只有两个真正的依赖：`numpy`、`sounddevice`
（`sounddevice` 自带 PortAudio，不需要单独装音频库）。

**此时渲染核心是现成的二进制**：仓库里有 `opm.dll`（Nuked OPM）和 `ssg.dll`（emu2149）。
不需要编译器就能跑。

命令行版另有 `rtplayer.py`（无界面）：

```powershell
python rtplayer.py <某个.mid> --bank <你的.opm> --ssg --pcm
```

---

## 2. 重新编译选音核心（可选）

只有改了 `core/opm.c` 或 `core/emu2149-main/emu2149.c` 才需要这步。
需要 **gcc**（[w64devkit](https://github.com/skeeto/w64devkit) 或 MSYS2 都行）。

```powershell
# OPM（Nuked OPM / LGPL-2.1）
gcc -O2 -shared -static -o opm.dll core\opm.c core\opm_wrapper.c

# SSG（emu2149 / MIT）
gcc -O2 -shared -static -o ssg.dll core\emu2149-main\emu2149.c core\emu2149-main\ssg_wrapper.c
```

编译参数说明：

- **`-O2` 比 `-O3` 快**。实测 Nuked OPM：`-O2` 3.50x 实时、`-O3` 3.20x、
  `-Ofast -march=native` 3.07x。逐时钟的标量代码，激进优化反而破坏缓存局部性。
- **`-static`** 是为了不依赖 MinGW 运行时 DLL，否则 EXE 分发时还要带 `libgcc_s_seh-1.dll`。

---

## 3. 打包成单文件 EXE

```powershell
python -m pip install pyinstaller
.\build_exe.ps1                    # 窗口版
.\build_exe.ps1 -Console           # 带控制台，排错用
.\build_exe.ps1 -Name MyPlayer     # 改名字
```

产物：`dist\OPMMIDI-RT.exe`，约 **24.7 MB**。

### 三个必须做对的参数

打包脚本里这三行不是可选项，去掉任何一个 EXE 都会在运行期炸：

```powershell
--add-binary "opm.dll;."          # 原生核心；--onefile 会解到 _MEIPASS
--add-binary "ssg.dll;."          # core.py / ssg_core.py 靠 __file__ 找到它
--collect-data sounddevice        # PortAudio 的 DLL 在 sounddevice 包里，不 collect 就漏
```

其余参数：

- `--onefile` 单文件（启动时解压到临时目录，首次启动约 1–2 s）
- `--windowed` 不弹控制台（排错时换 `--console`）
- `--add-data "*.opm;."` 附带音色库——**发布版建议去掉这行**，见 NOTICE.md

### 不需要改代码的地方

`core.py` 用 `os.path.dirname(os.path.abspath(__file__))` 找 DLL。
PyInstaller 的 `--onefile` 会把 `--add-binary` 的文件解到 `_MEIPASS`，
而冻结模块的 `__file__` 正好指向那里——**所以路径逻辑原样可用**。

配置文件和采样目录则用 `sys.executable` 的目录（即 EXE 旁边），两者在
`player_session.py` 的 `app_dir()` / `work_dir()` 里分开处理。

---

## 4. 数据文件怎么准备

### 音色库（`.opm`）

放到 EXE 旁边就会出现在下拉框里（程序同时搜索 EXE 目录和打包内部目录）。
没有的话界面能开，但选不到音色。

### 鼓采样（`.wav`）

**仓库和 EXE 都不带。** 需要有的话自己准备一个目录：

```
drum_samples\
    M01 01 Kick 1.wav
    M01 04 Snare 1.wav
    M01 11 Closed HH1.wav
    ...
```

- 文件名里要含关键字（`Kick 1`、`Snare 2`、`Closed HH`、`Crash`、`Ride`、`Conga` …），
  程序按关键字映射到 GM 打击乐音符，映射表在 `pcm_core.py` 的 `PCM_DRUM_MAP`
- 支持任意采样率：加载时会重采样到 55930 Hz
- 界面上「PCM 目录」指向它即可；找不到就自动退回 SSG/FM 鼓

---

## 5. 验证

```powershell
# 单测：实时引擎的 12 项自检（不开声卡）
python tool\rt_selftest.py --midi <某个.mid> --limit 8

# 冒烟：核心能不能出声，输出 smoke.wav
python tool\smoke_test.py

# EXE 自检：结果写进 dist\selfcheck.log
& .\dist\OPMMIDI-RT.exe --selfcheck
```

`--selfcheck` 会检查：冻结状态、DLL 位置、OPM/SSG 核心创建、PCM 采样数、声卡数量。

---

## 6. 排错

| 现象 | 原因 / 处理 |
|---|---|
| `FileNotFoundError: 找不到 opm.dll` | 没加 `--add-binary`，或从源码跑时 DLL 不在脚本目录 |
| EXE 能开窗，一按播放就崩 | 漏了 `--collect-data sounddevice` |
| `ImportError: No module named tkinter` | Python 安装时没勾 tcl/tk，重装并勾选 |
| 播放有爆音、`underrun` 一直涨 | 把 `blocksize` 从 256 加到 512；关掉占用 CPU 的程序 |
| 没有任何声音，但没报错 | 芯片选了 `opz` 会屏蔽 LFO；或音色库是空的；先用 `--list-devices` 确认设备 |
| 杀软报毒 / SmartScreen 拦截 | PyInstaller 单文件的常见误报；可加签名，或改用 `--onedir` |
| 编译出的 DLL 加载失败 | 少了 `-static`，缺 MinGW 运行时 DLL |

---

## 7. 目录结构

```
app.py               tkinter 界面入口
rtplayer.py          命令行播放器
player.py            离线渲染 MIDI -> WAV（参考实现，也是回归基准）
player_session.py    一次播放的组装（核心+音色+鼓+引擎），GUI 与 EXE 共用
rt_engine.py         实时引擎：采样级事件调度 / 重采样 / 生产者线程 / 混音
core.py              OPM 的 ctypes 绑定
ssg_core.py          SSG(YM2149) 的 ctypes 绑定
pcm_core.py          PCM 采样鼓
driver.py            OPM 寄存器驱动（通道分配、音色加载、鼓路由）
voice.py             .opm 文本解析
smf.py               MIDI 解析
translator.py        OPN -> OPM 参数翻译
core/                Nuked OPM 源码（LGPL-2.1）
core/emu2149-main/   emu2149 源码（MIT）
tool/                体检、审计、组装工具（见 README）
build_exe.ps1        一键打包
```
