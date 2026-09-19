#!/usr/bin/env python3
"""One-shot finite GPU animation: A/B dumb buffers, per-frame compute render + CPU copy to the back buffer + blocking atomic flip\n(PAGE_FLIP_EVENT), then return to the confirmed FB343. State must equal the FB343 text before/after, and during the run both\nplanes must be on one of {343, A, B} with only buffer-start lines differing."""
from pathlib import Path
import fcntl, hashlib, json, os, stat, subprocess, sys, time, signal
import health_guard as h
import gpuguard as g
from animlog import AnimLog
from borrow import borrow_fd, verify_original
import re

B = Path(__file__).resolve().parent
c = h.c; need = h.need
NET = 'enx<MAC>'
NODE = Path('/dev/kgsl-3d0')
GPU_DEVS = {'kgsl': '3d00000.qcom,kgsl-3d0', 'gmu': '3d37000.qcom,gmu', 'iommu': '3da0000.qcom,kgsl-iommu'}
GPUCC = Path('/sys/bus/platform/devices/3d90000.clock-controller/state_synced')
interrupted = False

def save(name, data):
    with (B/name).open('x') as f:
        json.dump(data, f, indent=2); f.flush(); os.fsync(f.fileno())
    os.chmod(B/name, 0o644)

def gpu_view():
    view = {}
    for k, d in GPU_DEVS.items():
        p = Path('/sys/bus/platform/devices')/d/'driver'
        view[k] = p.resolve().name if p.is_symlink() else None
    view['gpucc_state_synced'] = c.read(GPUCC).strip()
    view['dev_kgsl'] = NODE.exists()
    view['devfreq'] = sorted(p.name for p in Path('/sys/class/devfreq').iterdir())
    return view

def planes_on(state, base, fb):
    """True iff state equals the confirmed FB343 text with both FB343 planes now on `fb`. Only those planes' buffer start
    lines may differ (new dumb buffers); every other line, incl. 'allocated by = python3', must match exactly."""
    exp = base.replace('\tfb=343\n', '\tfb=%d\n' % fb)
    a, e = state.splitlines(), exp.splitlines()
    if len(a) != len(e) or exp.count('\tfb=%d\n' % fb) != 2: return False
    for x, y in zip(a, e):
        if y.strip() == 'start=0010485d':
            if not re.fullmatch(r'\t\t\t\tstart=[0-9a-f]+', x): return False
        elif x != y: return False
    return True

def check_icd(m):
    for rel, digest in m['vk_review']['icd_files'].items():
        p = Path(m['vk_review']['icd_root'])/rel
        need(p.is_file() and not p.is_symlink() and hashlib.sha256(c.read(p, True)).hexdigest() == digest, 'ICD file missing/changed: '+rel)

def check_firmware(m):
    for name, digest in m['vk_review']['firmware'].items():
        p = Path('/lib/firmware')/name
        need(p.is_file() and not p.is_symlink() and hashlib.sha256(c.read(p, True)).hexdigest() == digest, 'installed firmware missing/changed: '+name)

class Monitor(h.Monitor):
    def __init__(self, m):
        super().__init__(m); self.state_mode = ('exact', None) if m['anim_review']['start_fb'] == 343 else ('anim', (m['anim_review']['start_fb'],)); self.base_log = ''; self.underrun = None; self.transitions = []; self.last_view = None
    def display_ok(self):
        for w in self.m['retained_workers']:
            p = Path('/proc')/str(w['pid'])
            need(p.exists() and c.task_stat(c.read(p/'stat'))['starttime'] == w['starttime'], 'retained DRM worker changed: '+str(w['pid']))
        state, expected = c.read('/sys/kernel/debug/dri/0/state'), c.read(B/'expected-state.txt')
        mode, fbs = self.state_mode
        ok = state == expected if mode == 'exact' else any(planes_on(state, expected, f) for f in fbs)
        need(ok, 'DRM state unexpected in mode '+mode)
        lines = c.read('/sys/kernel/debug/dri/0/clients').splitlines()
        need(len(lines) == 2 and lines[1].split() == ['kms-held', '2604', '0', 'y', 'y', '0', '0'], 'DRM ownership changed')
        need(g.underruns(c.read('/sys/kernel/debug/dri/0/encoder68/status')) == self.underrun, 'display underrun counter changed')
        n = Path('/sys/class/net')/NET
        need(c.read(n/'carrier').strip() == '1' and c.read(n/'operstate').strip() == 'up', 'USB Ethernet link changed')
    def tick(self):
        tasks, unknown = super().tick()
        if self.underrun is not None: self.display_ok()
        faults = g.gpu_faults(self.base_log, self.last_log)
        need(not faults, 'GPU-side error/fault record: '+str(faults[:3]))
        view = gpu_view()
        if view != self.last_view:
            self.transitions.append({'uptime': c.read('/proc/uptime').split()[0], 'phase': self.phase, 'view': view})
            print('GPU_VIEW', json.dumps(view), flush=True); self.last_view = view
        return tasks, unknown

