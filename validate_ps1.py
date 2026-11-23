import subprocess
result = subprocess.run(
    ['powershell', '-NoProfile', '-c',
     '$c = Get-Content "E:\\matlab os\\start.ps1" -Raw; '
     '[System.Management.Automation.Language.Parser]::ParseInput($c, [ref]$null, [ref]$null) | Out-Null; '
     'Write-Host "Syntax OK"'],
    capture_output=True, text=True, timeout=10)
print('Return code:', result.returncode)
print('STDOUT:', result.stdout)
print('STDERR:', result.stderr)