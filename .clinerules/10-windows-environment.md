# 10 - Windows Environment

- Shell is Windows PowerShell 5.1. Do not use bash syntax: no &&, no ||,
  no heredocs, no export, no touch, no cat, no grep, no `rm -rf`.
- Chain commands with ; or run them one per call.
- The project path has a space: E:\matlab os. Always quote paths.
- Use .\.venv\Scripts\python.exe for python and pip. Do not use the
  Activate.ps1 script.
- Use pathlib in code; never hardcode a path outside config.toml.
- MATLAB is at D:\Apps\Matlab (verify with config.toml). Read it from config.
- Use ASCII in files and console output; no emoji in code or logs.
- Use Get-ChildItem and Select-String to inspect files, not Unix tools.