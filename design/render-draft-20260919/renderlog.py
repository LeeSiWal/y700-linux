"""Strict parser for vkrender.py output (two gates: render, commit)."""
import json, re

MESA_LINE = re.compile(r'^(MESA|TU|turnip)\b.*?:\s*(info|warning|debug)\b', re.I)
ORDER = ['INHERITED_DRM_FILE', 'READY_FOR_RENDER', 'ICD_LOADED', 'INSTANCE_CREATED', 'MESSENGER_CREATED', 'PHYSICAL_DEVICE',
         'QUEUE_FAMILY', 'DEVICE_CREATED', 'BUFFER_READY', 'PIPELINE_CREATED', 'COMMANDS_RECORDED', 'SUBMITTED',
         'FENCE_SIGNALED', 'VERIFY', 'VERIFY_PASS', 'DUMB_COPIED', 'VK_DESTROYED', 'FRAMEBUFFER', 'TEST_ONLY_PASS',
         'READY_FOR_COMMIT', 'COMMIT_ENTER', 'COMMIT_RETURNED', 'HELD']
GATE_AFTER = {'READY_FOR_RENDER': 1, 'READY_FOR_COMMIT': 2}

class RenderLog:
    def __init__(self):
        self.stage = 0; self.gates = 0; self.msgs = []; self.held = None
        self.fence_ms = None; self.verify = None; self.fb = None; self.copied = None
    def expect(self, tag):
        if self.stage >= len(ORDER) or ORDER[self.stage] != tag:
            raise RuntimeError('out of order: %s (expected %s)' % (tag, ORDER[self.stage] if self.stage < len(ORDER) else 'end'))
        needed = max([GATE_AFTER.get(t, 0) for t in ORDER[:self.stage]] + [0])
        if self.gates < needed: raise RuntimeError('progressed without gate: ' + tag)
        self.stage += 1
    def feed(self, line):
        if self.held is not None: raise RuntimeError('output after HELD: ' + line)
        if line.startswith('STOP:'): raise RuntimeError('worker error: ' + line)
        if line.startswith('VKMSG '):
            if self.gates < 1: raise RuntimeError('driver message before gate')
            if re.match(r'VKMSG sev=0x1000\b', line): raise RuntimeError('driver error: ' + line)
            self.msgs.append(line); return 'vkmsg'
        if MESA_LINE.search(line):
            if self.gates < 1: raise RuntimeError('driver output before gate')
            self.msgs.append(line); return 'mesa'
        tag = line.split(' ', 1)[0]; rest = line[len(tag):].strip()
        if tag == 'HELD':
            self.held = line
            if self.stage != len(ORDER) - 1 or not line.endswith('reason=GPU_FRAME_DISPLAYED_VISUAL_CONFIRMATION_REQUIRED'):
                raise RuntimeError('worker stopped: ' + line)
        self.expect(tag)
        if tag == 'PHYSICAL_DEVICE' and json.loads(rest).get('deviceName') != 'Adreno (TM) 840': raise RuntimeError('wrong device')
        if tag == 'FENCE_SIGNALED': self.fence_ms = float(re.fullmatch(r'ms=([0-9.]+)', rest).group(1))
        if tag == 'VERIFY':
            m = re.match(r'sampled=(\d+) mismatches=(\d+) sentinel_words=(\d+)', rest)
            if not m or int(m.group(1)) < 60000 or m.group(2) != '0' or m.group(3) != '0': raise RuntimeError('verify failed: ' + rest)
            self.verify = rest
        if tag == 'DUMB_COPIED':
            if not rest.endswith('identical=True'): raise RuntimeError('copy mismatch: ' + rest)
            self.copied = rest
        if tag == 'FRAMEBUFFER':
            m = re.match(r'id=(\d+) 1904x3040 pitch=7680 XR24 linear$', rest)
            if not m or int(m.group(1)) in (0, 78, 335, 341, 342): raise RuntimeError('framebuffer: ' + rest)
            self.fb = int(m.group(1))
        if tag == 'COMMIT_RETURNED' and rest != 'fb=%d' % self.fb: raise RuntimeError('commit fb mismatch: ' + rest)
        return {'READY_FOR_RENDER': 'gate1', 'READY_FOR_COMMIT': 'gate2', 'COMMIT_ENTER': 'commit', 'COMMIT_RETURNED': 'returned',
                'VERIFY_PASS': 'verified', 'HELD': 'held'}.get(tag, 'step')
