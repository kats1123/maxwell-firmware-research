"""Search for literal-pool refs to addresses near 0x142039AC that might be
the base of a struct whose members include the L/R balance bytes.

If code accesses 0x142039AC via [struct_base + offset], the searches we did
for 0x142039AC literal refs would have missed it.

Usage: python find_struct_base_refs.py <fw.bin>
"""
import lzma, struct, sys, capstone, re
md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

with open(sys.argv[1], "rb") as f: raw = f.read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000
fo = lambda a: a - BASE if a >= BASE else None

# Candidate struct base addresses — anything within 64 bytes BEFORE 0x142039AC
# (with offset 0-64 to reach byte 0/1 of balance buffer)
candidates = []
for off in range(0, 128, 2):
    addr = 0x142039AC - off
    if addr >= 0x14000000 and addr < 0x14400000:
        candidates.append(addr)

print(f"Searching for literal-pool refs to {len(candidates)} candidate base addresses near 0x142039AC...\n")
print(f"{'addr':>10s}  {'L_off':>5s}  refs  contexts")
print("-" * 70)

found_addrs = []
for addr in candidates:
    ab = struct.pack("<I", addr)
    refs = []
    s = 0
    while True:
        idx = fw.find(ab, s)
        if idx == -1: break
        if idx % 4 == 0:
            refs.append(idx)
        s = idx + 1
    if refs:
        offset_to_L = 0x142039AC - addr
        found_addrs.append((addr, offset_to_L, refs))
        print(f"  {addr:#010x}  +{offset_to_L:3d}  {len(refs):3d}   {[hex(BASE+r) for r in refs[:6]]}")

print()
print("=== For each promising address, find code that LDRs it and then writes at the right offset ===\n")

def resolve_pc_rel(ins):
    op = ins.op_str.lower()
    m = re.match(r"(r\d+),\s*\[pc,\s*#?(0x[0-9a-f]+|\d+)\]", op)
    if not m: return None, None
    reg = m.group(1)
    imm = int(m.group(2), 0)
    pc = ins.address + 4
    return reg, (pc & ~3) + imm

for addr, l_off, refs in found_addrs:
    if l_off > 64: continue  # filter to plausible balance struct offsets
    if l_off == 0: continue  # we already analyzed this
    print(f"--- Base {addr:#010x} (L at +{l_off}, R at +{l_off+1}) ---")
    for r in refs[:3]:
        # Find the LDR that loads this literal
        scan_start = max(0, r - 1024)
        chunk = fw[scan_start:r+4]
        try:
            insns = list(md.disasm(chunk, BASE + scan_start))
        except: continue
        ldr_addr = None
        ldr_reg = None
        for ins in insns:
            if not ins.mnemonic.lower().startswith("ldr"): continue
            reg, la = resolve_pc_rel(ins)
            if la == addr:
                ldr_addr = ins.address
                ldr_reg = reg
        if ldr_addr:
            # Now disassemble forward from LDR for next ~30 instructions
            ldr_fo = fo(ldr_addr)
            forward = fw[ldr_fo:ldr_fo + 120]
            finsns = list(md.disasm(forward, ldr_addr))
            print(f"  LDR @ {ldr_addr:#010x} loads {ldr_reg} = base. Following code:")
            for fi in finsns[:20]:
                # Highlight if writing to [reg, #offset] where offset matches L position
                op = fi.op_str.lower()
                note = ""
                if fi.mnemonic.lower().startswith("strb") and ldr_reg in op:
                    m2 = re.search(r"\[(r\d+)(?:,\s*#?(-?\d+|0x[0-9a-f]+))?\]", op)
                    if m2:
                        used_reg = m2.group(1)
                        off_str = m2.group(2)
                        off_v = int(off_str, 0) if off_str else 0
                        if used_reg == ldr_reg and off_v == l_off:
                            note = "  <-- WRITES LEFT BYTE (offset matches!)"
                        elif used_reg == ldr_reg and off_v == l_off + 1:
                            note = "  <-- WRITES RIGHT BYTE"
                print(f"    {fi.address:#010x}  {fi.mnemonic:8s} {fi.op_str}{note}")
            print()
