# Build the Marian desktop client into a single .exe.
#   cd C:\Users\caden\hardspace-finance\client
#   powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
# Output: dist\Marian.exe  (distribute this single file).

$ErrorActionPreference = "Stop"
python -m pip install --quiet --upgrade pyinstaller
python -m PyInstaller --noconfirm --clean hardspace.spec
Write-Host ""
Write-Host "Built: $(Resolve-Path dist\Marian.exe)"
Write-Host "Distribute that single file. Users run it, sign in, and enter their own keys in Settings."
