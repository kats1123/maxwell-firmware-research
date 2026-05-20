"""Aggressive scan for ANY code path that writes to 0x142039AC byte 0 or 1.

Coverage:
1. Direct literal-pool refs to 0x142039AC (already searched — 6 hits, all loader cluster)
2. Refs to nearby base addresses (0x14203900, 0x14203800, etc.) with strb at offset
   that lands on byte 0 (0xAC) or byte 1 (0xAD) of our buffer
3. movw+movt building 0x142039AC, 0x14203900, etc.

Output: every function found that could write to 0x142039AC[0] or [1].
"""
import lzma, struct, sys, capstone, re
md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

with open(sys.argv[1], "rb") as f: raw = f.read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000
fo = lambda a: a - BASE if a >= BASE else None
BUFFER = 0x142039AC

# Candidate base addresses with offsets 0x0 through 0x400 that land on byte 0
print("=== Plausible struct-base addresses ===\n")
plausible_bases = []
for base in range(0x14203000, 0x14204000, 4):
    L_off = BUFFER - base  # 0x142039AC - base
    if 0 <= L_off <= 0xFFF:  # Thumb-2 STRB imm12 limit
        ab = struct.pack("<I", base)
        refs = []
        s = 0
        while True:
            idx = fw.find(ab, s)
            if idx == -1: break
            if idx % 4 == 0: refs.append(idx)
            s = idx + 1
        if refs:
            plausible_bases.append((base, L_off, refs))

for base, l_off, refs in plausible_bases:
    print(f"  base {base:#010x}  L_off=+{l_off:#x}  {len(refs)} literal-pool refs at rt: {[hex(BASE+r) for r in refs[:5]]}")

# For each plausible base ref, find LDR + STRB sequence
print("\n=== Decoded writes to 0x142039AC[0]/[1] via base+offset ===\n")

def resolve_pc_rel(ins):
    op = ins.op_str.lower()
    m = re.match(r"(r\d+),\s*\[pc,\s*#?(0x[0-9a-f]+|\d+)\]", op)
    if not m: return None, None
    reg = m.group(1)
    imm = int(m.group(2), 0)
    pc = ins.address + 4
    return reg, (pc & ~3) + imm

found_writes = []
for base, l_off, refs in plausible_bases:
    R_off = l_off + 1
    for r in refs:
        # Find LDR loading this literal (look 1024 bytes back)
        scan_start = max(0, r - 1024)
        chunk = fw[scan_start:r + 4]
        try:
            insns = list(md.disasm(chunk, BASE + scan_start))
        except: continue
        ldr_addr, ldr_reg = None, None
        for ins in insns:
            if not ins.mnemonic.lower().startswith('ldr'): continue
            reg, la = resolve_pc_rel(ins)
            if la == BASE + r:
                ldr_addr = ins.address
                ldr_reg = reg
        if not ldr_addr: continue
        # Now disasm forward 50 insns looking for strb [ldr_reg, +l_off] or +R_off
        fwd = fw[fo(ldr_addr):fo(ldr_addr) + 200]
        try:
            insns2 = list(md.disasm(fwd, ldr_addr))
        except: continue
        for ins in insns2[:50]:
            if not ins.mnemonic.lower().startswith('strb'): continue
            op = ins.op_str.lower()
            # Match [ldr_reg, #offset] or [ldr_reg]
            m = re.search(rf"\[{ldr_reg}(?:,\s*#?(-?\d+|0x[0-9a-f]+))?\]", op)
            if not m: continue
            off_str = m.group(1)
            off = int(off_str, 0) if off_str else 0
            if off == l_off:
                found_writes.append((ldr_addr, ins.address, base, l_off, 'LEFT byte'))
                print(f"  *** LDR @ {ldr_addr:#010x} loads base {base:#x}, STRB at {ins.address:#010x} -> [base+{l_off:#x}] = LEFT byte (0x142039AC[0])")
            elif off == R_off:
                found_writes.append((ldr_addr, ins.address, base, l_off, 'RIGHT byte'))
                print(f"  *** LDR @ {ldr_addr:#010x} loads base {base:#x}, STRB at {ins.address:#010x} -> [base+{R_off:#x}] = RIGHT byte (0x142039AD)")

if not found_writes:
    print("  (none found via base+offset path)")

print()
print("=== ALL writes to 0x142039AC[0] or [1] via the 6 direct literal-pool refs (recheck) ===\n")
# For each known direct ref to 0x142039AC, find STRB sites
DIRECT_REFS = [0x1befc8, 0x1bf038, 0x1bf054, 0x1bf090, 0x1bf11c, 0x1bf3e4]
for r in DIRECT_REFS:
    scan_start = max(0, r - 1024)
    chunk = fw[scan_start:r + 4]
    insns = list(md.disasm(chunk, BASE + scan_start))
    ldr_addr, ldr_reg = None, None
    for ins in insns:
        if not ins.mnemonic.lower().startswith('ldr'): continue
        reg, la = resolve_pc_rel(ins)
        if la == BASE + r:
            ldr_addr = ins.address; ldr_reg = reg
    if not ldr_addr: continue
    fwd = fw[fo(ldr_addr):fo(ldr_addr) + 80]
    try: insns2 = list(md.disasm(fwd, ldr_addr))
    except: continue
    for ins in insns2[:20]:
        if not ins.mnemonic.lower().startswith('strb'): continue
        op = ins.op_str.lower()
        m = re.search(rf"\[{ldr_reg}(?:,\s*#?(-?\d+|0x[0-9a-f]+))?\]", op)
        if not m: continue
        off = int(m.group(1), 0) if m.group(1) else 0
        if off in (0, 1):
            print(f"  STRB [base+{off}] at {ins.address:#010x} (LDR from {ldr_addr:#010x} -> ref @ rt {BASE+r:#x})")
