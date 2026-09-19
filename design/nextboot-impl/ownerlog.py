"""Strict parser for display-owner.py output."""
import json, re

STEPS = ['FIRST_DRM_OPEN', 'TOPOLOGY', 'SPLASH_MODE', 'COMMIT_A_TEST_ONLY_PASS', 'READY_FOR_COMMIT_A', 'COMMIT_A_ENTER',
         'COMMIT_A_RETURNED', 'COMMIT_B_TEST_ONLY_PASS', 'READY_FOR_COMMIT_B', 'COMMIT_B_ENTER', 'COMMIT_B_RETURNED', 'OWNER_READY', 'HELD']
GATES = {'READY_FOR_COMMIT_A': 1, 'READY_FOR_COMMIT_B': 2}
REVIEWED_TOPOLOGY = {'connector': 69, 'crtc': 205, 'left': 95, 'right': 128, 'planes': 20}

class OwnerLog:
    def __init__(self):
        self.i = 0; self.gates = 0; self.info = {}; self.held = None
    def feed(self, line):
        if self.held is not None: raise RuntimeError('output after HELD: ' + line)
        if line.startswith('STOP:'): raise RuntimeError('owner error: ' + line)
        tag = line.split(' ', 1)[0]; rest = line[len(tag):].strip()
        if tag == 'HELD':
            self.held = line
            if not (self.i == len(STEPS) - 1 and line.endswith('reason=DISPLAY_OWNER_HOLDING')): raise RuntimeError('owner stopped: ' + line)
        if self.i >= len(STEPS) or STEPS[self.i] != tag: raise RuntimeError('out of order: %s (expected %s)' % (line, STEPS[self.i] if self.i < len(STEPS) else 'end'))
        need = max([GATES.get(s, 0) for s in STEPS[:self.i]] + [0])
        if self.gates < need: raise RuntimeError('progressed without gate: ' + line)
        self.i += 1
        if tag == 'FIRST_DRM_OPEN': self.info['fd'] = int(re.match(r'fd=(\d+)', rest).group(1))
        if tag == 'TOPOLOGY':
            topo = json.loads(rest)
            if topo != REVIEWED_TOPOLOGY: raise RuntimeError('topology differs: ' + rest)
            self.info['topology'] = topo
        if tag == 'OWNER_READY':
            info = json.loads(rest)
            if info['fd'] != self.info['fd'] or info['topology'] != self.info['topology']: raise RuntimeError('owner summary inconsistent')
            self.info.update(info)
        return {'READY_FOR_COMMIT_A': 'gate1', 'READY_FOR_COMMIT_B': 'gate2', 'COMMIT_A_ENTER': 'enterA', 'COMMIT_A_RETURNED': 'returnedA',
                'COMMIT_B_ENTER': 'enterB', 'COMMIT_B_RETURNED': 'returnedB', 'OWNER_READY': 'ready', 'HELD': 'held'}.get(tag, 'step')
