# NOTICE —— 第三方组件与数据文件

这个仓库里的**代码**和**数据**是两回事，发布前必须分开看。

---

## 代码：第三方组件

| 组件 | 位置 | 许可证 | 说明 |
|---|---|---|---|
| **Nuked OPM** | `core/opm.c` `core/opm.h` | **LGPL-2.1** | Copyright (C) 2020, 2026 Nuke.YKT。基于 YM2151 晶片照片的精确仿真 |
| **emu2149** | `core/emu2149-main/` | **MIT** | Copyright (c) 2014 Mitsutaka Okazaki。YM2149/AY-3-8910 |
| `opm_wrapper.c` `ssg_wrapper.c` | `core/` | 本项目 | 裸 C 包装层，导出 create/write/generate |
| **numpy** | 依赖 | BSD-3-Clause | |
| **sounddevice** + PortAudio | 依赖 | MIT | PortAudio 的 MIT 式许可证 |
| **PyInstaller** | 构建工具 | GPL-2.0 **+ 例外条款** | 例外条款允许分发用它打包的**闭源**程序；打包产物本身不因此变 GPL |
| **tkinter** | Python 标准库 | PSF | |

### LGPL 的实际义务（Nuked OPM）

证书要求：

1. **随分发提供源码** —— `core/opm.c` / `opm.h` 必须在仓库里（已满足）；
2. **允许用户替换该库** —— 动态链接（我们的 `opm.dll`）本身满足这一点；
3. **保留版权与许可声明** —— 源文件头部的 LGPL 声明不要删。

建议在仓库根目录放一份 `LICENSE.LGPL-2.1` 全文，并在 README 里写明
「本项目使用 Nuked OPM（LGPL-2.1），见 core/opm.c」。

> 注意：LGPL 只约束 `opm.c/opm.h` 这一部分。你自己的 Python 代码可以选择任意许可证，
> 但如果你把 `opm.c` **静态**编译进同一个可执行文件，LGPL 的传染性讨论就会变复杂——
> 现在用独立 `opm.dll` 动态加载，是最省事的做法，保持这样即可。

---

## 数据文件

| 数据 | 来源 | 是否随仓库分发 |
|---|---|---|
| `NECOPN_GM.opm` | **本项目作者自行从逆向还原的 `NECOPN.DRV` 中动态提取**（[necopn-drv-reverse](https://github.com/EriczjrWinter/necopn-drv-reverse)） | ✅ 是 |
| `FB01_GM*.opm` | 作者从自建 OPM 音色池（FB-01 ROM 导出 + TX81Z + `mdx2opm` 转换的 X68K 曲目）按槽位检索组装 | ✅ 是 |
| `xg.opm` / `FMTowns.opm` / `mp_fm.opm` / `fmmidi.opm` | 社区流传的 OPM 预设库，作者自行整理 | ✅ 是 |
| `drum_samples/*.wav` | Korg M1 鼓组**录音采样** | ❌ **否** |

### `.opm` 音色库：可以分发

理由：

1. **`.opm` 是社区文本格式**，不是音频数据——文件里只有寄存器参数（AR/D1R/TL/算法…），
   不含任何录音。OPM 生态里这类文件长期公开流转。
2. **这些库是作者自己转储/转换的产物**，不是从别处拷贝的发行物：
   `NECOPN_GM.opm` 来自对 `NECOPN.DRV` 的逆向提取，`FB01_GM*` 来自自建音色池的筛选，
   X68K 那部分用 `mdx2opm` 从自己的 `.mdx` 收藏转换。
3. 若涉及底层音色数据的权利归属，按原厂所有处理；本项目仅用于个人学习与
   复古技术研究，权利方如有异议将移除相关文件。

### `.wav` 鼓采样：不分发

Korg M1 采样是**真实录音**，属明确的录音制品版权，与 `.opm` 的参数数据性质不同。
所以：

- 仓库 `.gitignore` 里的 `*.wav` 从第一天就生效，**41 个采样从未进入 git 历史**
- EXE 打包时不含任何采样，只随包 `.opm`
- 采样目录由用户自己指定，缺失时自动退回 SSG / FM 合成鼓，不会报错

同样的道理适用于 OPNA 的 RHY 采样（`2608_BD.WAV` 等，Yamaha 版权）——
需要的话请自备。

### 发布检查表

```powershell
git ls-files | Select-String '\.wav$'    # 必须是 0
git ls-files | Select-String '\.opm$'    # 可以有
```

生成干净副本（保留 `.opm`、剔除 `.wav`）：

```powershell
.\tool\make_public_copy.ps1 -Out ..\OPMMIDI-RT-public
```

如果哪天要连 `.opm` 一起剔除，加 `-NoBanks`。

---

## 本项目自身的许可证

**MIT** —— 见根目录 [LICENSE](LICENSE)。

要点：

- MIT 不约束第三方组件；`core/opm.c` 仍是 LGPL-2.1、`core/emu2149-main/` 仍是 MIT，
  各自保留原始声明。
- `opm.dll` 是**动态加载**的独立模块，因此本项目代码不构成 LGPL 衍生作品。
- 数据文件（`.opm` 音色库）不在 MIT 覆盖范围内，权利归原厂，仅用于学习研究。

如需沿用主项目的口径，可在 README 里保留这句：

> 本项目仅供个人学习、复古技术研究与交流，所有权利归各自所有者所有。
