data = open(r"E:\matlab os\start_fixed.ps1", "rb").read()
bom = data[:3] == b"\xef\xbb\xbf"
print("BOM detected" if bom else "No BOM")
print("First 3 bytes:", data[:3])