"""Find ALL NVDM read call sites and the keys they read.

NVDM read functions in the firmware:
- 0x814feac (nvdm_read_lock_protect)  — the main read path
- 0x814fed8 (nvdm_write — for reference, NOT a read)

For each BL to 0x814feac, look backward to find the movw of the NVDM key.

Usage: python find_all_nvdm_reads.py <fw.bin>
"""
import lzma, struct, sys, re
import capstone

md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

with open(sys.argv[1], "rb") as f:
    raw = f.read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000

NVDM_READ_FN = 0x0814feac

def decode_thumb_bl(four, pc):
    if len(four) < 4: return None, False
    hw1 = struct.unpack("<H", four[:2])[0]
    hw2 = struct.unpack("<H", four[2:4])[0]
    if (hw1 & 0xF800) != 0xF000: return None, False
    if (hw2 & 0x8000) == 0: return None, False
    is_bl = bool(hw2 & 0x1000)
    S = (hw1 >> 10) & 1
    imm10 = hw1 & 0x3FF
    J1 = (hw2 >> 13) & 1
    J2 = (hw2 >> 11) & 1
    imm11 = hw2 & 0x7FF
    I1 = 1 ^ J1 ^ S
    I2 = 1 ^ J2 ^ S
    imm = (S << 24) | (I1 << 23) | (I2 << 22) | (imm10 << 12) | (imm11 << 1)
    if S: imm |= 0xFE000000
    if imm & 0x80000000: imm = imm - 0x100000000
    target = ((pc + 4) + imm) & 0xFFFFFFFF
    return target, is_bl

print(f"Scanning for BL to {NVDM_READ_FN:#x} (nvdm_read)...\n")
call_sites = []
for off in range(0, len(fw) - 4, 2):
    target, is_bl = decode_thumb_bl(fw[off:off+4], BASE + off)
    if target == NVDM_READ_FN and is_bl:
        call_sites.append(off)
print(f"Found {len(call_sites)} call sites\n")

def find_recent_movw(file_off, target_reg, lookback=0x80):
    start = max(0, file_off - lookback)
    chunk = fw[start:file_off]
    try:
        instrs = list(md.disasm(chunk, BASE + start))
    except:
        return None
    for ins in reversed(instrs):
        mn = ins.mnemonic.lower()
        op = ins.op_str.lower()
        if mn == "movw" and op.startswith(target_reg + ","):
            try:
                imm_str = op.split(",", 1)[1].strip().lstrip("#")
                return int(imm_str, 16) if imm_str.startswith("0x") else int(imm_str)
            except: pass
        if mn in ("mov.w", "mov", "movs") and op.startswith(target_reg + ","):
            try:
                imm_str = op.split(",", 1)[1].strip().lstrip("#")
                return int(imm_str, 16) if imm_str.startswith("0x") else int(imm_str)
            except: pass
    return None

print(f"{'#':>3} {'rt_addr':>12s}  {'NVDM key':>10s}  {'length':>6s}")
print("-" * 60)
key_count = {}
sites_by_key = {}
for i, off in enumerate(call_sites):
    addr = BASE + off
    key = find_recent_movw(off, "r0", lookback=0x80)
    length = find_recent_movw(off, "r3", lookback=0x40)
    key_str = f"{key:#06x}" if key is not None else "?"
    len_str = f"{length}" if length is not None else "?"
    print(f"{i:3d}  {addr:#012x}  {key_str:>10s}  {len_str:>6s}")
    if key is not None:
        key_count[key] = key_count.get(key, 0) + 1
        sites_by_key.setdefault(key, []).append(addr)

print(f"\n=== Unique NVDM keys READ ({len(key_count)} keys) ===\n")
for k in sorted(key_count.keys()):
    sites = sites_by_key[k]
    sites_str = ", ".join(f"{s:#x}" for s in sites[:3])
    if len(sites) > 3: sites_str += f" +{len(sites)-3}"
    note = ""
    KNOWN_KEYS = {
        0xF665: "USB-C balance — only read by dead-code Loader B",
        0xF668: "BT/dongle balance — only read by dead-code Loader B",
        0xF702: "Current source state (read by FUN_0x0817B2F4 wrapper)",
        0xF700: "Unknown — read by FUN_0x0817B334",
        0xF778: "Unknown — read by FUN_0x081DE058 (cmd 0x0901 sub 0x31)",
        0xF082: "Factory reset sentinel ('U' triggers reset)",
        0xE091: "Unknown 6-byte struct",
        0xE1E0: "Suspected volume limiter (default 0x7FFF)",
        0xE1E5: "Unknown 8-byte struct",
        0xE301: "Suspected EQ preset / DSP coeffs (564 bytes)",
        0xE304: "Companion to E301 (194 bytes)",
        0xE400: "Error log",
    }
    note = KNOWN_KEYS.get(k, "")
    print(f"  {k:#06x}  ({key_count[k]:3d} read sites)  {note}")
    print(f"           at: {sites_str}")
