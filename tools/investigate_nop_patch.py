"""Investigate context around the NOP-patched BL at 0x08154C66
and do a broader caller scan for FUN_0x081DE2E4 (including B.W).
"""
import lzma, struct, sys, capstone
md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

with open(sys.argv[1], "rb") as f:
    raw = f.read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000
fo = lambda a: a - BASE

print("=== STOCK firmware: context around 0x08154C66 (the NOP-patched BL) ===")
chunk = fw[fo(0x08154C40):fo(0x08154CA0)]
for ins in md.disasm(chunk, 0x08154C40):
    marker = "  <== PATCHED OUT" if ins.address == 0x08154C66 else ""
    print(f"  {ins.address:#010x}  {ins.mnemonic:8s} {ins.op_str}{marker}")

print()
print("=== Walking back to find containing function start ===")
chunk = fw[fo(0x08154A00):fo(0x08154C80)]
all_ins = list(md.disasm(chunk, 0x08154A00))
last_push = None
for ins in all_ins:
    if ins.address > 0x08154C66:
        break
    if ins.mnemonic.lower() in ("push", "push.w") and "lr" in ins.op_str.lower():
        last_push = ins.address
print(f"Containing function starts at: {last_push:#010x}")

print()
print("=== Broader caller scan for FUN_0x081DE2E4 (BL + BLX + B.W) ===")
TARGET = 0x081DE2E4

def decode_branch(four, pc):
    if len(four) < 4: return None, None
    hw1 = struct.unpack("<H", four[:2])[0]
    hw2 = struct.unpack("<H", four[2:4])[0]
    if (hw1 & 0xF800) != 0xF000: return None, None
    if (hw2 & 0x8000) == 0: return None, None
    kind = None
    op = hw2 & 0xD000
    if op == 0xD000: kind = "BL"
    elif op == 0xC000: kind = "BLX"
    elif op == 0x9000: kind = "B.W"
    elif op == 0x8000: kind = "B.W"
    else: return None, None
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
    return target, kind

callers = []
for off in range(0, len(fw) - 4, 2):
    t, kind = decode_branch(fw[off:off+4], BASE + off)
    if t == TARGET:
        callers.append((BASE + off, kind))
print(f"Total: {len(callers)}")
for addr, kind in callers:
    print(f"  {addr:#010x}  {kind}")

# Now also scan for the STOCK BL at 0x08154C66 to figure out what it called
print()
print("=== What did the patched-out BL at 0x08154C66 originally call? ===")
chunk = fw[fo(0x08154C66):fo(0x08154C6A)]
t, kind = decode_branch(chunk, 0x08154C66)
print(f"  Target: {t:#010x} ({kind})")
