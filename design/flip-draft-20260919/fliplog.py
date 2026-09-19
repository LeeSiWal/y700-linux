"""Strict parser for flip-held worker output. Any deviation is a STOP; no retry."""
import re

STEPS = 8
CONFIRMED_FB = 335
BUFFER = re.compile(r'BUFFER ([AB]) fb=(\d+) handle=(\d+) readback_pixels=5788160')
ENTER = re.compile(r'FLIP_ENTER (A|B|RETURN_FB335) fb=(\d+) user=(\d+)')
DONE = re.compile(r'FLIP_DONE (A|B|RETURN_FB335) fb=(\d+) seq=(\d+) ts=(\d+)\.(\d{6}) ioctl_to_event_ms=([0-9.]+)')


class FlipLog:
    def __init__(self):
        self.fbs = {}; self.ready = False; self.go_sent = False
        self.entered = None; self.done = []; self.returned = False; self.held = None

    def expected(self, k):
        """Step k (1-based): A,B alternating for STEPS, then return to FB335."""
        if k <= STEPS:
            label = 'A' if k % 2 else 'B'
            return label, self.fbs[label]
        return 'RETURN_FB335', CONFIRMED_FB

    def feed(self, line):
        """Returns an event name for the supervisor, or None. Raises on any deviation."""
        if self.held is not None:
            raise RuntimeError('output after HELD: ' + line)
        m = BUFFER.fullmatch(line)
        if m:
            label, fb = m.group(1), int(m.group(2))
            if self.ready or label in self.fbs or fb in (0, 78, CONFIRMED_FB) or fb in self.fbs.values():
                raise RuntimeError('unexpected buffer line: ' + line)
            if label == 'B' and 'A' not in self.fbs:
                raise RuntimeError('buffer order changed')
            self.fbs[label] = fb; return 'buffer'
        if line == 'READY_FOR_FLIPS':
            if self.ready or set(self.fbs) != {'A', 'B'}:
                raise RuntimeError('gate before both buffers verified')
            self.ready = True; return 'ready'
        m = ENTER.fullmatch(line)
        if m:
            k = len(self.done) + 1
            if not self.go_sent or self.entered is not None or k > STEPS + 1:
                raise RuntimeError('unexpected flip start: ' + line)
            if (m.group(1), int(m.group(2)), int(m.group(3))) != (*self.expected(k), k):
                raise RuntimeError('flip sequence mismatch: ' + line)
            self.entered = k; return 'enter'
        m = DONE.fullmatch(line)
        if m:
            k = self.entered
            if k is None or (m.group(1), int(m.group(2))) != self.expected(k):
                raise RuntimeError('flip completion mismatch: ' + line)
            # Recorded, not enforced: this vendor driver's vblank counter/timestamp is unverified
            # and a false STOP would leave A/B on screen. Physical check is the user's observation.
            seq = int(m.group(3))
            self.done.append({'step': k, 'label': m.group(1), 'fb': int(m.group(2)), 'seq': seq,
                              'ts': int(m.group(4)) + int(m.group(5)) / 1e6, 'ms': float(m.group(6))})
            self.entered = None; return 'done'
        if line == 'FLIPS_RETURNED':
            if len(self.done) != STEPS + 1 or self.entered is not None:
                raise RuntimeError('return before all flips completed')
            self.returned = True; return 'returned'
        if line.startswith('HELD '):
            self.held = line
            if not (self.returned and 'FLIP_BUFFERS_RETAINED_VISUAL_CONFIRMATION_REQUIRED' in line):
                raise RuntimeError('worker stopped: ' + line)
            return 'held'
        if line.startswith(('STOP:', 'ATOMIC', 'FLIP_ERROR')) or 'uncertain' in line:
            raise RuntimeError('worker error: ' + line)
        # Informational lines are allowed only before the gate.
        if self.go_sent:
            raise RuntimeError('unexpected line after gate: ' + line)
        return None
