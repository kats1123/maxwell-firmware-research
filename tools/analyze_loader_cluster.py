"""Analyze every function in the loader cluster (0x081DDFC8-0x081DE3E4)
that references 0x142039AC.

For each LDR pc-rel site that loads 0x142039AC, find:
- The containing function (walk back to nearest PUSH or BL boundary)
- What the function does with the address (read vs write, what value)
- Direct and indirect callers of the containing function

Usage: python analyze_loader_cluster.py <fw.bin>
"""
import lzma, struct, sys
import capstone, re

md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
md.detail = True

with open(sys.argv[1], "rb") as f:
    raw = f.read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000

# 6 known literal-pool entries containing 0x142039AC
LITERALS = [0x1befc8, 0x1bf038, 0x1bf054, 0x1bf090, 0x1bf11c, 0x1bf3e4]

def runtime(off): return BASE + off
def file_off(addr):
    if addr < BASE: return None
    o = addr - BASE
    return o if 0 <= o < len(fw) else None

def find_ldr_for_literal(lit_off):
    """Find the LDR pc-rel site that loads from this literal-pool entry."""
    lit_addr = runtime(lit_off)
    # Scan up to 1024 bytes before
    scan_start = max(0, lit_off - 1024)
    chunk = fw[scan_start:lit_off + 4]
    try:
        insns = list(md.disasm(chunk, runtime(scan_start)))
    except Exception:
        return None
    for ins in insns:
        mn = ins.mnemonic.lower()
        if not mn.startswith("ldr"):
            continue
        op = ins.op_str.lower()
        m = re.match(r"(r\d+),\s*\[pc,\s*#?(0x[0-9a-f]+|\d+)\]", op)
        if not m: continue
        imm = int(m.group(2), 0)
        pc = ins.address + 4
        la = (pc & ~3) + imm
        if la == lit_addr:
            return (ins.address, m.group(1))
    return None

def find_function_start(addr, max_search=0x300):
    """Walk backwards looking for a PUSH instruction or function-prologue
    pattern (PUSH {.*lr} is a strong indicator on Cortex-M)."""
    fo = file_off(addr)
    if fo is None: return None
    scan_start = max(0, fo - max_search)
    chunk = fw[scan_start:fo + 4]
    try:
        insns = list(md.disasm(chunk, runtime(scan_start)))
    except Exception:
        return None
    # Walk through and find the last PUSH instruction before addr
    last_push = None
    for ins in insns:
        if ins.address > addr:
            break
        mn = ins.mnemonic.lower()
        if mn in ("push", "push.w") and "lr" in ins.op_str.lower():
            last_push = ins.address
    return last_push

def disasm_window(addr, before=8, after=16):
    fo = file_off(addr)
    if fo is None: return []
    # Find the actual start somewhere before
    start = max(0, fo - before * 4)
    end = min(len(fw), fo + after * 4)
    chunk = fw[start:end]
    try:
        return list(md.disasm(chunk, runtime(start)))
    except Exception:
        return []

print("=== Analysis of each LDR site that loads 0x142039AC ===\n")
function_starts = set()
for lit_off in LITERALS:
    ldr = find_ldr_for_literal(lit_off)
    if not ldr:
        print(f"\n-- Literal @ {runtime(lit_off):#010x}: NO LDR found")
        continue
    ldr_addr, ldr_reg = ldr
    func_start = find_function_start(ldr_addr)
    if func_start:
        function_starts.add(func_start)
    print(f"\n{'-'*70}")
    print(f"-- Literal @ file {lit_off:#x} (rt {runtime(lit_off):#x})")
    print(f"   LDR site: {ldr_addr:#010x}  loads into {ldr_reg}")
    print(f"   Function start (nearest PUSH lr): {func_start:#x}" if func_start else "   Function start: unknown")
    # Disassemble window around LDR
    print(f"   Disassembly around LDR:")
    insns = disasm_window(ldr_addr, before=4, after=12)
    for ins in insns:
        marker = "  <==" if ins.address == ldr_addr else ""
        print(f"     {ins.address:#010x}  {ins.mnemonic:8s} {ins.op_str}{marker}")

print("\n\n=== Unique function starts found ===\n")
for fs in sorted(function_starts):
    print(f"  {fs:#010x}")
    # Find direct BL callers of each
    def decode_bl(four, pc):
        if len(four) < 4: return None, False
        hw1 = struct.unpack("<H", four[:2])[0]
        hw2 = struct.unpack("<H", four[2:4])[0]
        if (hw1 & 0xF800) != 0xF000: return None, False
        if (hw2 & 0x8000) == 0: return None, False
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
        return target, True
    direct = []
    for off in range(0, len(fw) - 4, 2):
        target, is_bl = decode_bl(fw[off:off+4], BASE + off)
        if target == fs:
            direct.append(BASE + off)
    print(f"    direct BL callers: {len(direct)}")
    for d in direct:
        print(f"      {d:#010x}")
    # Indirect: literal-pool entries containing fs (with or without Thumb bit)
    for thumb in (0, 1):
        ab = struct.pack("<I", fs | thumb)
        s = 0
        ind = []
        while True:
            idx = fw.find(ab, s)
            if idx == -1: break
            if idx % 4 == 0:
                ind.append(idx)
            s = idx + 1
        if ind:
            print(f"    indirect (Thumb+{thumb}, {fs|thumb:#x}): {len(ind)} literal-pool entries")
            for r in ind[:5]:
                print(f"      file {r:#x} (rt {BASE+r:#x})")
