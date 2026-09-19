#!/usr/bin/env python3
"""One-shot Vulkan (turnip/KGSL) trial for a NEW boot, kind = compute | anim (from the manifest). Uses the boot registries
(display-owner + gpu-keeper) instead of per-boot PIDs/FB ids. Every tick: template guard, owner/keeper/held test workers
alive, DRM client = owner only, underrun unchanged, and DRM state == native text (anim: during the run both planes on one
of {base, A, B} with only buffer-start lines differing). Never kills, closes, restores or retries.
Usage: python3 -B run-vk.py --check | sudo python3 -B run-vk.py --apply"""
from pathlib import Path
import fcntl, hashlib, json, os, re, signal, stat, subprocess, sys, time
import bootmon as bm, registry as rg

B = Path(__file__).resolve().parent
AGENT = Path('/home/siwal/y700-agent')
c, need = bm.c, bm.need
interrupted = False
ICD_ROOT = Path('/home/siwal/y700-gpu/turnip-kgsl-26.2.3')

def save(path, data):
    with Path(path).open('x') as f: f.write(json.dumps(data, indent=2)); f.flush(); os.fsync(f.fileno())
    os.chmod(path, 0o644)

def planes_on(state, base, base_fb, fb):
    """state equals base with both base-fb planes on `fb`; only buffer start lines inside those plane blocks may differ."""
    a, e = state.splitlines(), base.replace('\tfb=%d\n' % base_fb, '\tfb=%d\n' % fb).splitlines()
    if len(a) != len(e) or base.count('\tfb=%d\n' % base_fb) != 2: return False
    in_base = False
    for x, y in zip(a, e):
        if re.fullmatch(r'plane\[\d+\]: .*', y): in_base = False
        if y == '\tfb=%d' % fb: in_base = True
        if in_base and re.fullmatch(r'\t\t\t\tstart=[0-9a-f]+', y):
            if not re.fullmatch(r'\t\t\t\tstart=[0-9a-f]+', x): return False
        elif x != y: return False
    return True

class Guard:
    def __init__(self, m, t):
        self.m = m; self.mon = bm.BootMonitor(m, t)
        self.reg, self.reg_sha = rg.load(c, m['boot_id']); self.greg, _ = rg.load(c, m['boot_id'], 'gpu')
        self.native = None; self.mode = ('exact', ())
    def processes(self):
        rg.process_ok(c, self.reg['owner']); rg.process_ok(c, self.greg['keeper'])
        for w in self.m['retained_tests']: rg.process_ok(c, w)
    def display(self):
        lines = c.read(rg.DEBUG/'clients').splitlines()
        need(len(lines) == 2 and lines[1].split()[1] == str(self.reg['owner']['pid']) and lines[1].split()[3] == 'y', 'DRM client/master changed')
        need(rg.underruns(c, self.reg['encoder_status']) == {int(k): v for k, v in self.reg['underrun_base'].items()}, 'underrun changed')
        st = c.read(rg.DEBUG/'state'); kind, fbs = self.mode
        if kind == 'exact': need(st == self.native, 'native DRM state changed')
        else: need(st == self.native or any(planes_on(st, self.native, self.reg['native_fb'], f) for f in fbs), 'DRM state unexpected during animation')
    def tick(self):
        r = self.mon.tick(); self.processes(); self.display(); return r
    def observe(self, s):
        end = time.monotonic() + s
        while time.monotonic() < end: self.tick(); time.sleep(.5)
        self.mon.observe(2, quiet=True)

def static(m, g):
    for name, digest in m['files'].items():
        need(Path(name).name == name and hashlib.sha256(c.read(B/name, True)).hexdigest() == digest, 'bundle changed: ' + name)
    need(not (B/'attempt.json').exists(), 'already attempted; inspect result, do not retry')
    g.mon.context(); g.processes()
    for rel, digest in m['icd_files'].items():
        need(hashlib.sha256(c.read(ICD_ROOT/rel, True)).hexdigest() == digest, 'ICD changed: ' + rel)
    for name, digest in m['firmware'].items():
        need(hashlib.sha256(c.read(Path('/lib/firmware')/name, True)).hexdigest() == digest, 'firmware changed: ' + name)
    st = os.lstat('/dev/kgsl-3d0'); mj, mn = (int(x) for x in g.greg['node'].split(':'))
    need(stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(mj, mn) and stat.S_IMODE(st.st_mode) == 0o600 and st.st_uid == 0, 'kgsl node changed')
    need(hashlib.sha256(c.read(g.reg['native_state_file'], True)).hexdigest() == g.reg['native_state_sha256'], 'native state file changed')

