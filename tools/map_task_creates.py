"""For each task name, dump the disassembly window around its LDR reference
so we can see (a) the task entry-point function pointer and (b) priority
and stack size args to xTaskCreate.

Usage: python map_task_creates.py <fw.bin>
"""
import lzma, struct, sys, re
import capstone

md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
md.detail = True

with open(sys.argv[1], "rb") as f:
    raw = f.read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000

def runtime(off): return BASE + off
def file_off(addr):
    if addr < BASE: return None
    o = addr - BASE
    if o >= len(fw): return None
    return o

TASKS = {
    0x269350: "charger_task",
    0x26f5f6: "audio_codec_task",
    0x26ff80: "Linear_task",
    0x27013c: "battery_charger_task",
    0x271010: "ui_shell_task",
    0x272e10: "bt_task",
    0x272e30: "controler_test_task",
    0x272e44: "UI_realtime",
    0x272e50: "race command",
    0x272e5d: "AM_Task",
}

def resolve_ldr_pc_rel(ins):
    op = ins.op_str.lower()
    m = re.match(r"(r\d+),\s*\[pc,\s*#?(0x[0-9a-f]+|\d+)\]", op)
    if not m:
        return None, None
    reg = m.group(1)
    imm = int(m.group(2), 0)
    pc = ins.address + 4
    lit_addr = (pc & ~3) + imm
    return reg, lit_addr

def read_word_at(addr):
    fo = file_off(addr)
    if fo is None or fo + 4 > len(fw): return None
    return struct.unpack("<I", fw[fo:fo+4])[0]

for off, name in TASKS.items():
    addr = runtime(off)
    addr_bytes = struct.pack("<I", addr)
    refs = []
    s = 0
    while True:
        idx = fw.find(addr_bytes, s)
        if idx == -1: break
        if idx % 4 == 0:
            refs.append(idx)
        s = idx + 1
    print(f"\n{'='*78}\n=== {name!r} @ {addr:#010x} — {len(refs)} literal-pool refs\n{'='*78}")
    for r in refs:
        # Find LDR within previous 1024 bytes that loads addr
        scan_start = max(0, r - 1024)
        chunk = fw[scan_start:r+4]
        try:
            insns = list(md.disasm(chunk, runtime(scan_start)))
        except Exception:
            continue
        ldr_addr = None
        ldr_reg = None
        for ins in insns:
            if not ins.mnemonic.lower().startswith("ldr"):
                continue
            reg, lit_addr = resolve_ldr_pc_rel(ins)
            if lit_addr == addr:
                ldr_addr = ins.address
                ldr_reg = reg
                break
        if ldr_addr is None:
            print(f"  ref @ file {r:#x} but no LDR found")
            continue
        # Now disassemble from ldr_addr forward up to next BL or 24 instructions
        ldr_fo = file_off(ldr_addr)
        forward = fw[ldr_fo:ldr_fo + 96]
        finsns = list(md.disasm(forward, ldr_addr))
        print(f"\n  --- LDR @ {ldr_addr:#010x}: {ldr_reg} = task-name ptr ---")
        # Track register states
        regs = {}
        for fi in finsns[:24]:
            mn = fi.mnemonic.lower()
            op = fi.op_str.lower()
            # Track LDR pc-rel literals
            if mn.startswith("ldr"):
                reg, la = resolve_ldr_pc_rel(fi)
                if reg and la is not None:
                    val = read_word_at(la)
                    if val is not None:
                        regs[reg] = ('pcrel', val, la)
                        print(f"    {fi.address:#010x}  {mn:8s} {op}  ; -> [{la:#010x}] = {val:#010x}")
                        continue
            elif mn in ("movs", "mov", "movw"):
                m = re.match(r"(r\d+),\s*#?(-?(?:0x[0-9a-f]+|\d+))", op)
                if m:
                    reg = m.group(1)
                    val = int(m.group(2), 0)
                    regs[reg] = ('imm', val, None)
                    print(f"    {fi.address:#010x}  {mn:8s} {op}  ; {reg}={val:#x}")
                    continue
            elif mn in ("bl", "bl.w"):
                try:
                    bl_target = int(fi.op_str.lstrip("#"), 0)
                except:
                    bl_target = None
                print(f"    {fi.address:#010x}  {mn:8s} {op}  ; BL target={bl_target}")
                # Show register state at BL
                regs_str = ", ".join(f"{k}={v[1]:#x}" for k,v in sorted(regs.items()))
                print(f"      [regs at BL: {regs_str}]")
                break
            print(f"    {fi.address:#010x}  {mn:8s} {op}")
