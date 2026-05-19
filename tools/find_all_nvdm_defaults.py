"""Find ALL NVDM default-write call sites in the firmware.

Pattern: the firmware's factory init writes default values to NVDM. The
function 0x081AF824 is nvdm_write_default(key, buf, len). Each call site:
   movw r0, #<NVDM_KEY>
   movw r3, #<DEFAULT_IMM>  (or load from constant)
   movs r2, #<LENGTH>
   add  r1, sp, #<OFFSET>
   str  r3, [sp, #<OFFSET>]
   bl   #0x081AF824

By finding all of these, we know what's patchable persistently.
"""
import lzma, struct
import capstone, sys
md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

if len(sys.argv) < 2:
    print("Usage: python find_all_nvdm_defaults.py <Maxwell_v1.0.1.74_XBOX_headset.bin>")
    sys.exit(1)
FW = sys.argv[1]
with open(FW, "rb") as f:
    raw = f.read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000

NVDM_WRITE_FN = 0x081AF824

# Use my Thumb-2 BL decoder to find all calls to 0x081AF824
def decode_thumb_bl(four_bytes, pc):
    if len(four_bytes) < 4: return None, False
    hw1 = struct.unpack("<H", four_bytes[:2])[0]
    hw2 = struct.unpack("<H", four_bytes[2:4])[0]
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

# Scan all 2-byte-aligned offsets
print(f"Scanning for BL to {NVDM_WRITE_FN:#x} (nvdm_write_default)...")
call_sites = []
for off in range(0, len(fw) - 4, 2):
    target, is_bl = decode_thumb_bl(fw[off:off+4], BASE + off)
    if target == NVDM_WRITE_FN and is_bl:
        call_sites.append(off)

print(f"Found {len(call_sites)} call sites")

# For each call site, look BACKWARDS to find:
# - the movw r0, #<key> (NVDM key)
# - the movw r3, #<default> (initial value)
# - the movs r2, #<len> (length)
import re

def find_recent_movw(file_off, target_reg, lookback=0x40):
    """Look backward for a movw rN, #imm setting target_reg.
    Returns the immediate value or None.
    target_reg is e.g. 'r0' or 'r3'."""
    start = max(0, file_off - lookback)
    chunk = fw[start:file_off]
    instrs = list(md.disasm(chunk, BASE + start))
    # Look at the most recent movw setting our register
    for ins in reversed(instrs):
        mn = ins.mnemonic.lower()
        op = ins.op_str.lower()
        if mn.startswith("movw") and op.startswith(target_reg + ","):
            try:
                imm_str = op.split(",", 1)[1].strip().lstrip("#")
                return int(imm_str, 16)
            except: pass
        # Also handle mov.w (could be different form)
        if mn in ("mov.w", "movs", "mov") and op.startswith(target_reg + ","):
            try:
                imm_str = op.split(",", 1)[1].strip().lstrip("#")
                if imm_str.startswith("0x") or imm_str.replace(".","").isdigit():
                    return int(imm_str, 16) if imm_str.startswith("0x") else int(imm_str)
            except: pass
    return None

# Decode each call site
print()
print(f"{'#':>3} {'file_off':>10s} {'runtime':>12s}  {'NVDM key':>10s}  {'default':>10s}  {'len':>5s}")
print("-" * 70)
seen_keys = set()
for i, off in enumerate(call_sites):
    addr = BASE + off
    key = find_recent_movw(off, "r0", lookback=0x80)
    default = find_recent_movw(off, "r3", lookback=0x40)
    length = find_recent_movw(off, "r2", lookback=0x40)
    key_str = f"{key:#06x}" if key is not None else "?"
    default_str = f"{default:#06x}" if default is not None else "?"
    length_str = f"{length}" if length is not None else "?"
    flag = ""
    if key is not None:
        if key == 0xf665: flag = " <- USB-C balance (our patched key)"
        elif key == 0xf668: flag = " <- BT/dongle balance (our patched key)"
        elif key == 0xe400: flag = " <- error log"
    print(f"{i:3d}  {off:#010x}  {addr:#010x}  {key_str:>10s}  {default_str:>10s}  {length_str:>5s}{flag}")
    if key is not None:
        seen_keys.add(key)

print()
print(f"Unique NVDM keys seen: {len(seen_keys)}")
for k in sorted(seen_keys):
    print(f"  {k:#06x}")
