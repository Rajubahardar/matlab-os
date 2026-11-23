# Local AI OS V1 Two-Command Launcher
# Usage: cd "E:\matlab os" .\\start.ps1
#
# This launcher starts Local AI OS V1 with two PowerShell commands.
# It automatically starts the MATLAB MCP server, waits for it to be healthy,
# and opens the browser interface.

# Resolve MATLAB path - check hardcoded path first, then config.toml
$MATLAB_ENGINE_PATH = "D:/Apps/Matlab/bin/matlab.exe"
if (-not (Test-Path "$MATLAB_ENGINE_PATH")) {
    $configPath = "e:\matlab os\config.toml"
    if (Test-Path $configPath) {
        $configContent = Get-Content $configPath
        $engineMatch = $configContent -match '^\s*engine_path\s*="(.+)"'
        if ($engineMatch) {
            $MATLAB_ENGINE_PATH = $matches[1]
        }
    }
}

if (-not (Test-Path "$MATLAB_ENGINE_PATH")) {
    Write-Error "Failed to start MCP server. Check that MATLAB is installed at $MATLAB_ENGINE_PATH"
    exit 1
}

Write-Host "Starting MATLAB MCP server..."

# MCP configuration from config.toml (V1 Release Candidate)
$MCP_HOST = "localhost"
$MCP_PORT = 31515
$MCP_URL = "http://${MCP_HOST}:$MCP_PORT"
$mcpUrl = "$MCP_URL/health"

# Check if MCP server is already running
$mcpHealthy = $false

try {
    $response = Invoke-WebRequest -Uri $mcpUrl -Method Get -TimeoutSec 5
    $mcpHealthy = $response.StatusCode -eq 200
} catch {
    # MCP server not running - will start it
}

# Start MATLAB MCP server if not already healthy
if (-not $mcpHealthy) {
    Start-Process -FilePath $MATLAB_ENGINE_PATH -ArgumentList "-r", "startMCPServer; exit" -NoNewWindow | Out-Null

    # Wait for server to become healthy
    $maxWait = 60
    $waited = 0
    while ($waited -lt $maxWait) {
        try {
            $response = Invoke-WebRequest -Uri $mcpUrl -Method Get -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                $mcpHealthy = $true
                break
            }
        } catch {
            # Server not yet ready
        }
        Start-Sleep -Seconds 2
        $waited += 2
    }
}

# Open browser to the interface
if ($mcpHealthy) {
    Write-Host "Opening browser to $MCP_URL ..."
    # -Verb RunAs unnecessarily elevates the browser; launch without elevation for local URLs
    Start-Process -Uri $MCP_URL

    # Display status
    Write-Host "Local AI OS V1 is running."
    Write-Host "Interface: $MCP_URL"
    Write-Host "Press Ctrl+C to stop the MATLAB MCP server and exit."

    # Wait for Ctrl+C
    while ($true) {
        Start-Sleep -Seconds 5
    }
} else {
    Write-Error "Failed to start MCP server. Check that MATLAB is installed at $MATLAB_ENGINE_PATH"
    exit 1
}
