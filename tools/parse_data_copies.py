"""Parse the boot-time .data section copies from the reset handler.

The reset handler at runtime 0x08133000 contains a sequence of BL calls
to a memcpy helper at 0x08133158. Each call uses three LDR pc-rel
instructions to load (src, dst_start, dst_end) and then BL.

Extract every such (src, dst_start, dst_end) tuple by scanning the
reset handler region.

Usage: python parse_data_copies.py <fw.bin>
"""
import lzma, struct, sys, capstone, re

md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

with open(sys.argv[1], "rb") as f:
    raw = f.read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000

# Scan reset handler region: 0x08133000 - 0x08133150 (before the memcpy helper)
SCAN_START = 0x08133000
SCAN_END = 0x08133158
fo = lambda a: a - BASE

chunk = fw[fo(SCAN_START):fo(SCAN_END)]
insns = list(md.disasm(chunk, SCAN_START))

# State: track recent LDR pc-rel results (reg -> value)
reg_vals = {}

def resolve_pc_rel(ins):
    op = ins.op_str.lower()
    m = re.match(r"(r\d+),\s*\[pc,\s*#?(0x[0-9a-f]+|\d+)\]", op)
    if not m: return None, None
    reg = m.group(1)
    imm = int(m.group(2), 0)
    pc = ins.address + 4
    lit_addr = (pc & ~3) + imm
    f = fo(lit_addr)
    if f is None or f + 4 > len(fw): return reg, None
    val = struct.unpack("<I", fw[f:f+4])[0]
    return reg, val

copies = []

for ins in insns:
    mn = ins.mnemonic.lower()
    if mn.startswith("ldr"):
        reg, val = resolve_pc_rel(ins)
        if reg is not None and val is not None:
            reg_vals[reg] = val
    elif mn in ("bl", "bl.w"):
        try:
            target = int(ins.op_str.lstrip("#"), 0)
        except:
            target = None
        if target == 0x08133158:
            # Captured a memcpy call. Args: r1=src, r2=dst, r3=dst_end
            src = reg_vals.get("r1")
            dst = reg_vals.get("r2")
            dst_end = reg_vals.get("r3")
            copies.append((ins.address, src, dst, dst_end))

print(f"=== Boot-time .data copies (via memcpy at 0x08133158) ===\n")
print(f"{'caller':>10s}  {'src_flash':>12s}  {'dst_start':>12s}  {'dst_end':>12s}  {'size':>8s}")
print("-" * 72)
for caller, src, dst, end in copies:
    size = end - dst if (end is not None and dst is not None) else None
    sz_str = f"{size:#x}" if size is not None else "?"
    src_str = f"{src:#x}" if src is not None else "?"
    dst_str = f"{dst:#x}" if dst is not None else "?"
    end_str = f"{end:#x}" if end is not None else "?"
    print(f"  {caller:#010x}  {src_str:>12s}  {dst_str:>12s}  {end_str:>12s}  {sz_str:>8s}")

# Also list memset-style zero-init regions (the loop at 0x08133040)
print(f"\n=== Also-look-at zero-init loop at 0x08133040 ===")
print("  (Cleared region: from value at 0x081330DC to 0x081330E0... not parsed yet.)")
