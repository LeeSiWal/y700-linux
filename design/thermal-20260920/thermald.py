#!/usr/bin/env python3
"""y700-thermald: user-space thermal control for the Y700 (Snapdragon 8 Elite) on the stock GKI kernel.
The kernel's CPU thermal zones have passive trips but no cooling device bound (Android's vendor thermal-engine does the
mitigation there), so nothing throttles below the 125 C 'hot' trip: the prime cores (policy6) go 66 -> 100 C in 2 s at
4.6 GHz. Measured 2026-09-20: a 3.4 GHz cap kept them at 68-78 C under the same Steam download with no throughput loss.

Every second: for each domain (prime CPUs, efficiency CPUs, GPU) take the hottest zone, compare with the profile target and
move that domain's frequency cap one step (two when far above, straight to the floor at EMERGENCY). Caps rise one step at
a time only after the domain stayed COOL_MARGIN below target for RAISE_AFTER seconds. The battery temperature lowers all
ceilings while above its limit. On exit the original maximum frequencies are restored.

Profiles (thermal.json {"profile": "quiet"|"balanced"|"performance"}, re-read when it changes; default balanced).
Writes only: cpufreq policy{0,6}/scaling_max_freq and kgsl-3d0/max_clock_mhz. Status: /run/y700-thermal/status.json.
Usage: sudo python3 -B thermald.py | thermald.py --test | thermald.py --dry-run (reads + decisions, no writes)"""
import json, os, signal, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE/'thermal.json'
RUN = Path('/run/y700-thermal')
LOGDIR = Path('/home/siwal/y700-agent')
CPUFREQ = Path('/sys/devices/system/cpu/cpufreq')
KGSL = Path('/sys/class/kgsl/kgsl-3d0')
BATTERY_TEMP = Path('/sys/class/power_supply/battery/temp')          # tenths of a degree C
PERIOD, RAISE_AFTER, COOL_MARGIN, FAR_ABOVE, EMERGENCY = 1.0, 3, 5.0, 8.0, 105.0

# domain -> thermal zone type prefixes (hottest wins)
ZONES = {'prime': ('cpu-1-',), 'eff': ('cpu-0-',), 'gpu': ('gpuss-',)}
# domain: (ceiling kHz / MHz, floor, target C) per profile
PROFILES = {
    'quiet':       {'prime': (2668800, 1497600, 75), 'eff': (2496000, 1440000, 75), 'gpu': (726, 382, 75), 'battery': 40},
    'balanced':    {'prime': (3398400, 1632000, 85), 'eff': (3628800, 1440000, 85), 'gpu': (1050, 382, 85), 'battery': 44},
    'performance': {'prime': (4608000, 1632000, 95), 'eff': (3628800, 1440000, 95), 'gpu': (1200, 382, 92), 'battery': 46},
}
DEFAULT_PROFILE = 'balanced'

class Domain:
    """Pure step controller (unit-tested): steps = available frequencies ascending; update(temp, ceiling, floor, target)
    -> new cap (one of steps)."""
    def __init__(self, name, steps, cap):
        self.name, self.steps = name, sorted(steps); self.i = self._index(cap); self.cool = 0
    def _index(self, f): return max([k for k, s in enumerate(self.steps) if s <= f] or [0])
    @property
    def cap(self): return self.steps[self.i]
    def update(self, temp, ceiling, floor, target):
        hi, lo = self._index(ceiling), self._index(floor)
        lo = min(lo, hi)
        if temp >= EMERGENCY: self.i = lo; self.cool = 0
        elif temp > target:
            self.i = max(lo, self.i - (2 if temp > target + FAR_ABOVE else 1)); self.cool = 0
        elif temp < target - COOL_MARGIN:
            self.cool += 1
            if self.cool >= RAISE_AFTER: self.i = min(hi, self.i + 1); self.cool = 0
        else: self.cool = 0
        self.i = max(lo, min(hi, self.i))                   # a new profile ceiling/floor applies at once
        return self.cap

def zone_map(root='/sys/class/thermal'):
    out = {d: [] for d in ZONES}
    for z in sorted(Path(root).glob('thermal_zone*')):
        try: t = (z/'type').read_text().strip()
        except OSError: continue
        for d, prefixes in ZONES.items():
            if t.startswith(prefixes): out[d].append(z/'temp')
    return out

def hottest(paths):
    vals = []
    for p in paths:
        try: vals.append(int(p.read_text()) / 1000)
        except (OSError, ValueError): pass
    return max(vals) if vals else None

def read_profile(path=CONFIG):
    try: p = json.loads(Path(path).read_text()).get('profile', DEFAULT_PROFILE)
    except (OSError, ValueError, AttributeError): p = DEFAULT_PROFILE
    return p if p in PROFILES else DEFAULT_PROFILE

def battery_limited(prof, batt, dom):
    """Battery above its limit: each degree over takes the domain ceiling one more step down (at least to 3/4 range)."""
    ceiling, floor, target = prof[dom]
    if batt is None or batt <= prof['battery']: return ceiling
    return max(floor, int(ceiling - (ceiling - floor) * min(0.75, 0.15 * (batt - prof['battery'] + 1))))

