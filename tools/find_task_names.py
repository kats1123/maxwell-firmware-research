"""Find ALL FreeRTOS task names and the task_def_create call sites.

Strategy:
1. Find all printable strings that look like task names (end in _TASK,
   _task, Task, or appear near known task strings).
2. Find the format string "xCreate task %s, pri %d" — every BL to a
   function that loads this string with a `task_name` arg is a task creator.
3. List every BL site that loads this format string via adjacent literal pool
   so we know who creates which task.

Usage: python find_task_names.py <fw.bin>
"""
import lzma, struct, sys, re
import capstone

md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
md.detail = True

if len(sys.argv) < 2:
    print("Usage: python find_task_names.py <fw.bin>")
    sys.exit(1)

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


# === Step 1: find candidate task-name strings ===

ascii_re = re.compile(rb"[\x20-\x7e]{4,64}\x00")
# More relaxed task-name patterns
patterns = [
    re.compile(r".*_TASK$"),
    re.compile(r".*_task$"),
    re.compile(r".*_Task$"),
    re.compile(r".*Task$"),
    re.compile(r"^task[_ ].*"),
    re.compile(r"^[a-zA-Z][a-zA-Z0-9_]+ task$"),
    re.compile(r"^race command$"),
    re.compile(r"^UI_realtime$"),
    re.compile(r"^bt_task$"),
    re.compile(r"^AM_Task$"),
]

task_strings = {}  # off -> name
for m in ascii_re.finditer(fw):
    s = m.group()[:-1].decode("ascii", errors="replace")
    if any(p.match(s) for p in patterns):
        task_strings[m.start()] = s

print("=== Candidate task-name strings ===\n")
for off in sorted(task_strings.keys()):
    print(f"  file {off:#08x} (runtime {runtime(off):#010x})  {task_strings[off]!r}")

# === Step 2: find the task-create format string ===
fmt_off = fw.find(b"xCreate task %s, pri %d\x00")
print(f"\n=== Task creator format string at file {fmt_off:#x} (runtime {runtime(fmt_off):#010x}) ===")

# === Step 3: find all literal-pool references to that runtime address ===
target = runtime(fmt_off)
target_bytes = struct.pack("<I", target)
print(f"\nSearching for literal-pool references to {target:#x} (bytes {target_bytes.hex()})...")

lit_refs = []
start = 0
while True:
    idx = fw.find(target_bytes, start)
    if idx == -1:
        break
    # Must be 4-byte aligned for literal pool
    if idx % 4 == 0:
        lit_refs.append(idx)
    start = idx + 1

print(f"Found {len(lit_refs)} aligned literal-pool references")
for lr in lit_refs:
    print(f"  literal at file {lr:#08x} (runtime {runtime(lr):#010x})")

# For each literal-pool entry, look backwards for the LDR that loads it.
# In Thumb-2, an LDR pc-relative is `LDR rN, [pc, #imm]` and the immediate
# is computed as (PC+4) aligned-down to 4-byte boundary + imm.
# For each LDR site, capture the surrounding context.

print("\n=== LDR sites that load the format-string pointer ===\n")
ldr_sites = []
for lr in lit_refs:
    # The LDR that references this literal can be up to ~1020 bytes earlier.
    # Search 2-byte-aligned instructions in the 1024 bytes before.
    scan_start = max(0, lr - 1024)
    chunk = fw[scan_start:lr+4]
    insns = list(md.disasm(chunk, runtime(scan_start)))
    for ins in insns:
        if ins.mnemonic.lower().startswith("ldr"):
            # Resolve pc-relative
            op = ins.op_str.lower()
            m = re.match(r"r\d+,\s*\[pc,\s*#?(0x[0-9a-f]+|\d+)\]", op)
            if not m:
                continue
            imm = int(m.group(1), 0)
            pc = ins.address + 4
            lit_addr = (pc & ~3) + imm
            if lit_addr == runtime(lr):
                ldr_sites.append((ins.address, lr))
                break

for ldr_addr, lit_off in ldr_sites:
    print(f"  LDR @ {ldr_addr:#010x} -> literal @ file {lit_off:#x} -> format string")

# Now: each LDR site loads the format string. Walk forward to find the BL
# (presumably to printf/syslog). The function that wraps each task-create
# call probably loads the task NAME pointer before this LDR. So just before
# the LDR, look for "LDR rN, [pc, #imm]" that loads a task-name pointer
# (i.e. an address that lies inside the firmware and points to a printable
# string).

print("\n=== Task-name pointers loaded just before each format-string LDR ===\n")

for ldr_addr, _ in ldr_sites:
    # Walk back ~64 bytes, look for LDR instructions
    fo = file_off(ldr_addr)
    if fo is None: continue
    scan_start = max(0, fo - 128)
    chunk = fw[scan_start:fo+4]
    insns = list(md.disasm(chunk, runtime(scan_start)))
    # Collect all pc-relative LDR targets
    candidates_here = []
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
        # Read the pointer at this literal
        lit_fo = file_off(lit_addr)
        if lit_fo is None or lit_fo + 4 > len(fw): continue
        ptr = struct.unpack("<I", fw[lit_fo:lit_fo+4])[0]
        ptr_fo = file_off(ptr)
        if ptr_fo is None: continue
        # Check if it points to a printable string
        end = ptr_fo
        while end < len(fw) and end < ptr_fo + 64 and 0x20 <= fw[end] <= 0x7E:
            end += 1
        if end > ptr_fo + 2 and end < len(fw) and fw[end] == 0:
            s = fw[ptr_fo:end].decode('ascii', errors='replace')
            candidates_here.append((ins.address, ptr, s))
    if candidates_here:
        print(f"-- Caller @ {ldr_addr:#010x}:")
        for caddr, ptr, s in candidates_here[-4:]:
            print(f"     LDR @ {caddr:#010x} -> {ptr:#010x} = {s!r}")
