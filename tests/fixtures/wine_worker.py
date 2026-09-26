"""Protocol peer for subprocess tests; this does not implement DLSS."""

import json
import os
import signal
import struct
import sys
import time


def read_exact(size):
    data = sys.stdin.buffer.read(size)
    if len(data) != size:
        raise EOFError("Truncated request")
    return data


mode = os.environ.get("TEST_WORKER_MODE", "success")
if mode == "shutdown-hang":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print("ready", flush=True)
    time.sleep(60)
if mode == "hang":
    time.sleep(60)
if sys.argv[2] == "--probe":
    if mode == "error":
        print("NGX initialization failed", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"available": True, "multi_frame_count_max": 1}))
    sys.exit(0)

if mode == "eof":
    sys.exit(1)
magic, width, height, count, generated = struct.unpack("<5I", read_exact(20))
assert magic == 0x31534746
sys.stdout.buffer.write(struct.pack("<4I", 0x31524746, 0, 1, 0))
sys.stdout.buffer.flush()
while True:
    header = sys.stdin.buffer.read(32)
    if not header:
        break
    magic, index, reset, _, numerator, denominator = struct.unpack("<4I2q", header)
    assert magic == 0x31464746 and denominator > 0
    color = read_exact(width * height * 4)
    motion = read_exact(width * height * 4)
    sys.stdout.buffer.write(struct.pack("<4I", 0x314F4746, 0, 1, 0))
    sys.stdout.buffer.write(color)
    sys.stdout.buffer.flush()
