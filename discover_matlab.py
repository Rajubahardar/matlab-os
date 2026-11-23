#!/usr/bin/env python
import os
import subprocess

def discover_matlab():
    """Search for matlab.exe in D:\Apps\Matlab and detect version."""
    matlab_root = r"D:\Apps\Matlab"
    
    if not os.path.exists(matlab_root):
        print(f"Matlab root directory not found: {matlab_root}")
        return None, None
    
    # Walk through the directory to find matlab.exe
    matlab_exe = None
    for root, dirs, files in os.walk(matlab_root):
        for f in files:
            if f == "matlab.exe":
                matlab_exe = os.path.join(root, f)
                break
        if matlab_exe:
            break
    
    if matlab_exe:
        print(f"Found MATLAB executable: {matlab_exe}")
        
        # Try to get version info
        try:
            result = subprocess.run([matlab_exe, "-ver"], capture_output=True, text=True, timeout=5)
            print(f"Version output: {result.stdout}")
            version = result.stdout.strip()
        except Exception as e:
            print(f"Could not get version: {e}")
            version = "Unknown"
        
        # Check if MATLAB is running
        try:
            result = subprocess.run(
                ['tasklist', '/FO', 'CSV', '/IM', 'matlab.exe'],
                capture_output=True, text=True, timeout=5
            )
            matlab_running = "MATLAB" in result.stdout
            print(f"MATLAB running: {matlab_running}")
        except Exception as e:
            print(f"Could not check running status: {e}")
            matlab_running = False
        
        return matlab_exe, version
    else:
        print("matlab.exe not found in D:\\Apps\\Matlab")
        return None, None

if __name__ == "__main__":
    exe, ver = discover_matlab()
    if exe:
        print(f"\nDiscovery complete: {exe} (version: {ver})")
    else:
        print("\nDiscovery failed - no MATLAB executable found")