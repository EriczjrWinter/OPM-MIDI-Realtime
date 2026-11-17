# make_public_copy.ps1
# 生成一份"可以直接公开"的仓库副本：只含代码、文档、编译产物，剔除第三方数据。
#
#   .\tool\make_public_copy.ps1                     # 输出到 ..\OPMMIDI-RT-public
#   .\tool\make_public_copy.ps1 -Out D:\tmp\pub      # 指定目录
#   .\tool\make_public_copy.ps1 -NoBanks            # 连 .opm 也不带
#
# 剔除内容：*.wav（鼓采样，录音版权）、dist/ build/ __pycache__/
# 保留内容：全部源码、core/ 下的 C 源码、opm.dll/ssg.dll（源码可重编）、工具、文档
#          以及 .opm 音色库（作者自行转储/转换的产物，见 NOTICE.md）
param(
    [string]$Out = (Join-Path (Split-Path $PSScriptRoot -Parent) "..\OPMMIDI-RT-public"),
    [switch]$NoBanks
)

$ErrorActionPreference = "Stop"
$src = Split-Path $PSScriptRoot -Parent
Set-Location $src

if (-not (Test-Path ".git")) { throw "这里不是 git 仓库：$src" }

$Out = [System.IO.Path]::GetFullPath($Out)
if (Test-Path $Out) {
    Write-Host "目标已存在，先删除：$Out" -ForegroundColor Yellow
    Remove-Item $Out -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Out | Out-Null

# 只复制被 git 跟踪的文件，天然排除 __pycache__ / dist / build
$files = & git ls-files
$skipped = @()
$copied = 0

foreach ($f in $files) {
    if ($f -like "*.wav") { $skipped += $f; continue }
    if ($NoBanks -and $f -like "*.opm") { $skipped += $f; continue }
    $dst = Join-Path $Out $f
    $dir = Split-Path $dst -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Copy-Item -LiteralPath $f -Destination $dst -Force
    $copied++
}

# 附一份"数据文件请自备"的说明
$bankNote = @'
# 音色库

仓库随带几个 `.opm` 音色库，都是本项目作者自己转储/转换的产物：

- `NECOPN_GM.opm` —— 从逆向还原的 `NECOPN.DRV` 中动态提取
  （<https://github.com/EriczjrWinter/necopn-drv-reverse>）
- `FB01_GM_v7.opm` / `FB01_GM_v4.opm` —— 从自建 OPM 音色池按 GM 槽位检索组装
  （FB-01 ROM 导出 + TX81Z + `mdx2opm` 转换的 X68K 曲目）

`.opm` 是纯参数文本（寄存器值），不含录音。底层音色数据的权利归原厂所有，
本项目仅用于个人学习与复古技术研究。

## 换成自己的音色库

把任意 `.opm` 放到 **EXE / app.py 所在目录**，程序启动时会自动扫描。

`.opm` 是 VOPM 的文本音色格式：

```
@:0 GM_Piano_1
LFO: 0 0 0 0 0
CH: 64 0 4 0 0 120 0
M1: 31 8 4 4 0 12 0 1 0 0
C1: 31 8 4 4 0 20 0 1 0 0
M2: 0 0 0 0 0 127 0 1 0 0
C2: 31 8 4 4 0 0 0 1 0 0
```

首行 `@:N 名字` 里的 N 就是 MIDI 程序号（0–127 对应 GM 的 1–128）。

其他来源举例：

- Yamaha FB-01 的 96 个 ROM 音色（社区有人整理成 .opm）
- VOPM 官方示例库
- 用 `mdx2opm`（<https://github.com/vampirefrog/mdxtools>）转换自己的 `.mdx` 收藏
  —— 注意它会**把整首曲子的全局 LFO 抄进每个音色**，导入前记得把
  `LFO:` 行改成 `0 0 0 0 0`，否则每个音都带满档颤音
- 自己用 OPM 编辑器手搓

## 鼓采样

`drum_samples/` **不随仓库分发**（Korg M1 采样是录音制品，受版权保护）。
需要 PCM 鼓时，自己在 EXE 旁边放一个 `drum_samples/` 目录，文件名含
`Kick 1` / `Snare 2` / `Closed HH` / `Crash` / `Ride` 等关键字即可，
映射规则见 `pcm_core.py` 的 `PCM_DRUM_MAP`。

没有采样也不影响使用：鼓会自动走 SSG（YM2149）或 FM 的合成鼓。
'@
$bankDir = Join-Path $Out "banks"
New-Item -ItemType Directory -Force -Path $bankDir | Out-Null
Set-Content -LiteralPath (Join-Path $bankDir "README.md") -Value $bankNote -Encoding utf8

Write-Host ""
Write-Host "复制 $copied 个文件 → $Out" -ForegroundColor Green
if ($skipped.Count -gt 0) {
    Write-Host "剔除 $($skipped.Count) 个数据文件：" -ForegroundColor Yellow
    $skipped | Select-Object -First 12 | ForEach-Object { Write-Host "  - $_" }
    if ($skipped.Count -gt 12) { Write-Host "  …还有 $($skipped.Count - 12) 个" }
}
Write-Host ""
Write-Host "下一步（首次公开）：" -ForegroundColor Cyan
Write-Host "  cd `"$Out`""
Write-Host "  git init; git add -A; git commit -m `"initial`""
Write-Host "  git remote add origin <你的 GitHub 仓库>; git push -u origin master"
