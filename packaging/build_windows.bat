@echo off
rem DiffScope Windows build script (run from project root).
rem Requires: system Python with pyinstaller, PySide6, Pillow, openpyxl, PyPDF2.
rem NOTE: keep this file ASCII-only; cmd misparses UTF-8 Chinese comments.
setlocal
cd /d "%~dp0.."

pyinstaller --noconfirm --clean ^
  --name DiffScope ^
  --windowed ^
  --icon packaging/icon.ico ^
  --add-data "assets\icon_256.png;." ^
  --exclude-module PyQt5 ^
  --exclude-module tkinter ^
  --exclude-module QtWebEngineCore ^
  --exclude-module QtWebEngineWidgets ^
  --exclude-module QtQml ^
  --exclude-module QtQuick ^
  --exclude-module QtQuick3D ^
  --exclude-module QtMultimedia ^
  --exclude-module QtCharts ^
  --exclude-module QtDataVisualization ^
  --exclude-module QtNetworkAuth ^
  --exclude-module QtSensors ^
  --exclude-module QtSerialPort ^
  --exclude-module QtTest ^
  --exclude-module QtDesigner ^
  run.py

if errorlevel 1 (
  echo [build FAILED]
  exit /b 1
)
rem Create the zip distribution
powershell -NoProfile -Command "Compress-Archive -Path 'dist\DiffScope\*' -DestinationPath 'dist\DiffScope-windows-x64.zip' -Force"
echo [DONE] dist\DiffScope\ and dist\DiffScope-windows-x64.zip
endlocal
