import struct, os

TARGET  = r"D:\OKApps\ok-nte\ok-nte\data\apps\ok-nte\python\pythonw.exe"
ARGS    = r"D:\OKApps\launcher\launcher.py"
WORKDIR = r"D:\OKApps\launcher"
ICON    = r"D:\OKApps\launcher\assets\ok-script-app.ico"
NAME    = "OK 游戏助手"
LNK     = r"D:\桌面\OK 游戏助手.lnk"

def ustr(s):
    b = s.encode("utf-16-le") + b"\x00\x00"   # UTF-16LE + null terminator
    return struct.pack("<H", len(b)) + b       # 2-byte byte-count prefix (incl null)

# ---- LinkInfo (ANSI LocalBasePath, 路径全 ASCII) ----
localbase = TARGET.encode("ascii") + b"\x00"
volume_id = struct.pack("<IIII", 16, 3, 0, 16)   # size=16, Fixed, serial=0, labelOff=16(空)
linkinfo_body = volume_id + localbase
linkinfo = struct.pack("<IIIIIII",
                       28 + len(linkinfo_body),   # LinkInfoSize
                       28,                         # HeaderSize
                       0x1,                        # Flags: VolumeIDAndLocalBasePath
                       28,                         # VolumeIDOffset
                       28 + len(volume_id),       # LocalBasePathOffset
                       0, 0) + linkinfo_body

# ---- Header ----
LinkFlags = 0x2 | 0x8 | 0x10 | 0x20 | 0x40 | 0x80   # LinkInfo|Name|WorkDir|Args|Icon|Unicode
clsid = bytes.fromhex("0002140100000000c000000000000046")
header = struct.pack("<I", 76) + clsid
header += struct.pack("<I", LinkFlags)
header += struct.pack("<I", 0x20)                 # FileAttributes NORMAL
header += b"\x00" * 24                            # 3 x 8-byte times
header += struct.pack("<I", 0)                    # FileSize
header += struct.pack("<I", 0)                    # IconIndex
header += struct.pack("<I", 0x1)                  # ShowCommand SW_SHOWNORMAL
header += struct.pack("<H", 0)                    # HotKey
header += struct.pack("<H", 0)                    # Reserved1
header += struct.pack("<I", 0)                    # Reserved2
header += struct.pack("<I", 0)                    # Reserved3

stringdata = ustr(NAME) + ustr(WORKDIR) + ustr(ARGS) + ustr(ICON)

data = header + linkinfo + stringdata
with open(LNK, "wb") as f:
    f.write(data)
print("WROTE", len(data), "bytes ->", LNK)

# ---- 回读验证 ----
d = open(LNK, "rb").read().decode("utf-16-le", errors="ignore")
print("含 pythonw.exe:", "pythonw.exe" in d)
print("含 launcher.py:", "launcher.py" in d)
print("含 工作目录:", "D:\\OKApps\\launcher" in d)
print("含 图标:", "ok-script-app.ico" in d)
