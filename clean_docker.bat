@echo off
echo Stopping Docker processes...
taskkill /F /IM "Docker Desktop.exe" 2>nul
taskkill /F /IM "dockerd.exe" 2>nul
taskkill /F /IM "docker.exe" 2>nul
net stop com.docker.service 2>nul

echo Taking ownership...
takeown /F "C:\ProgramData\DockerDesktop" /R /A >nul 2>&1

echo Granting full control...
icacls "C:\ProgramData\DockerDesktop" /grant Administrators:F /T >nul 2>&1

echo Deleting directory...
rmdir /S /Q "C:\ProgramData\DockerDesktop" >nul 2>&1

echo Done.
