$content = Get-Content 'E:\matlab os\start.ps1' -Raw
$null = [System.Management.Automation.Language.Parser]::ParseInput($content, [ref]$null, [ref]$null)
Write-Host "PowerShell syntax validation: PASSED"