class Actuators:
    def __init__(self, dry=False):
        self.dry = dry
        self.paths = {'prime': CPUFREQ/'policy6/scaling_max_freq', 'eff': CPUFREQ/'policy0/scaling_max_freq', 'gpu': KGSL/'max_clock_mhz'}
        self.steps = {'prime': [int(x) for x in (CPUFREQ/'policy6/scaling_available_frequencies').read_text().split()] + [int((CPUFREQ/'policy6/cpuinfo_max_freq').read_text())],
                      'eff': [int(x) for x in (CPUFREQ/'policy0/scaling_available_frequencies').read_text().split()],
                      'gpu': [int(x) for x in (KGSL/'freq_table_mhz').read_text().split()]}
        self.original = {'prime': int((CPUFREQ/'policy6/cpuinfo_max_freq').read_text()), 'eff': int((CPUFREQ/'policy0/cpuinfo_max_freq').read_text()),
                         'gpu': max(self.steps['gpu'])}
        self.last = {}
    def current(self, d): return int(self.paths[d].read_text())
    def set(self, d, v):
        if self.last.get(d) == v: return False
        if not self.dry: self.paths[d].write_text(str(v))
        self.last[d] = v; return True
    def restore(self):
        for d, v in self.original.items():
            try:
                if not self.dry: self.paths[d].write_text(str(v))
            except OSError: pass

def log(*a):
    line = '%s %s' % (time.strftime('%H:%M:%S'), ' '.join(str(x) for x in a)); print(line, flush=True)
    try:
        with (LOGDIR/('thermald-%s.log' % Path('/proc/sys/kernel/random/boot_id').read_text()[:8])).open('a') as f: f.write(line + '\n')
    except OSError: pass

def main(argv):
    if '--test' in argv: return selftest()
    dry = '--dry-run' in argv
    if not dry and os.geteuid() != 0: sys.exit('run as root (or --dry-run)')
    zones = zone_map(); missing = [d for d, z in zones.items() if not z]
    if missing: sys.exit('no thermal zones for %s' % missing)
    act = Actuators(dry)
    doms = {d: Domain(d, act.steps[d], act.current(d)) for d in ZONES}
    stop = []
    for s in (signal.SIGTERM, signal.SIGINT): signal.signal(s, lambda *_: stop.append(1))
    if not dry: RUN.mkdir(mode=0o755, exist_ok=True)
    profile = None; t_status = 0
    log('START dry=%s zones %s original %s' % (dry, {d: len(z) for d, z in zones.items()}, act.original))
    try:
        while not stop:
            p = read_profile()
            if p != profile: log('PROFILE', p, PROFILES[p]); profile = p
            prof = PROFILES[profile]
            try: batt = int(BATTERY_TEMP.read_text()) / 10
            except (OSError, ValueError): batt = None
            state = {'t': time.time(), 'profile': profile, 'battery_c': batt, 'domains': {}}
            for d, dom in doms.items():
                temp = hottest(zones[d])
                if temp is None: continue
                ceiling, floor, target = prof[d]; ceiling = battery_limited(prof, batt, d)
                old = dom.cap; cap = dom.update(temp, ceiling, floor, target)
                if act.set(d, cap) and old != cap: log('CAP %s %s -> %s (%.0f C, target %d, ceiling %s, battery %s)' % (d, old, cap, temp, target, ceiling, batt))
                state['domains'][d] = {'temp_c': temp, 'cap': cap, 'target_c': target, 'ceiling': ceiling}
            if not dry and time.monotonic() - t_status > 5:
                tmp = RUN/'status.tmp'; tmp.write_text(json.dumps(state)); os.chmod(tmp, 0o644); os.replace(tmp, RUN/'status.json'); t_status = time.monotonic()
            time.sleep(PERIOD)
    finally:
        act.restore(); log('EXIT restored', act.original)

def selftest():
    steps = [768000, 1632000, 2668800, 3129600, 3398400, 3648000, 4396800, 4608000]
    d = Domain('prime', steps, 4608000)
    assert d.update(90, 3398400, 1632000, 85) == 3398400             # profile ceiling applies at once
    assert d.update(90, 3398400, 1632000, 85) == 3129600             # above target: one step down
    assert d.update(99, 3398400, 1632000, 85) == 1632000             # far above: two steps (clamped to the floor)
    assert d.update(84, 3398400, 1632000, 85) == 1632000             # inside the band: hold
    for _ in range(2): assert d.update(70, 3398400, 1632000, 85) == 1632000
    assert d.update(70, 3398400, 1632000, 85) == 2668800             # 3 s cool: one step up
    assert d.update(106, 3398400, 1632000, 85) == 1632000            # emergency: floor
    for _ in range(30): d.update(60, 3398400, 1632000, 85)
    assert d.cap == 3398400                                          # never above the ceiling
    assert d.update(60, 4608000, 1632000, 95) == 3398400             # performance: rises step by step, not at once
    prof = PROFILES['balanced']
    assert battery_limited(prof, 40.0, 'prime') == 3398400 and battery_limited(prof, None, 'prime') == 3398400
    assert 1632000 <= battery_limited(prof, 46.0, 'prime') < 3398400
    assert battery_limited(prof, 60.0, 'prime') >= 1632000
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        c = Path(t)/'thermal.json'; assert read_profile(c) == 'balanced'
        c.write_text('{"profile": "quiet"}'); assert read_profile(c) == 'quiet'
        c.write_text('{"profile": "turbo"}'); assert read_profile(c) == 'balanced'
        c.write_text('not json'); assert read_profile(c) == 'balanced'
    z = zone_map(); assert all(z.values()), z
    print('thermald selftest OK', {k: len(v) for k, v in z.items()}); return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
