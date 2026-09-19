"""Strict parser for vkanim.py output (gate 1: setup, gate 2: animation)."""
import re

MESA_LINE = re.compile(r'^(MESA|TU|turnip)\b.*?:\s*(info|warning|debug)\b', re.I)
FRAME = re.compile(r'F (\d+) fb=(\d+) render_ms=([0-9.]+) copy_ms=([0-9.]+) flip_ms=([0-9.]+) t_ms=([0-9.]+)')

class AnimLog:
    def __init__(self, frames, base_fb):
        self.start_fb = base_fb; self.existing = None; self.frames = frames; self.state = 'start'; self.gates = 0; self.msgs = []; self.held = None
        self.fb = {}; self.done = []; self.summary = None
    def feed(self, line):
        if self.held is not None: raise RuntimeError('output after HELD: ' + line)
        if line.startswith('STOP:'): raise RuntimeError('worker error: ' + line)
        if line.startswith('VKMSG ') or MESA_LINE.search(line):
            if self.gates < 1: raise RuntimeError('driver output before gate')
            if re.match(r'VKMSG sev=0x1000\b', line): raise RuntimeError('driver error: ' + line)
            self.msgs.append(line); return 'msg'
        s = self.state
        def to(new, need_gates=0):
            if self.gates < need_gates: raise RuntimeError('progressed without gate: ' + line)
            self.state = new
        if s == 'start' and line == 'INHERITED_DRM_FILE planes 95/128 on FB%d, mode active; frames=%d' % (self.start_fb, self.frames): to('fbs'); return 'step'
        if s == 'fbs' and line.startswith('EXISTING_FBS '):
            self.existing = set(__import__('json').loads(line[13:]))
            if self.start_fb not in self.existing: raise RuntimeError('base fb not in existing list')
            to('inherited'); return 'step'
        if s == 'inherited' and line == 'READY_FOR_SETUP': to('ready1'); return 'gate1'
        if s == 'ready1' and line.startswith('VULKAN_READY '): to('vk', 1); return 'step'
        m = re.fullmatch(r'BUFFER ([AB]) fb=(\d+) handle=(\d+)', line)
        if m and ((s == 'vk' and m.group(1) == 'A') or (s == 'bufA' and m.group(1) == 'B')):
            fb = int(m.group(2))
            if fb in self.existing or fb == 0 or fb in self.fb.values(): raise RuntimeError('bad fb ' + line)
            self.fb[m.group(1)] = fb; to('buf' + m.group(1), 1); return 'buffer'
        if s == 'bufB' and line == 'TEST_ONLY_PASS A B BASE': to('tested', 1); return 'step'
        if s == 'tested' and line == 'READY_FOR_ANIMATION': to('ready2', 1); return 'gate2'
        if s == 'ready2' and line == 'ANIMATION_ENTER': to('anim', 2); return 'enter'
        m = FRAME.fullmatch(line)
        if m and s == 'anim':
            k, fb = int(m.group(1)), int(m.group(2))
            if k != len(self.done) + 1 or k > self.frames or fb != self.fb['B' if k % 2 else 'A']: raise RuntimeError('frame sequence: ' + line)
            self.done.append(tuple(float(x) for x in m.groups()[2:])); return 'frame'
        m = re.fullmatch(r'ANIMATION_SUMMARY frames=(\d+) seconds=([0-9.]+) fps=([0-9.]+) max_frame_ms=([0-9.]+)', line)
        if m and s == 'anim':
            if int(m.group(1)) != self.frames or len(self.done) != self.frames: raise RuntimeError('summary before all frames')
            self.summary = {'frames': self.frames, 'seconds': float(m.group(2)), 'fps': float(m.group(3)), 'max_frame_ms': float(m.group(4))}
            to('summary', 2); return 'summary'
        if s == 'summary' and line == 'RETURNED_BASE': to('returned', 2); return 'returned'
        if s == 'returned' and line == 'VK_DESTROYED': to('destroyed', 2); return 'destroyed'
        if s == 'destroyed' and line.startswith('HELD ') and line.endswith('reason=ANIMATION_DONE_BASE_RETAINED'):
            self.held = line; return 'held'
        if line.startswith('HELD '): self.held = line; raise RuntimeError('worker stopped: ' + line)
        raise RuntimeError('unexpected line in state %s: %s' % (s, line))
