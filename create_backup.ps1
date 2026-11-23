$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backupPath = "E:\matlab os\backup_V1_$timestamp"
$exclude = @('.git','*.pyc','__pycache__','.venv','venv','target','build','*.exp','*.lib','*.pdb','*.log','*.html','*.html.summary')
Copy-Item -Path "E:\matlab os\*" -Destination $backupPath -Recurse -Exclude $exclude
Write-Output "Backup created at: $backupPath"