def main():
    need(sys.argv[1:] in (['--check'], ['--apply']), 'use --check or --apply')
    m = json.loads(c.read(B/'manifest.json')); t = json.loads(c.read(B/'review-templates.json'))
    need(m['kind'] in ('compute', 'anim'), 'unknown kind')
    g = Guard(m, t); static(m, g)
    print('PREFLIGHT PASS: kind=%s owner+keeper+%d held tests alive, ICD/firmware/node/native state files intact.' % (m['kind'], len(m['retained_tests'])), flush=True)
    if sys.argv[1] == '--check': return
    need(os.geteuid() == 0, 'interactive sudo required')
    g.native = c.read(g.reg['native_state_file'])
    with (B/'apply.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        g.mon.bind(['adci', 'hfi', 'fence']); need(set(g.mon.waits.pid_kinds().values()) == {'adci', 'hfi', 'fence'}, 'reviewed waits not all bound')
        g.display(); print('Observing baseline for 15 seconds.', flush=True); g.observe(15)
        save(B/'attempt.json', {'boot_id': m['boot_id'], 'time': time.time(), 'registry_sha256': g.reg_sha})
        report = {'boot_id': m['boot_id'], 'kind': m['kind'], 'dmesg_before': c.dmesg()}
        child = None
        try:
            (B/'cache').mkdir()
            env = {'PATH': '/usr/bin:/bin', 'HOME': str(B), 'XDG_CACHE_HOME': str(B/'cache'), 'LANG': 'C.UTF-8',
                   'TU_DEBUG': 'startup', 'MESA_SHADER_CACHE_DISABLE': 'true'}
            fds = ()
            if m['kind'] == 'compute':
                cmd = ['python3', '-B', str(B/'vkcompute.py')]
                from computelog import ComputeLog as L; log = L()
            else:
                from borrow_owner import borrow_fd
                from animlog import AnimLog
                env.update(ANIM_FRAMES=str(m['frames']), ANIM_BASE_FB=str(g.reg['native_fb']), ANIM_MODE_BLOB=str(g.reg['mode_blob']))
                bfd = borrow_fd(c, g.reg); fds = (bfd,)
                cmd = ['python3', '-B', str(B/'vkanim.py'), '--drm-fd', str(bfd)]; log = AnimLog(m['frames'], g.reg['native_fb'])
            try:
                with (B/'worker.log').open('xb', buffering=0) as out:
                    child = subprocess.Popen(cmd, pass_fds=fds, stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT,
                                             start_new_session=True, cwd=str(B), env=env)
            finally:
                for f in fds: os.close(f)      # duplicate only; the owner keeps the DRM file
            os.chmod(B/'worker.log', 0o644)
            ident = {'pid': child.pid, 'starttime': c.task_stat(c.read(Path('/proc')/str(child.pid)/'stat'))['starttime'], 'cmd': cmd[:3]}
            report['worker'] = ident; save(B/'worker.json', ident)
            offset = 0; pending = b''; gate_at = None; gates = 0; verified = returned = held = None; start = time.monotonic()
            while True:
                now = time.monotonic(); g.tick(); rg.process_ok(c, ident)
                need(not interrupted, 'interrupted; worker retained')
                raw = (B/'worker.log').read_bytes(); pending += raw[offset:]; offset = len(raw)
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1); line = line.decode(errors='replace')
                    if not line.startswith('F '): print(line, flush=True)
                    ev = log.feed(line)
                    if ev in ('gate1', 'gate2'): gate_at = now
                    if ev == 'verified': verified = now; report.update(fence_ms=log.fence_ms, verify=log.verify)
                    if ev == 'returned': returned = now; g.mode = ('exact', ()); report.update(summary=log.summary, frames=log.done, fbs=log.fb)
                    if ev == 'held': held = now
                ready2 = m['kind'] == 'anim' or verified is not None and now - verified >= 30
                if gate_at is not None and (gates == 0 or ready2) and now - gate_at >= 2:
                    g.mon.observe(2, quiet=True); gates += 1; log.gates = gates; gate_at = None
                    if m['kind'] == 'anim' and gates == 2: g.mode = ('anim', (log.fb['A'], log.fb['B']))   # before GO
                    print('GATE_%d_RELEASED' % gates, flush=True); child.stdin.write(b'GO\n'); child.stdin.flush()
                if held is not None and now - held >= (60 if m['kind'] == 'compute' else 30):
                    g.mon.observe(2, quiet=True); g.tick()
                    report['status'] = 'VK_%s_DONE' % m['kind'].upper(); report['driver_messages'] = log.msgs
                    print(report['status'], flush=True); break
                need(now - start < 300, 'trial timeout; resources retained')
                time.sleep(.25)
        except BaseException as exc:
            report['error'] = str(exc); raise
        finally:
            if child is not None:
                try: child.stdin.close()
                except Exception: pass
            report.update(audit=g.mon.audit + g.mon.review.audit)
            for key, fn in (('dmesg_after', c.dmesg), ('tasks_after', c.tasks), ('battery_after', c.battery)):
                try: report[key] = fn()
                except Exception as exc: report[key + '_error'] = str(exc)
            save(B/'result.json', report); print('Saved:', B/'result.json', flush=True)

if __name__ == '__main__':
    def stop(*_):
        global interrupted; interrupted = True
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM): signal.signal(sig, stop)
    signal.signal(signal.SIGALRM, c.timeout)
    try: main()
    except Exception as exc: sys.exit('STOP: ' + str(exc))
