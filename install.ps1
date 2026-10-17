# KnowFlow 一键安装 / 更新（Windows 10/11）
#
# 在 PowerShell 里运行：
#   irm https://raw.githubusercontent.com/jie-ot/KnowFlow/main/install.ps1 | iex
#
# 用命令下载的文件不带“来自互联网”标记，所以不会弹 SmartScreen 的“Windows 已保护你的电脑”，
# 也不会出现 .NET 拒绝加载 DLL 导致窗口打不开的问题。重复运行即更新到最新版，数据不受影响。
& {
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'  # 显示进度条会让下载慢很多
  [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

  $url  = 'https://github.com/jie-ot/KnowFlow/releases/latest/download/KnowFlow-Windows.zip'
  $dest = Join-Path $env:LOCALAPPDATA 'Programs\KnowFlow'
  $tmp  = Join-Path $env:TEMP ('knowflow-' + [guid]::NewGuid().ToString('N'))
  $zip  = "$tmp.zip"

  Write-Host '[KnowFlow] 正在下载最新版本，约 40 MB ...'
  Invoke-WebRequest $url -OutFile $zip -UseBasicParsing
  Expand-Archive $zip -DestinationPath $tmp -Force
  $exe = Get-ChildItem $tmp -Recurse -Filter 'KnowFlow.exe' | Select-Object -First 1
  if (-not $exe) { throw '压缩包里没有找到 KnowFlow.exe' }

  Get-Process KnowFlow -ErrorAction SilentlyContinue | Stop-Process -Force
  Start-Sleep -Milliseconds 800
  if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
  New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
  Move-Item $exe.Directory.FullName $dest
  Get-ChildItem $dest -Recurse -File | Unblock-File
  Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue

  $target = Join-Path $dest 'KnowFlow.exe'
  $shell = New-Object -ComObject WScript.Shell
  foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir 'KnowFlow.lnk'))
    $lnk.TargetPath = $target
    $lnk.WorkingDirectory = $dest
    $lnk.Save()
  }

  Write-Host "[KnowFlow] 已安装到 $dest"
  Write-Host '[KnowFlow] 桌面和开始菜单里都有 KnowFlow 快捷方式；数据在 %APPDATA%\KnowFlow，更新或重装不会丢。'
  Start-Process $target
}
