#requires -version 5.1
<#
.SYNOPSIS
    Local AI OS V1 launcher.

.DESCRIPTION
    Reads [mcp] host and port from config.toml, starts the Simulink Agentic
    Toolkit MCP server (matlab-mcp-server.exe) if it is not already running,
    opens the interface in the browser, and stops that server again when you
    press Ctrl+C.

    Only a server that this launcher started is stopped on exit. A server that
    was already running when you launched is left alone.

    The MCP server executable is looked up in this order:
      1. mcp_server_path in config.toml
      2. %USERPROFILE%\.matlab\agentic-toolkits\bin\matlab-mcp-server.exe

.EXAMPLE
    cd "E:\matlab os"
    .\start.ps1
#>

$ErrorActionPreference = 'Stop'

# ------------------------------------------------------------------------
# Settings from config.toml (with defaults)
# ------------------------------------------------------------------------
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $ProjectRoot 'config.toml'

$McpHost = 'localhost'
$McpPort = 31515
$McpServerPath = $null

if (Test-Path -LiteralPath $ConfigPath) {
    # Match one line at a time: $matches is only populated for a single string,
    # never when -match filters a whole array.
    foreach ($line in Get-Content -LiteralPath $ConfigPath) {
        if ($line -match '^\s*host\s*=\s*"([^"]+)"') {
            $McpHost = $matches[1]
        }
        elseif ($line -match '^\s*port\s*=\s*(\d+)') {
            $McpPort = [int] $matches[1]
        }
        elseif ($line -match '^\s*mcp_server_path\s*=\s*"([^"]+)"') {
            $McpServerPath = $matches[1]
        }
    }
}
else {
    Write-Warning "config.toml not found at $ConfigPath - using defaults."
    Write-Warning 'Copy config.example.toml to config.toml and set engine_path first.'
}

if (-not $McpServerPath) {
    $McpServerPath = Join-Path $env:USERPROFILE '.matlab\agentic-toolkits\bin\matlab-mcp-server.exe'
}

$McpUrl = "http://${McpHost}:${McpPort}"
$HealthUrl = "$McpUrl/health"

# ------------------------------------------------------------------------
# Health check
# ------------------------------------------------------------------------
function Test-McpHealth {
    try {
        $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 5
        return ($response.StatusCode -eq 200)
    }
    catch {
        # Refused, timed out, 404 - all mean "not ready yet".
        return $false
    }
}

# ------------------------------------------------------------------------
# Ctrl+C
#   Read from the console rather than through a .NET event: a script block
#   cannot be attached to an event (the event fires on a thread with no
#   runspace), and CancelKeyPress is only reachable through compiled C#.
# ------------------------------------------------------------------------
function Test-CtrlCPressed {
    try {
        if (-not [Console]::KeyAvailable) { return $false }
        $key = [Console]::ReadKey($true)
        return (($key.Key -eq [ConsoleKey]::C) -and ($key.Modifiers -band [ConsoleModifiers]::Control))
    }
    catch {
        return $false
    }
}

# ------------------------------------------------------------------------
# Start, wait, clean up
# ------------------------------------------------------------------------
$serverProcess = $null
$ownsServer = $false

try {
    if (Test-McpHealth) {
        Write-Host "MCP server already running at $McpUrl."
    }
    else {
        if (-not (Test-Path -LiteralPath $McpServerPath)) {
            throw "MCP server not found at:`n  $McpServerPath`nInstall the Simulink Agentic Toolkit, or set mcp_server_path in config.toml."
        }

        Write-Host 'Starting Simulink Agentic Toolkit MCP server...'
        $serverProcess = Start-Process -FilePath $McpServerPath -PassThru
        $ownsServer = $true

        $deadline = (Get-Date).AddSeconds(60)
        while ((Get-Date) -lt $deadline) {
            if ($serverProcess.HasExited) {
                throw "MCP server exited immediately (exit code $($serverProcess.ExitCode)). If it speaks stdio only, it has to be started by your MCP client instead."
            }
            if (Test-McpHealth) {
                break
            }
            Start-Sleep -Seconds 2
        }

        if (-not (Test-McpHealth)) {
            throw "MCP server did not answer $HealthUrl within 60 seconds. Check $McpUrl manually."
        }

        Write-Host "MCP server is healthy (PID $($serverProcess.Id))."
    }

    Write-Host "Opening $McpUrl in your browser..."
    Start-Process $McpUrl

    Write-Host ''
    Write-Host 'Local AI OS V1 is running.'
    Write-Host "Interface: $McpUrl"
    Write-Host ''
    Write-Host 'Press Ctrl+C to stop the MCP server and exit.'

    if (-not [Console]::IsInputRedirected) {
        while (-not (Test-CtrlCPressed)) {
            Start-Sleep -Milliseconds 250
        }
        Write-Host ''
        Write-Host 'Ctrl+C received.'
    }
    else {
        $null = Read-Host 'Press Enter to stop the MCP server and exit'
    }
}
finally {
    if ($ownsServer) {
        if ($serverProcess -and -not $serverProcess.HasExited) {
            Write-Host "Stopping MCP server (PID $($serverProcess.Id))..."
            Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue
            $null = $serverProcess.WaitForExit(5000)
        }
        else {
            Write-Host 'MCP server had already stopped.'
        }
    }
    else {
        Write-Host 'The MCP server was already running before launch - leaving it running.'
    }

    Write-Host 'Local AI OS V1 stopped.'
}
