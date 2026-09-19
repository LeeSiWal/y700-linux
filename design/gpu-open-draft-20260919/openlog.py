"""Strict parser for kgsl-open.py output. Deviations STOP; nothing is retried."""
import json, re

CHIP_ID = 0x44050a01
OPEN_RET = re.compile(r'OPEN_RETURNED fd=(\d+) ms=([0-9.]+)')


class OpenLog:
    def __init__(self):
        self.ready = self.go_sent = self.entered = False
        self.open_ms = None; self.devinfo = None; self.held = None

    def feed(self, line):
        if self.held is not None: raise RuntimeError('output after HELD: ' + line)
        if line == 'READY_FOR_OPEN':
            if self.ready: raise RuntimeError('duplicate ready')
            self.ready = True; return 'ready'
        if line == 'OPEN_ENTER':
            if not self.go_sent or self.entered: raise RuntimeError('open without gate')
            self.entered = True; return 'enter'
        m = OPEN_RET.fullmatch(line)
        if m:
            if not self.entered or self.open_ms is not None: raise RuntimeError('unexpected open return')
            self.open_ms = float(m.group(2)); return 'opened'
        if line.startswith('DEVINFO '):
            if self.open_ms is None or self.devinfo is not None: raise RuntimeError('devinfo out of order')
            info = json.loads(line[8:])
            if info.get('chip_id') != CHIP_ID: raise RuntimeError('chip_id mismatch: ' + hex(info.get('chip_id', 0)))
            self.devinfo = info; return 'devinfo'
        if line.startswith('HELD '):
            self.held = line
            if not (self.devinfo is not None and line.endswith('reason=KGSL_FD_RETAINED')):
                raise RuntimeError('worker stopped: ' + line)
            return 'held'
        if line.startswith('STOP:'): raise RuntimeError('worker error: ' + line)
        raise RuntimeError('unexpected worker line: ' + line)
