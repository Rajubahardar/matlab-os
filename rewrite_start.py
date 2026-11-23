#!/usr/bin/env python
import re

# Read the original file
with open("e:\\matlab os\\start.ps1", "r") as f:
    content = f.read()

# Fix 1: Add MATLAB path resolution before the hardcoded path
# Replace the hardcoded configuration section
old_config = '''# Hardcoded configuration from config.toml (V1 Release Candidate)
$MCP_HOST = "localhost"
$MCP_PORT = 31515
$MCP_URL = "http://${MCP_HOST}:$MCP_PORT"
$MATLAB_ENGINE_PATH = "D:/Apps/Matlab/bin/matlab.exe"'''

new_config = '''# Resolve MATLAB path - check hardcoded path first, then config.toml
$MATLAB_ENGINE_PATH = "D:/Apps/Matlab/bin/matlab.exe"
if (-not (Test-Path "$MATLAB_ENGINE_PATH")) {
    $configPath = "e:\\matlab os\\config.toml"
    if (Test-Path $configPath) {
        $configContent = Get-Content $configPath
        $engineMatch = $configContent -match "^\s*engine_path\s*=\s*"(.+)""
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
$MCP_URL = "http://${MCP_HOST}:$MCP_PORT"'''

if old_config in content:
    content = content.replace(old_config, new_config)
    print("Fixed MATLAB path resolution")
else:
    print("Could not find old config section to replace")

# Fix 2: Remove -Verb RunAs from browser launch
old_browser = 'Start-Process -Uri $MCP_URL -Verb RunAs'
new_browser = 'Start-Process -Uri $MCP_URL'

if old_browser in content:
    content = content.replace(old_browser, new_browser)
    print("Fixed browser elevation")
else:
    print("Could not find browser launch line to fix")

# Write the modified content
with open("e:\\matlab os\\start.ps1", "w") as f:
    f.write(content)

print("Done! File rewritten.")