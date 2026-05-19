"""Identify the RTOS used by Maxwell firmware.

Strategy: search decompressed firmware for string signatures and known
symbol names from the common embedded RTOSes (FreeRTOS, ThreadX, NuttX,
Zephyr, RT-Thread) and Airoha/MediaTek-SDK-specific task wrappers.

Outputs:
  - All matching signatures with offsets
  - All printable strings near RTOS-looking offsets (debug strings often
    name tasks: "MAIN", "IDLE", "BT_TASK", etc.)

Usage: python identify_rtos.py <Maxwell_v1.0.1.74_XBOX_headset.bin>
"""
import lzma, struct, sys, re

if len(sys.argv) < 2:
    print("Usage: python identify_rtos.py <fw.bin>")
    sys.exit(1)

with open(sys.argv[1], "rb") as f:
    raw = f.read()
payload = raw[0x1000:]
fixed = payload[:5] + struct.pack("<Q", 0xFFFFFFFFFFFFFFFF) + payload[13:]
fw = lzma.decompress(fixed, format=lzma.FORMAT_ALONE)
BASE = 0x0801F000
print(f"Decompressed firmware: {len(fw):,} bytes, runtime base {BASE:#x}")

# Categorized signatures. Each tuple: (category, pattern_bytes, note)
SIGNATURES = [
    # FreeRTOS — extremely common in MediaTek/Airoha BT/audio SDKs
    ("FreeRTOS", b"FreeRTOS",                "kernel string"),
    ("FreeRTOS", b"pxCurrentTCB",           "current task TCB pointer"),
    ("FreeRTOS", b"xTaskCreate",            "task creation API"),
    ("FreeRTOS", b"vTaskStartScheduler",    "scheduler start"),
    ("FreeRTOS", b"vTaskDelay",             "delay API"),
    ("FreeRTOS", b"vTaskSwitchContext",     "context switch"),
    ("FreeRTOS", b"xQueueGenericReceive",   "queue receive"),
    ("FreeRTOS", b"xQueueGenericSend",      "queue send"),
    ("FreeRTOS", b"prvIdleTask",            "idle task"),
    ("FreeRTOS", b"uxCriticalNesting",      "critical section counter"),
    ("FreeRTOS", b"xSemaphoreCreate",       "semaphore API"),
    ("FreeRTOS", b"port.c",                 "FreeRTOS port file"),
    ("FreeRTOS", b"queue.c",                "FreeRTOS queue file"),
    ("FreeRTOS", b"tasks.c",                "FreeRTOS tasks file"),
    ("FreeRTOS", b"heap_",                  "FreeRTOS heap implementation"),
    ("FreeRTOS", b"timers.c",               "FreeRTOS timer file"),
    ("FreeRTOS", b"croutine.c",             "FreeRTOS coroutine file"),
    # ThreadX (Microsoft / formerly Express Logic)
    ("ThreadX",  b"ThreadX",                "kernel string"),
    ("ThreadX",  b"tx_thread_",             "thread API prefix"),
    ("ThreadX",  b"_tx_thread_create",      "thread create"),
    ("ThreadX",  b"_tx_initialize",         "init"),
    # NuttX
    ("NuttX",    b"NuttX",                  "kernel string"),
    ("NuttX",    b"task_create",            "task creation"),
    # Zephyr
    ("Zephyr",   b"Zephyr",                 "kernel string"),
    ("Zephyr",   b"k_thread_",              "thread API"),
    # RT-Thread
    ("RT-Thread", b"RT-Thread",             "kernel string"),
    ("RT-Thread", b"rt_thread_create",      "thread create"),
    # MediaTek / Airoha
    ("Airoha-SDK", b"hal_nvic_",            "Airoha HAL NVIC"),
    ("Airoha-SDK", b"hal_gpt_",             "Airoha HAL general-purpose timer"),
    ("Airoha-SDK", b"hal_uart_",            "Airoha HAL UART"),
    ("Airoha-SDK", b"syslog",               "Airoha syslog"),
    ("Airoha-SDK", b"bt_task",              "BT task name"),
    ("Airoha-SDK", b"audio_task",           "audio task name"),
    ("Airoha-SDK", b"main_task",            "main task name"),
    ("Airoha-SDK", b"IoT_SDK_for_BT_Audio", "SDK version banner"),
    ("Airoha-SDK", b"AB1568",               "chip identifier"),
    ("Airoha-SDK", b"AB1565",               "chip identifier (relative)"),
    ("Airoha-SDK", b"audio_anc",            "ANC subsystem"),
    ("Airoha-SDK", b"task_def",             "task definition"),
    ("Airoha-SDK", b"_task",                "any *_task suffix"),
    ("Airoha-SDK", b"sleep_manager",        "sleep manager"),
    ("Airoha-SDK", b"race_cmd",             "RACE command"),
    ("Airoha-SDK", b"nvdm_",                "NVDM API"),
    ("Airoha-SDK", b"bt_sink_srv",          "BT sink service"),
    ("Airoha-SDK", b"app_event",            "app event"),
]

print(f"\n=== RTOS signature scan ===\n")

by_category = {}
for category, pat, note in SIGNATURES:
    locations = []
    start = 0
    while True:
        idx = fw.find(pat, start)
        if idx == -1:
            break
        locations.append(idx)
        start = idx + 1
        if len(locations) > 200:
            break
    if locations:
        by_category.setdefault(category, []).append((pat, note, locations))

for category in ["FreeRTOS", "ThreadX", "NuttX", "Zephyr", "RT-Thread", "Airoha-SDK"]:
    hits = by_category.get(category, [])
    if not hits:
        print(f"--- {category}: NO HITS")
        continue
    total = sum(len(locs) for _, _, locs in hits)
    print(f"--- {category}: {total} total hits across {len(hits)} patterns ---")
    for pat, note, locations in hits:
        first = locations[:3]
        first_str = ", ".join(f"file {h:#x} (runtime {BASE+h:#x})" for h in first)
        more = "" if len(locations) <= 3 else f" ... +{len(locations)-3} more"
        print(f"  {pat!r:40s}  ({note})  {len(locations):4d}x: {first_str}{more}")
    print()

# Pull out strings near the first FreeRTOS hit (if any) — task names typically live there
print("=== Likely task-name strings ===\n")
print("Strings matching common task-name patterns (uppercase words, *_TASK, *_task):\n")

# String extractor: ASCII runs of 4+ chars
ascii_re = re.compile(rb"[\x20-\x7e]{4,}")
task_name_re = re.compile(r"^(?:[A-Z_]+|[a-z]+_task|task_[a-z]+|[A-Z][a-zA-Z0-9_]*Task)$")

# Extract all printable strings and filter
candidates = []
for m in ascii_re.finditer(fw):
    s = m.group().decode('ascii', errors='replace')
    if task_name_re.match(s):
        candidates.append((m.start(), s))

# Deduplicate names
seen = {}
for off, s in candidates:
    if s not in seen:
        seen[s] = off

# Show shorter "likely task name" strings
print(f"Found {len(seen)} distinct candidate task names:\n")
for s, off in sorted(seen.items()):
    if 4 <= len(s) <= 32:
        print(f"  {off:#08x}  {s}")
