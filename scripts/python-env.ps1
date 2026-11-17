# 解析 VigorSync 使用的 Python 解释器，顺序为：
# 1. 环境变量 VIGORSYNC_PYTHON
# 2. 项目内 .venv
# 3. conda 环境 VigorSync
function Resolve-VigorSyncPython {
    param([string]$ProjectRoot)

    if ($env:VIGORSYNC_PYTHON) {
        if (-not (Test-Path -LiteralPath $env:VIGORSYNC_PYTHON)) {
            throw "VIGORSYNC_PYTHON does not exist: $env:VIGORSYNC_PYTHON"
        }
        return $env:VIGORSYNC_PYTHON
    }

    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }

    if ($env:CONDA_PREFIX -and (Split-Path -Leaf $env:CONDA_PREFIX) -eq "VigorSync") {
        $activePython = Join-Path $env:CONDA_PREFIX "python.exe"
        if (Test-Path -LiteralPath $activePython) {
            return $activePython
        }
    }

    $condaBase = $null
    try {
        $condaBase = (& conda info --base 2>$null | Select-Object -First 1)
    }
    catch {
        $condaBase = $null
    }
    if ($condaBase) {
        $condaPython = Join-Path $condaBase "envs\VigorSync\python.exe"
        if (Test-Path -LiteralPath $condaPython) {
            return $condaPython
        }
    }

    throw "No VigorSync Python interpreter found. Create it with 'conda create -n VigorSync python=3.12' or set VIGORSYNC_PYTHON."
}
