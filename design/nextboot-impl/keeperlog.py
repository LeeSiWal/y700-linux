"""Strict parser for gpu-keeper.py output."""
import json, re
CHIP_ID = 0x44050a31

class KeeperLog:
    def __init__(self):
        self.stage = 0; self.go_sent = False; self.open_ms = None; self.devinfo = None; self.fd = None; self.held = None
    def feed(self, line):
        if self.held is not None: raise RuntimeError('output after HELD: ' + line)
        if line.startswith('STOP:'): raise RuntimeError('keeper error: ' + line)
        if line == 'READY_FOR_OPEN' and self.stage == 0: self.stage = 1; return 'ready'
        if line == 'OPEN_ENTER' and self.stage == 1 and self.go_sent: self.stage = 2; return 'enter'
        m = re.fullmatch(r'OPEN_RETURNED fd=(\d+) ms=([0-9.]+)', line)
        if m and self.stage == 2: self.fd, self.open_ms = int(m.group(1)), float(m.group(2)); self.stage = 3; return 'opened'
        if line.startswith('DEVINFO ') and self.stage == 3:
            d = json.loads(line[8:])
            if d.get('chip_id') != CHIP_ID: raise RuntimeError('chip id %r' % d.get('chip_id'))
            self.devinfo = d; self.stage = 4; return 'devinfo'
        if line.startswith('HELD '):
            self.held = line
            if self.stage == 4 and line.endswith('reason=KGSL_FD_RETAINED'): return 'held'
            raise RuntimeError('keeper stopped: ' + line)
        raise RuntimeError('unexpected keeper line in stage %d: %s' % (self.stage, line))