def worker_ok(child, identity):
    need(child.poll() is None, 'worker exited; kgsl fd released by exit; no retry')
    p = Path('/proc')/str(child.pid)
    need(c.task_stat(c.read(p/'stat'))['starttime'] == identity['starttime'], 'worker PID reused')
    need(c.read(p/'cmdline', True).split(b'\0')[:3] == [b'python3', b'-B', str(B/'vkanim.py').encode()], 'worker command changed')

def check_node():
    st = os.lstat(NODE)
    need(stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(495, 0) and stat.S_IMODE(st.st_mode) == 0o600 and st.st_uid == 0, '/dev/kgsl-3d0 changed')

def supervise(child, identity, monitor, report):
    frames = monitor.m['anim_review']['frames']
    ar = monitor.m['anim_review']
    log = AnimLog(frames, ar['start_fb'], ar['reserved_fbs']); start = time.monotonic(); gate_at = None; gates_sent = 0; entered = returned = held = None; offset = 0; pending = b''
    try:
        while True:
            now = time.monotonic(); monitor.tick()
            need(not interrupted, 'interrupted; worker/fds/buffers retained')
            worker_ok(child, identity)
            raw = (B/'worker.log').read_bytes(); pending += raw[offset:]; offset = len(raw)
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1); line = line.decode(errors='replace')
                if not line.startswith('F '): print(line, flush=True)
                ev = log.feed(line)
                if ev in ('gate1', 'gate2'): gate_at = now
                if ev == 'enter': entered = now
                if ev == 'returned':
                    returned = now; monitor.state_mode = ('exact', None); monitor.phase = 'returned observation'
                    report.update(summary=log.summary, frames=log.done, fbs=log.fb); report['snapshots'].append(snap('after-animation'))
                if ev == 'held': held = now
            if gate_at is not None and gates_sent < 2 and now-gate_at >= 2:
                monitor.observe(2, quiet=True); worker_ok(child, identity)
                _, unknown = monitor.tick(); need(not unknown, 'unreviewed task before gate')
                gates_sent += 1; log.gates = gates_sent; gate_at = None
                name = 'setup' if gates_sent == 1 else 'animation'
                if gates_sent == 2: monitor.state_mode = ('anim', (343, ar['start_fb'], log.fb['A'], log.fb['B']))   # before GO
                save(name+'-intent.json', {'boot_id': monitor.m['boot_id'], 'worker': identity, 'time': time.time()})
                monitor.phase = name; report[name+'_gate_released'] = True
                print(name.upper()+'_GATE_RELEASED', flush=True); child.stdin.write(b'GO\n'); child.stdin.flush()
            if entered is not None and returned is None: need(now-entered < frames*0.2+10, 'animation did not finish in time; worker retained')
            if held is not None and now-held >= 30:
                monitor.observe(2, quiet=True); worker_ok(child, identity)
                report['snapshots'].append(snap('after-30-seconds'))
                report['status'] = 'ANIMATION_DONE_RETURNED_FB343'; report['driver_messages'] = log.msgs
                print(report['status'], json.dumps(log.summary), flush=True)
                return
            need(now-start < frames*0.2+240, 'animation trial timeout; resources retained')
            time.sleep(.25)
    finally:
        try: child.stdin.close()
        except BrokenPipeError: pass

def snap(label):
    return {'phase': label, 'uptime': c.read('/proc/uptime').strip(), 'gpu': gpu_view(), 'clocks': c.clocks(),
            'underrun': c.read('/sys/kernel/debug/dri/0/encoder68/status')}

