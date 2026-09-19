#!/usr/bin/env python3
"""One-shot GPU-rendered frame to the panel: compute-rendered 1904x3040 frame (turnip/KGSL), CPU copy into a new cached dumb
buffer on a borrowed DRM file, then (gate 2) one blocking atomic FB_ID switch of planes 95+128. Before the commit the DRM
state must equal the FB335 text exactly; after it, equal except fb id/buffer address, with both planes on the new fb."""
from pathlib import Path
import fcntl, hashlib, json, os, stat, subprocess, sys, time, signal
import health_guard as h
import gpuguard as g
from renderlog import RenderLog
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

def moved_to(state, expected, fb):
    """True iff state equals the FB335 expectation with exactly the two FB335 planes now on `fb`; only those planes'
    buffer start lines and the FB 'allocated by' (creator comm, must be python3) may differ. Every other line (other planes' fb=, geometry, sizes, pitches) must match exactly."""
    exp = expected.replace('\tfb=335\n', '\tfb=%d\n' % fb)
    a, e = state.splitlines(), exp.splitlines()
    if len(a) != len(e) or exp.count('\tfb=%d\n' % fb) != 2: return False
    for x, y in zip(a, e):
        if y.strip() == 'start=00100591':
            if not re.fullmatch(r'\t\t\t\tstart=[0-9a-f]+', x): return False
        elif y == '\t\tallocated by = kms-held':          # FB creator's comm; the new FB is made by the Python worker
            if x != '\t\tallocated by = python3': return False
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
        super().__init__(m); self.state_mode = ('exact', None); self.base_log = ''; self.underrun = None; self.transitions = []; self.last_view = None
    def display_ok(self):
        for w in self.m['retained_workers']:
            p = Path('/proc')/str(w['pid'])
            need(p.exists() and c.task_stat(c.read(p/'stat'))['starttime'] == w['starttime'], 'retained DRM worker changed: '+str(w['pid']))
        state, expected = c.read('/sys/kernel/debug/dri/0/state'), c.read(B/'expected-state.txt')
        mode, fb = self.state_mode
        exact = state == expected
        moved = moved_to(state, expected, fb) if fb else False
        need(exact if mode == 'exact' else (exact or moved) if mode == 'transition' else moved, 'DRM state unexpected in mode '+mode)
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
    need(c.read(p/'cmdline', True).split(b'\0')[:3] == [b'python3', b'-B', str(B/'vkrender.py').encode()], 'worker command changed')

def check_node():
    st = os.lstat(NODE)
    need(stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(495, 0) and stat.S_IMODE(st.st_mode) == 0o600 and st.st_uid == 0, '/dev/kgsl-3d0 changed')

def supervise(child, identity, monitor, report):
    log = RenderLog(); start = time.monotonic(); gate_at = None; gates_sent = 0; returned = None; offset = 0; pending = b''
    try:
        while True:
            now = time.monotonic(); monitor.tick()
            need(not interrupted, 'interrupted; worker/fds/buffers retained')
            worker_ok(child, identity)
            raw = (B/'worker.log').read_bytes(); pending += raw[offset:]; offset = len(raw)
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1); line = line.decode(errors='replace'); print(line, flush=True)
                ev = log.feed(line)
                if ev in ('gate1', 'gate2'): gate_at = now
                if ev == 'verified': report.update(fence_ms=log.fence_ms, verify=log.verify)
                if ev == 'commit': monitor.state_mode = ('transition', log.fb)
                if ev == 'returned':
                    returned = now; monitor.state_mode = ('new', log.fb); monitor.phase = 'displayed observation'
                    report.update(fb=log.fb, copied=log.copied, committed=True); report['snapshots'].append(snap('after-commit'))
            if gate_at is not None and gates_sent < 2 and now-gate_at >= 2:
                monitor.observe(2, quiet=True); worker_ok(child, identity)
                _, unknown = monitor.tick(); need(not unknown, 'unreviewed task before gate')
                gates_sent += 1; log.gates = gates_sent; gate_at = None
                name = 'render' if gates_sent == 1 else 'commit'
                save(name+'-intent.json', {'boot_id': monitor.m['boot_id'], 'worker': identity, 'time': time.time()})
                monitor.phase = name; report[name+'_gate_released'] = True
                if gates_sent == 2: monitor.state_mode = ('transition', log.fb)   # before GO: the worker commits within ms
                print(name.upper()+'_GATE_RELEASED', flush=True); child.stdin.write(b'GO\n'); child.stdin.flush()
            if gates_sent >= 1 and returned is None: need(now-start < 150, 'render/commit did not finish; worker retained')
            if returned is not None and log.held is not None and now-returned >= 30:
                monitor.observe(2, quiet=True); worker_ok(child, identity)
                report['snapshots'].append(snap('after-30-seconds'))
                report['status'] = 'GPU_FRAME_DISPLAYED_AWAITING_VISUAL_CONFIRMATION'; report['driver_messages'] = log.msgs
                print(report['status'], 'fb=%d fence_ms=%.2f' % (log.fb, log.fence_ms), flush=True)
                return
            need(now-start < 240, 'render trial timeout; resources retained')
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
                  'render_gate_released': False, 'commit_gate_released': False, 'physical': 'requires user observation of the panel'}
        try:
            (B/'cache').mkdir()
            env = {'PATH': '/usr/bin:/bin', 'HOME': str(B), 'XDG_CACHE_HOME': str(B/'cache'), 'LANG': 'C.UTF-8',
                   'TU_DEBUG': 'startup', 'MESA_SHADER_CACHE_DISABLE': 'true'}
            report['worker_env'] = env
            borrowed = borrow_fd(m, c)
            try:
                with (B/'worker.log').open('xb', buffering=0) as out:
                    child = subprocess.Popen(['python3', '-B', str(B/'vkrender.py'), '--drm-fd', str(borrowed)], pass_fds=(borrowed,),
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
