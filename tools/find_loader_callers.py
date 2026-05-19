"""Find ALL callers of the two NVDM-to-runtime balance loader functions.

Direct callers: BL/BLX instructions targeting the function address.
Indirect callers: function-pointer-table entries containing the function
address (literal pool entries that would be loaded into a register and
later branched to via BX/BLX).

Targets:
  FUN_0x081DDFD4 — async loader (caller 0x817B250 known)
  FUN_0x081DE2E4 — sync loader (callers unknown)

Outputs both direct BL targets and literal-pool references.

Usage: python find_loader_callers.py <fw.bin>
"""
import lzma, struct, sys

with open(sys.argv[1], "rb") as f:
    raw = f.read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000

TARGETS = {
    0x081DDFD4: "Loader A (async, uses 0x814fed8)",
    0x081DE2E4: "Loader B (sync, uses 0x814feac)",
}

def decode_thumb_bl(four, pc):
    if len(four) < 4: return None, False, False
    hw1 = struct.unpack("<H", four[:2])[0]
    hw2 = struct.unpack("<H", four[2:4])[0]
    if (hw1 & 0xF800) != 0xF000: return None, False, False
    if (hw2 & 0x8000) == 0: return None, False, False
    is_bl = bool(hw2 & 0x1000)
    is_blx = (hw2 & 0x1000) == 0 and (hw2 & 0xD000) == 0xC000
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
    return target, is_bl, is_blx

for target_addr, label in TARGETS.items():
    print(f"\n{'='*78}")
    print(f"=== {label} @ {target_addr:#010x}")
    print(f"{'='*78}\n")

    # Direct BL/BL.W callers
    print("-- Direct BL/BLX callers --")
    direct = []
    for off in range(0, len(fw) - 4, 2):
        target, is_bl, is_blx = decode_thumb_bl(fw[off:off+4], BASE + off)
        if target == target_addr and (is_bl or is_blx):
            direct.append((BASE + off, "BL" if is_bl else "BLX"))
    if not direct:
        print("  (none)")
    for addr, kind in direct:
        print(f"  {addr:#010x}  {kind}")

    # Indirect: literal-pool entries containing this address (Thumb +1)
    print("\n-- Literal-pool entries containing this address (indirect callers) --")
    for thumb_bit in (0, 1):
        search_addr = target_addr | thumb_bit
        ab = struct.pack("<I", search_addr)
        refs = []
        s = 0
        while True:
            idx = fw.find(ab, s)
            if idx == -1: break
            if idx % 4 == 0:
                refs.append(idx)
            s = idx + 1
        if refs:
            print(f"  Thumb+{thumb_bit} ({search_addr:#x}): {len(refs)} aligned ref(s)")
            for r in refs:
                print(f"    literal at file {r:#x} (runtime {BASE+r:#x})")

    print()
