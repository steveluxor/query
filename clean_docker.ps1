# Self-elevate
if (-NOT ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
    Start-Process powershell.exe "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

Write-Output "Stopping Docker processes..."
Get-Process "Docker Desktop", "dockerd", "docker" -ErrorAction SilentlyContinue | Stop-Process -Force
Stop-Service "com.docker.service" -Force -ErrorAction SilentlyContinue

Write-Output "Taking ownership..."
& takeown /F "C:\ProgramData\DockerDesktop" /R /A 2>&1

Write-Output "Granting permissions..."
& icacls "C:\ProgramData\DockerDesktop" /grant Administrators:F /T 2>&1

Write-Output "Deleting directory..."
Remove-Item "C:\ProgramData\DockerDesktop" -Recurse -Force -ErrorAction Stop

Write-Output "Done! Directory deleted."