def main():
    need(sys.argv[1:] in (['--check'], ['--apply']), 'use --check or --apply')
    m = json.loads(c.read(B/'manifest.json'))
    for name, digest in m['files'].items():
        need(Path(name).name == name and hashlib.sha256(c.read(B/name, True)).hexdigest() == digest, 'bundle changed: '+name)
    need(not (B/'attempt.json').exists(), 'already attempted; inspect result, do not retry')
    for key in ('anim_review', 'vk_review', 'retained_workers', 'original_worker', 'original_executable'):
        need(key in m, 'manifest missing key: '+key)
    need(m['anim_review'].get('frames') in (120, 600), 'bad frame count in manifest')
    monitor = Monitor(m); monitor.context(); monitor.check_log(c.dmesg()); check_firmware(m)
    need(gpu_view() == m['vk_review']['expected_before'], 'GPU state differs from review: '+json.dumps(gpu_view()))
    for w in m['retained_workers']:
        p = Path('/proc')/str(w['pid'])
        need(p.exists() and c.task_stat(c.read(p/'stat'))['starttime'] == w['starttime'], 'retained DRM worker changed: '+str(w['pid']))
    check_icd(m); check_node()
    print('PREFLIGHT PASS: same boot, 167 module identities, KGSL bound, node 495:0, firmware and ICD hashes, '+str(len(m['retained_workers']))+' retained workers.', flush=True)
    if sys.argv[1] == '--check': return
    need(os.geteuid() == 0, 'interactive sudo required')
    with (B/'apply.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monitor.underrun = g.underruns(c.read('/sys/kernel/debug/dri/0/encoder68/status'))
        need(monitor.underrun == {int(k): v for k, v in m['vk_review']['underrun_base'].items()}, 'underrun base differs from review')
        monitor.base_log = c.dmesg(); monitor.last_log = monitor.base_log
        print('Observing baseline for 15 seconds.', flush=True); monitor.observe(15, quiet=True)
        save('attempt.json', {'boot_id': m['boot_id'], 'time': time.time()})
        report = {'boot_id': m['boot_id'], 'dmesg_before': monitor.base_log, 'gpu_before': gpu_view(), 'snapshots': [snap('before')],
                  'setup_gate_released': False, 'animation_gate_released': False, 'physical': 'requires user observation of the panel'}
        try:
            (B/'cache').mkdir()
            env = {'PATH': '/usr/bin:/bin', 'HOME': str(B), 'XDG_CACHE_HOME': str(B/'cache'), 'LANG': 'C.UTF-8',
                   'TU_DEBUG': 'startup', 'MESA_SHADER_CACHE_DISABLE': 'true', 'ANIM_FRAMES': str(m['anim_review']['frames']), 'ANIM_START_FB': str(m['anim_review']['start_fb']),
                   'ANIM_RESERVED_FBS': ','.join(map(str, m['anim_review']['reserved_fbs']))}
            report['worker_env'] = env
            borrowed = borrow_fd(m, c)
            try:
                with (B/'worker.log').open('xb', buffering=0) as out:
                    child = subprocess.Popen(['python3', '-B', str(B/'vkanim.py'), '--drm-fd', str(borrowed)], pass_fds=(borrowed,),
                                             stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT, start_new_session=True, cwd=str(B), env=env)
            finally:
                os.close(borrowed)  # duplicate only; PID2604 and all retained workers keep the DRM file
            os.chmod(B/'worker.log', 0o644)
            identity = {'pid': child.pid, 'starttime': c.task_stat(c.read(Path('/proc')/str(child.pid)/'stat'))['starttime'], 'boot_id': m['boot_id']}
            report['worker'] = identity; save('worker.json', identity)
            supervise(child, identity, monitor, report)
        except BaseException as exc:
            report['error'] = str(exc); report['phase'] = monitor.phase; report['status'] = 'STOPPED_INSPECT_RETAINED'
            raise
        finally:
            try: check_firmware(m); check_icd(m); check_node(); report['firmware_icd_node_unchanged'] = True
            except Exception as exc: report['final_check_error'] = str(exc)
            report.update(transitions=monitor.transitions, audit=monitor.audit, reviewed_waits=monitor.waits)
            report['kgsl_log'] = [l for l in g.new_lines(monitor.base_log, monitor.last_log)
                                  if any(k in l.lower() for k in ('kgsl', 'adreno', 'gmu', 'zap', 'gpu', 'firmware', 'scm', 'pas'))]
            for key, fn in [('dmesg_after', c.dmesg), ('tasks_after', c.tasks), ('battery_after', c.battery)]:
                try: report[key] = fn()
                except Exception as exc: report[key+'_error'] = str(exc)
            save('result.json', report); print('Saved:', B/'result.json', flush=True)

if __name__ == '__main__':
    def stop(*_):
        global interrupted; interrupted = True
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM): signal.signal(sig, stop)
    signal.signal(signal.SIGALRM, c.timeout)
    try: main()
    except Exception as exc: sys.exit('STOP: '+str(exc))
