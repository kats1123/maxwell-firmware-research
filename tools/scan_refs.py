"""Exhaustive reference scan: callers of a function + all uses of a constant.

Scans EVERY 2-byte offset (Thumb alignment) and decodes one instruction,
so it does not depend on a linear sweep staying in sync.

Finds:
  - BL/BLX instructions whose target == --fn
  - literal-pool words (32-bit) == --val, and any ldr that loads them
  - raw 16-bit / 32-bit occurrences of --val in the whole image

Usage: python scan_refs.py <fw.bin> --fn 0x081DE120 --val 0xF702
"""
import lzma, struct, sys, capstone

raw = open(sys.argv[1], "rb").read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000
END = BASE + len(fw)
fo = lambda a: a - BASE

fn = None
val = None
args = sys.argv[2:]
for i, a in enumerate(args):
    if a == "--fn":
        fn = int(args[i+1], 0)
    if a == "--val":
        val = int(args[i+1], 0)

md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

# --- 1. caller scan (BL/BLX to fn) ---
if fn is not None:
    fn_t = fn & ~1
    print(f"=== BL/BLX callers of {fn:#010x} ===")
    hits = 0
    for off in range(0, len(fw) - 4, 2):
        try:
            ins = next(md.disasm(fw[off:off+4], BASE + off, 1))
        except StopIteration:
            continue
        mn = ins.mnemonic.lower()
        if mn in ("bl", "blx", "b.w", "b"):
            try:
                tgt = int(ins.op_str.lstrip("#"), 0) & ~1
            except ValueError:
                continue
            if tgt == fn_t:
                print(f"  {ins.address:#010x}  {mn} {ins.op_str}")
                hits += 1
    print(f"  total: {hits}\n")

# --- 2. constant scan ---
if val is not None:
    print(f"=== occurrences of {val:#x} in image ===")
    # 32-bit little-endian
    b32 = struct.pack("<I", val & 0xFFFFFFFF)
    print(f"-- 32-bit LE word {b32.hex()} (literal-pool / data) --")
    o = 0
    lit_addrs = []
    while True:
        o = fw.find(b32, o)
        if o < 0:
            break
        if o % 2 == 0:
            lit_addrs.append(BASE + o)
            print(f"  {BASE+o:#010x}")
        o += 1
    # 16-bit LE
    b16 = struct.pack("<H", val & 0xFFFF)
    print(f"-- 16-bit LE halfword {b16.hex()} (count only) --")
    cnt = 0
    o = 0
    while True:
        o = fw.find(b16, o)
        if o < 0:
            break
        cnt += 1
        o += 1
    print(f"  {cnt} occurrences (incl. the 32-bit ones above)")
    # any ldr [pc,#x] that resolves to one of the literal addrs
    if lit_addrs:
        print(f"-- ldr r,[pc,#x] loading one of those literals --")
        for off in range(0, len(fw) - 4, 2):
            try:
                ins = next(md.disasm(fw[off:off+4], BASE + off, 1))
            except StopIteration:
                continue
            mn = ins.mnemonic.lower()
            if mn.startswith("ldr") and "[pc," in ins.op_str:
                try:
                    imm = int(ins.op_str.split("#")[-1].rstrip("]"), 0)
                except ValueError:
                    continue
                pc = (ins.address + 4) & ~3
                if pc + imm in lit_addrs:
                    print(f"  {ins.address:#010x}  {mn} {ins.op_str}  -> {pc+imm:#x}")
