# build_exe.ps1
# 打包成单文件 EXE。
#
#   .\build_exe.ps1                # 窗口版（默认，无控制台）
#   .\build_exe.ps1 -Console       # 带控制台，排错用
#   .\build_exe.ps1 -Name MyPlayer # 改名字
#
# 注意：**不会打包任何 PCM 鼓采样**（版权素材）。EXE 只带 .opm 音色库；
# 鼓采样需要用户自己在 EXE 旁边放 drum_samples/，或在界面上指定目录。
param(
    [string]$Name = "OPMMIDI-RT",
    [switch]$Console
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "PATH 里找不到 python"
}
python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "安装 PyInstaller…" -ForegroundColor Yellow
    python -m pip install -q pyinstaller
}

$mode = if ($Console) { "--console" } else { "--windowed" }

$piArgs = @(
    "--noconfirm", "--clean", "--onefile", $mode,
    "--name", $Name,
    # 两个原生核心：打包后会被解到 _MEIPASS，core.py/ssg_core.py 用 __file__ 就能找到
    "--add-binary", "opm.dll;.",
    "--add-binary", "ssg.dll;.",
    # PortAudio 的 DLL 在 sounddevice 包里，不 collect 会漏
    "--collect-data", "sounddevice",
    # 附带音色库（v7 默认，v4 备选）
    "--add-data", "FB01_GM_v7.opm;.",
    "--add-data", "FB01_GM_v4.opm;.",
    "--add-data", "NECOPN_GM.opm;.",
    "app.py"
)

Write-Host "构建 $Name ($mode)…" -ForegroundColor Cyan
python -m PyInstaller @piArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }

$exe = Join-Path $PSScriptRoot "dist\$Name.exe"
if (Test-Path $exe) {
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "完成: $exe ($mb MB)" -ForegroundColor Green
    Write-Host "自检: & '$exe' --selfcheck   → 结果写进 dist\selfcheck.log" -ForegroundColor Green
} else {
    throw "没有生成 EXE"
}
