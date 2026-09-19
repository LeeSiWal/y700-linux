"""Strict parser for vkcompute.py output (two gates). Driver messages allowed below error severity."""
import json, re

MESA_LINE = re.compile(r'^(MESA|TU|turnip)\b.*?:\s*(info|warning|debug)\b', re.I)
ORDER = ['READY_FOR_COMPUTE', 'ICD_LOADED', 'INSTANCE_CREATED', 'MESSENGER_CREATED', 'PHYSICAL_DEVICE', 'QUEUE_FAMILY',
         'DEVICE_CREATED', 'BUFFER_READY', 'PIPELINE_CREATED', 'COMMANDS_RECORDED', 'SUBMITTED', 'FENCE_SIGNALED',
         'VERIFY', 'VERIFY_PASS', 'READY_FOR_DESTROY', 'DEVICE_DESTROYED', 'INSTANCE_DESTROYED', 'HELD']
GATE_AFTER = {'READY_FOR_COMPUTE': 1, 'READY_FOR_DESTROY': 2}

class ComputeLog:
    def __init__(self):
        self.stage = 0; self.gates = 0; self.msgs = []; self.errors = []; self.held = None
        self.fence_ms = None; self.verify = None; self.device = None; self.queue = None; self.buffer = None
    def expect(self, tag):
        if self.stage >= len(ORDER) or ORDER[self.stage] != tag:
            raise RuntimeError('out of order: %s (expected %s)' % (tag, ORDER[self.stage] if self.stage < len(ORDER) else 'end'))
        needed = 0
        for t in ORDER[:self.stage]:
            needed = max(needed, GATE_AFTER.get(t, 0))
        if self.gates < needed: raise RuntimeError('progressed without gate: ' + tag)
        self.stage += 1
    def feed(self, line):
        if self.held is not None: raise RuntimeError('output after HELD: ' + line)
        if line.startswith('STOP:'): raise RuntimeError('worker error: ' + line)
        if line.startswith('VKMSG '):
            if self.gates < 1: raise RuntimeError('driver message before gate')
            self.msgs.append(line)
            if re.match(r'VKMSG sev=0x1000\b', line): raise RuntimeError('driver error: ' + line)
            return 'vkmsg'
        if MESA_LINE.search(line):
            if self.gates < 1: raise RuntimeError('driver output before gate')
            self.msgs.append(line); return 'mesa'
        tag = line.split(' ', 1)[0]
        if tag == 'HELD':
            self.held = line
            if self.stage != len(ORDER) - 1 or not line.endswith('reason=COMPUTE_DONE_DESTROYED'):
                raise RuntimeError('worker stopped: ' + line)
        self.expect(tag)
        rest = line[len(tag):].strip()
        if tag == 'PHYSICAL_DEVICE':
            self.device = json.loads(rest)
            if self.device.get('deviceName') != 'Adreno (TM) 840': raise RuntimeError('wrong device')
        if tag == 'QUEUE_FAMILY': self.queue = rest
        if tag == 'BUFFER_READY': self.buffer = rest
        if tag == 'FENCE_SIGNALED': self.fence_ms = float(re.fullmatch(r'ms=([0-9.]+)', rest).group(1))
        if tag == 'VERIFY':
            m = re.match(r'written_ok=(\d+)/(\d+) untouched=(\d+)/(\d+)', rest)
            if not m or m.group(1) != m.group(2) or m.group(3) != m.group(4): raise RuntimeError('verify mismatch: ' + rest)
            self.verify = rest
        return {'READY_FOR_COMPUTE': 'gate1', 'READY_FOR_DESTROY': 'gate2', 'VERIFY_PASS': 'verified',
                'INSTANCE_DESTROYED': 'destroyed', 'HELD': 'held'}.get(tag, 'step')
