# Instala o Clipper do GTA 6 no Windows (PowerShell) A PARTIR DESTA PASTA. Versão blindada:
#   - NÃO baixa a skill da internet (a cópia local é a única fonte)
#   - NÃO executa script remoto (nada de "irm ... | iex", nada de ExecutionPolicy Bypass)
#   - os pacotes vêm de requirements.txt, em versões fixas
# Uso:  & "$HOME\.claude\skills\clipper-gta6\instalar.ps1"
$ErrorActionPreference = "Continue"
$aqui = if ($PSScriptRoot) { $PSScriptRoot } else { Join-Path $HOME ".claude\skills\clipper-gta6" }

$py = $null
foreach ($c in @(@{exe = "py"; pre = @("-3")}, @{exe = "python"; pre = @()})) {
  $cmd = Get-Command $c.exe -ErrorAction SilentlyContinue
  # o python.exe da pasta WindowsApps é só um atalho que abre a Microsoft Store: ignora
  if ($cmd -and $cmd.Source -notlike "*\WindowsApps\*") {
    $pre = $c.pre
    try { & $c.exe @pre -c "import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)" 2>$null } catch { continue }
    if ($LASTEXITCODE -eq 0) { $py = $c; break }
  }
}
if (-not $py) {
  Write-Host "Não achei o Python 3.10 a 3.13 neste computador."
  Write-Host "Instale o oficial (python.org ou: winget install -e --id Python.Python.3.12 --scope user),"
  Write-Host "feche e abra o PowerShell e rode este arquivo de novo."
  exit 1
}
Write-Host "OK: Python encontrado"
$pre = $py.pre
& $py.exe @pre (Join-Path $aqui "scripts\clip.py") instalar
