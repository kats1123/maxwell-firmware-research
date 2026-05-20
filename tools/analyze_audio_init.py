"""Analyze FUN_0x081DE120 — the boot audio-routing init.

Goal: map every DSP-coefficient write (via FUN_0x081DDF54) and which
ones are source-conditional (inside a `cmp state,#0xA` branch).

FUN_0x081DDF54(outer_id, value, channel):
  outer_id  — DSP coefficient/register group
  value     — the value written
  channel   — 0x38 = LEFT, 0x39 = RIGHT

This tells us what audio config differs between USB-C and wireless.

Usage: python analyze_audio_init.py <fw.bin>
"""
import lzma, struct, sys, capstone, re

md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)

with open(sys.argv[1], "rb") as f:
    raw = f.read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000
fo = lambda a: a - BASE

FN_START = 0x081DE120
FN_END = 0x081DE32A
DSP_WRITE = 0x081DDF54
GET_STATE = 0x0817B2F4
NVDM_READ = 0x0814FEAC

chunk = fw[fo(FN_START):fo(FN_END) + 8]
insns = list(md.disasm(chunk, FN_START))

# Track register immediate values so we can resolve FUN_0x081DDF54 args
regs = {}
chan_name = {0x38: "LEFT ", 0x39: "RIGHT"}

print(f"=== FUN_0x081DE120 audio init — DSP writes & source branches ===\n")
print(f"DSP write fn = FUN_0x081DDF54(r0=outer_id, r1=value, r2=channel)")
print(f"channel 0x38=LEFT, 0x39=RIGHT\n")

# First pass: find the source-state cmp branches
print("--- source-conditional branches (cmp against state) ---")
for ins in insns:
    mn = ins.mnemonic.lower()
    if mn == "cmp" and ("r7" in ins.op_str.lower() or "r0" in ins.op_str.lower()):
        # is the next instruction a branch?
        print(f"  {ins.address:#010x}  {mn} {ins.op_str}")
print()

# Second pass: walk through, track regs, annotate every DSP write and NVDM read
print("--- full instruction walk (DSP writes + NVDM reads + branches annotated) ---")
regs = {}
for ins in insns:
    if ins.address >= FN_END + 8: break
    mn = ins.mnemonic.lower()
    op = ins.op_str.lower()
    # Track simple immediate loads
    m = re.match(r"(r\d+|sb|sl|r8),\s*#(-?(?:0x[0-9a-f]+|\d+))$", op.replace(" ", ""))
    if mn in ("movs", "mov", "movw", "mov.w") and m:
        regs[m.group(1)] = int(m.group(2), 0)
    elif mn in ("uxtb",):
        # uxtb rX, rY — value becomes unknown-ish, clear
        m2 = re.match(r"(r\d+),", op)
        if m2 and m2.group(1) in regs: del regs[m2.group(1)]

    note = ""
    if mn in ("bl", "bl.w"):
        try:
            tgt = int(ins.op_str.lstrip("#"), 0)
        except:
            tgt = None
        if tgt == DSP_WRITE:
            o = regs.get("r0")
            v = regs.get("r1")
            c = regs.get("r2")
            cn = chan_name.get(c, hex(c) if c is not None else "?")
            os_ = hex(o) if o is not None else "?"
            vs = hex(v) if v is not None else "?"
            note = f"   *** DSP WRITE: coeff={os_} {cn} = {vs} ***"
        elif tgt == GET_STATE:
            note = "   <-- get source state (NVDM 0xF702)"
        elif tgt == NVDM_READ:
            o = regs.get("r0")
            note = f"   <-- nvdm_read(key={hex(o) if o else '?'})"
        else:
            note = f"   (call {tgt:#x})" if tgt else ""
    elif mn in ("cmp",) and ("r7" in op):
        note = "   <== SOURCE-STATE CHECK"
    elif mn.startswith("b") and mn not in ("bl","bl.w","bic","bfi","bfc"):
        note = "   (branch)"

    # Only print interesting lines
    if note:
        print(f"  {ins.address:#010x}  {mn:8s} {ins.op_str}{note}")
