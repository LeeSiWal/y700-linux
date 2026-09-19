"""Pure checks for the KGSL load trial; unit-testable without a device."""
import re

UNDERRUN = re.compile(r'intf:(\d+)\s+vsync:\s+\d+\s+underrun:\s+(\d+)\s+mode:\s+video')
# Any GPU-side error or fault record stops further steps (modules stay loaded; nothing is unloaded).
GPU_FAULT = re.compile(r'(?i)(kgsl|adreno|gmu|gen8|zap|coresight|sysstats|msm_performance|devfreq|governor)'
                       r'.*(fail|error|unable|couldn\'t|cannot|timed? ?out|fault|invalid|denied|not found|bad )')
FW_REQUEST = re.compile(r'gen80200_(sqe\.fw|aqe\.fw|gmu\.bin|zap\.mbn)')


def payload(line):
    return re.sub(r'^\[\s*[0-9.]+\]\s+\[\s*[TC]\d+\]\s?', '', line)


def underruns(text):
    found = {int(i): int(n) for i, n in UNDERRUN.findall(text)}
    if set(found) != {1, 2}:
        raise RuntimeError('unexpected encoder counter format')
    return found


def new_lines(before, after):
    old = set(before.splitlines())
    return [l for l in after.splitlines() if l not in old]


def gpu_faults(before, after):
    return [l for l in new_lines(before, after) if GPU_FAULT.search(payload(l))]


def firmware_mentions(before, after):
    return [l for l in new_lines(before, after) if FW_REQUEST.search(l)]
