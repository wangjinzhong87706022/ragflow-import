# run_jhc.ps1 — 泾惠渠知识库 建库/导入 一键入口（密钥只经交互输入，不落盘）
#
# 用法（在 src/ 目录下）：
#   .\run_jhc.ps1                 # 阶段2：建库（先 dry-run 打印计划，确认后 --apply）
#   .\run_jhc.ps1 -Import         # 阶段3：全量导入（213 个文件，串行含 OCR，耗时较长）
#   .\run_jhc.ps1 -Import -Dataset jhc3 -Limit 5    # 冒烟：只导计划处库前 5 个
#
# 断点续传：run_import 幂等，中断后重跑同一命令即可从 import_state.json 续传。
param(
    [switch]$Import,
    [string]$Dataset,
    [int]$Limit
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot          # 管线脚本需在 src/ 下运行
$env:KB_PROFILE = 'jinghuiqu'
if (-not $env:RAGFLOW_API_BASE) {
    $env:RAGFLOW_API_BASE = 'https://labragf.openagp.top:9080/api/v1'   # 远程实例；本地自启可先 set 后运行
    Write-Host "[INFO] RAGFLOW_API_BASE 未设置，默认远程实例 $env:RAGFLOW_API_BASE"
}
if (-not $env:RAGFLOW_API_KEY) {
    $sec = Read-Host '请输入泾惠渠用户的 RAGFlow API Key（掩码输入）' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($sec)
    try { $env:RAGFLOW_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringUni($ptr) }
    finally { [Runtime.InteropServices.Marshal]::FreeHGlobal($ptr) }
}

if (-not $Import) {
    python run_setup.py --dry-run
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $ans = Read-Host '确认按上述计划建库？(y/N)'
    if ($ans -eq 'y') { python run_setup.py --apply }
    return
}

# 注意避开 PowerShell 自动变量 $args
$pyArgs = @('run_import.py', '--apply')
if ($Dataset) { $pyArgs += @('--dataset', $Dataset) }
if ($Limit)   { $pyArgs += @('--limit', $Limit) }
python @pyArgs
