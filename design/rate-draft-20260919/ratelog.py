"""Strict parser for rate-held output. Deviations STOP; timing values are measurements, not pass/fail."""
import re

FB_A, FB_B, CONFIRMED_FB = 341, 342, 335
RETAINED = re.compile(r'RETAINED_FB id=(\d+) 1904x3040 pitch=7680')
SUMMARY = re.compile(r'RATE_SUMMARY steps=(\d+) intervals=(\d+) span_us=(\d+) mean_us=(\d+) min_us=(\d+) '
                     r'max_us=(\d+) slow=(\d+) fps_x100=(\d+) first_us=(\d+)')
TIME = re.compile(r'T (\d+) (\d+) (\d+)')


def fb_at(k, steps):
    return CONFIRMED_FB if k > steps else (FB_A if k % 2 else FB_B)


class RateLog:
    def __init__(self, steps):
        self.steps = steps; self.retained = []; self.ready = False; self.go_sent = False
        self.entered = False; self.summary = None; self.times = []; self.returned = False; self.held = None

    def feed(self, line):
        if self.held is not None:
            raise RuntimeError('output after HELD: ' + line)
        m = RETAINED.fullmatch(line)
        if m:
            if self.ready or len(self.retained) >= 3:
                raise RuntimeError('unexpected retained FB line: ' + line)
            self.retained.append(int(m.group(1)))
            if self.retained != [FB_A, FB_B, CONFIRMED_FB][:len(self.retained)]:
                raise RuntimeError('retained framebuffer order/identity changed')
            return 'retained'
        if line.startswith('PLAN '):
            if line.split()[1] != 'steps=%d' % self.steps:
                raise RuntimeError('worker step count differs from manifest')
            return None
        if line == 'READY_FOR_RATE':
            if self.ready or len(self.retained) != 3:
                raise RuntimeError('gate before retained framebuffers verified')
            self.ready = True; return 'ready'
        if line == 'RATE_ENTER':
            if not self.go_sent or self.entered:
                raise RuntimeError('rate loop entered without gate')
            self.entered = True; return 'enter'
        m = SUMMARY.fullmatch(line)
        if m:
            v = [int(x) for x in m.groups()]
            if not self.entered or self.summary is not None or v[0] != self.steps or v[1] != self.steps - 1:
                raise RuntimeError('unexpected summary: ' + line)
            self.summary = dict(zip(('steps', 'intervals', 'span_us', 'mean_us', 'min_us', 'max_us', 'slow',
                                     'fps_x100', 'first_us'), v))
            return 'summary'
        m = TIME.fullmatch(line)
        if m:
            k, fb, t = (int(x) for x in m.groups())
            if self.summary is None or k != len(self.times) + 1 or k > self.steps + 1 or fb != fb_at(k, self.steps):
                raise RuntimeError('timing record mismatch: ' + line)
            if self.times and t <= self.times[-1]:
                raise RuntimeError('completion times not increasing')
            self.times.append(t); return 'time'
        if line == 'RATE_RETURNED':
            if len(self.times) != self.steps + 1:
                raise RuntimeError('return before all timing records')
            s = self.summary
            if self.times[self.steps - 1] - self.times[0] != s['span_us']:
                raise RuntimeError('summary span disagrees with timing records')
            self.returned = True; return 'returned'
        if line.startswith('HELD '):
            self.held = line
            if not (self.returned and line.endswith('reason=RATE_DONE_FB335_RETAINED')):
                raise RuntimeError('worker stopped: ' + line)
            return 'held'
        if line.startswith(('STOP:', 'ATOMIC', 'RATE_FAILED_AT', 'RATE_PARTIAL')) or 'uncertain' in line:
            raise RuntimeError('worker error: ' + line)
        if self.go_sent:
            raise RuntimeError('unexpected line after gate: ' + line)
        return None
