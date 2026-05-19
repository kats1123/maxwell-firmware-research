"""For each candidate task name, find every literal-pool reference and
disassemble around it to identify the task-creation call site.

We're trying to build a complete inventory of FreeRTOS tasks: name,
creator function, priority, stack size (if recoverable from instruction
context).

Usage: python find_all_task_creators.py <fw.bin>
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

# Task names: file offset -> string
TASKS = {
    0x0eb7ba: "DTM_TASK",
    0x0eb7c3: "DPR_TASK",
    0x0eb7cc: "DAV_TASK",
    0x0eb7d5: "DHP_TASK",
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

print(f"=== Searching for literal-pool references to each task name ===\n")

for off, name in TASKS.items():
    addr = runtime(off)
    addr_bytes = struct.pack("<I", addr)
    # Find aligned occurrences
    refs = []
    s = 0
    while True:
        idx = fw.find(addr_bytes, s)
        if idx == -1: break
        if idx % 4 == 0:
            refs.append(idx)
        s = idx + 1
    print(f"-- {name!r} @ {addr:#010x}: {len(refs)} literal-pool refs")
    for r in refs:
        # Find LDR that loads this literal
        scan_start = max(0, r - 1024)
        chunk = fw[scan_start:r+4]
        try:
            insns = list(md.disasm(chunk, runtime(scan_start)))
        except Exception:
            insns = []
        for ins in insns:
            if not ins.mnemonic.lower().startswith("ldr"):
                continue
            op = ins.op_str.lower()
            m = re.match(r"r\d+,\s*\[pc,\s*#?(0x[0-9a-f]+|\d+)\]", op)
            if not m:
                continue
            imm = int(m.group(1), 0)
            pc = ins.address + 4
            lit_addr = (pc & ~3) + imm
            if lit_addr == addr:
                # Found the LDR. Now disassemble the next ~16 instructions to
                # see what BL we hit (likely the task-create call).
                next_chunk = fw[file_off(ins.address):file_off(ins.address) + 64]
                next_ins = list(md.disasm(next_chunk, ins.address))
                bl_target = None
                for ni in next_ins[:20]:
                    if ni.mnemonic.lower() in ("bl", "bl.w"):
                        # Resolve BL target
                        try:
                            bl_target = int(ni.op_str.lstrip("#"), 0)
                            break
                        except: pass
                print(f"     LDR @ {ins.address:#010x} loads name; next BL -> {bl_target:#010x}" if bl_target else f"     LDR @ {ins.address:#010x} loads name; next BL not found")
                break
    print()
