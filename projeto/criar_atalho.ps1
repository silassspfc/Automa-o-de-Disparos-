# Execute este script uma vez para criar o atalho na área de trabalho
# Como usar: clique com botão direito no arquivo > "Executar com PowerShell"

$pasta   = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $pasta ".venv\Scripts\pythonw.exe"
$script  = Join-Path $pasta "executar.pyw"
$desktop = [Environment]::GetFolderPath("Desktop")

$ws = New-Object -ComObject WScript.Shell
$shortcut = $ws.CreateShortcut("$desktop\Certificados Onodera.lnk")
$shortcut.TargetPath       = $pythonw
$shortcut.Arguments        = "`"$script`""
$shortcut.WorkingDirectory = $pasta
$shortcut.IconLocation     = "shell32.dll,71"
$shortcut.Description      = "Gerar e enviar certificados Onodera"
$shortcut.Save()

Write-Host "Atalho criado na area de trabalho com sucesso."
pause
