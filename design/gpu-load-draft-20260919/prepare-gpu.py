#!/usr/bin/env python3
"""One-shot KGSL stage-1 trial: load the reviewed 7-module GPU closure and observe probe/bind.
Does NOT open /dev/kgsl-3d0, submit work, change clocks/ICC, or touch KMS. Nothing is ever unloaded.
The display (FB335, 13 retained workers), USB Ethernet and underrun counters are checked every tick.
Firmware must already be installed in /lib/firmware by the separate reviewed command; hashes are checked here."""
from pathlib import Path
import fcntl, hashlib, json, os, signal, subprocess, sys, time
import health_guard as h
import gpuguard as g

B = Path(__file__).resolve().parent
c = h.c; need = h.need
FW_DIR = Path('/lib/firmware')
NET = 'enx<MAC>'
GPU_DEVS = {'kgsl': '3d00000.qcom,kgsl-3d0', 'gmu': '3d37000.qcom,gmu', 'iommu': '3da0000.qcom,kgsl-iommu'}
GPUCC = Path('/sys/bus/platform/devices/3d90000.clock-controller/state_synced')

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
    view['dev_kgsl'] = os.path.exists('/dev/kgsl-3d0')   # existence only; never opened
    view['devfreq'] = sorted(p.name for p in Path('/sys/class/devfreq').iterdir())
    return view

class GpuMonitor(h.Monitor):
    def __init__(self, m):
        super().__init__(m); self.base_log = ''; self.underrun = None; self.transitions = []; self.last_view = None

    def display_ok(self):
        for w in self.m['retained_workers']:
            p = Path('/proc')/str(w['pid'])
            need(p.exists() and c.task_stat(c.read(p/'stat'))['starttime'] == w['starttime'], 'retained DRM worker changed: '+str(w['pid']))
        need(c.read('/sys/kernel/debug/dri/0/state') == c.read(B/'expected-state.txt'), 'DRM state changed (FB335 layout)')
        lines = c.read('/sys/kernel/debug/dri/0/clients').splitlines()
        need(len(lines) == 2 and lines[1].split() == ['kms-held', '2604', '0', 'y', 'y', '0', '0'], 'DRM ownership changed')
        need(g.underruns(c.read('/sys/kernel/debug/dri/0/encoder68/status')) == self.underrun, 'display underrun counter changed')
        n = Path('/sys/class/net')/NET
        need(c.read(n/'carrier').strip() == '1' and c.read(n/'operstate').strip() == 'up', 'USB Ethernet link changed')

    def tick(self):
        tasks, unknown = super().tick()
        self.display_ok()
        faults = g.gpu_faults(self.base_log, self.last_log)
        need(not faults, 'GPU-side error/fault record: '+str(faults[:3]))
        view = gpu_view()
        if view != self.last_view:
            self.transitions.append({'uptime': c.read('/proc/uptime').split()[0], 'phase': self.phase, 'view': view})
            print('GPU_VIEW', json.dumps(view), flush=True); self.last_view = view
        return tasks, unknown

    def load(self, filename):
        self.observe(2, quiet=True); self.phase = filename
        module = filename[:-3].replace('-', '_')
        self.load_start[module] = int(float(c.read('/proc/uptime').split()[0])*os.sysconf('SC_CLK_TCK'))
        print('LOAD', filename, flush=True)
        with (B/(filename+'.log')).open('x') as f:
            p = subprocess.Popen(['/sbin/insmod', str(B/filename)], stdout=f, stderr=subprocess.STDOUT)
            self.insmod_pid = p.pid; started = time.monotonic()
            try:
                while p.poll() is None:
                    self.tick(); need(time.monotonic()-started < 25, 'insmod timeout; process retained, no retry')
                    time.sleep(.2)
                f.flush(); os.fsync(f.fileno())
                need(p.returncode == 0, 'insmod failed: '+filename)
                self.loaded[module] = self.m['load_notes'][module]
                self.tick()
            finally:
                if p.poll() is None: self.audit.append({'event': 'incomplete_insmod', 'pid': p.pid, 'module': module})
        self.insmod_pid = None
        need(c.read(Path('/sys/module')/module/'initstate').strip() == 'live', 'module not live: '+module)
        self.observe(3, quiet=True)

def check_firmware(m):
    # firmware_class.path is not readable on this kernel (EPERM), so only the default search path is used.
    for name, digest in m['gpu_review']['firmware'].items():
        p = FW_DIR/name
        need(p.is_file() and not p.is_symlink() and hashlib.sha256(c.read(p, True)).hexdigest() == digest, 'installed firmware missing/changed: '+name)

def main():
    need(sys.argv[1:] in (['--check'], ['--apply']), 'use --check or --apply')
    m = json.loads(c.read(B/'manifest.json'))
    for name, digest in m['files'].items():
        need(Path(name).name == name or name.startswith('firmware/'), 'unsafe bundle path: '+name)
        need(hashlib.sha256(c.read(B/name, True)).hexdigest() == digest, 'bundle changed: '+name)
    need(not (B/'attempt.json').exists(), 'already attempted; inspect result, do not retry')
    monitor = GpuMonitor(m); monitor.context(); monitor.check_log(c.dmesg())
    view = gpu_view()
    need(view == m['gpu_review']['expected_before'], 'GPU devices/devfreq state differs from review: '+json.dumps(view))
    loaded = {x.split()[0] for x in c.read('/proc/modules').splitlines()}
    need(not ({f[:-3].replace('-', '_') for f in m['load_order']} & loaded), 'a closure module is already loaded')
    check_firmware(m)
    print('PREFLIGHT PASS: same boot, 160 live module identities, GPU devices unbound, closure not loaded, bundle and /lib/firmware hashes.', flush=True)
    if sys.argv[1] == '--check': return
    need(os.geteuid() == 0, 'interactive sudo required')
    with (B/'apply.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monitor.underrun = g.underruns(c.read('/sys/kernel/debug/dri/0/encoder68/status'))
        need(monitor.underrun == {int(k): v for k, v in m['gpu_review']['underrun_base'].items()}, 'underrun base differs from review')
        monitor.base_log = c.dmesg(); monitor.last_log = monitor.base_log
        print('Observing baseline for 15 seconds (display, network, tasks, log).', flush=True)
        monitor.observe(15, quiet=True)
        report = {'boot_id': m['boot_id'], 'dmesg_before': monitor.base_log, 'clocks_before': c.clocks(),
                  'gpu_before': gpu_view()}
        save('attempt.json', {'boot_id': m['boot_id'], 'time': time.time(), 'load_order': m['load_order']})
        try:
            for filename in m['load_order']: monitor.load(filename)
            monitor.phase = 'kgsl observation'
            print('Observing KGSL probe/bind for 60 seconds. /dev/kgsl-3d0 is not opened.', flush=True)
            monitor.observe(60, quiet=True)
            report['gpu_after'] = gpu_view()
            report['firmware_log'] = g.firmware_mentions(monitor.base_log, monitor.last_log)
            report['kgsl_log'] = [l for l in g.new_lines(monitor.base_log, monitor.last_log)
                                  if any(k in l.lower() for k in ('kgsl', 'adreno', 'gmu', 'zap', 'gpu'))]
            report['clocks_after'] = c.clocks()
            report['status'] = 'KGSL_CLOSURE_LOADED_OBSERVED_NOT_OPENED'
            print(report['status'], json.dumps(report['gpu_after']), flush=True)
        except BaseException as exc:
            report['error'] = str(exc); report['phase'] = monitor.phase
            raise
        finally:
            try: check_firmware(m); report['firmware_unchanged'] = True
            except Exception as exc: report['firmware_check_error'] = str(exc)
            report.update(loaded=monitor.loaded, reviewed_waits=monitor.waits, audit=monitor.audit, transitions=monitor.transitions)
            for key, fn in [('dmesg_after', c.dmesg), ('tasks_after', c.tasks), ('battery_after', c.battery)]:
                try: report[key] = fn()
                except Exception as exc: report[key+'_error'] = str(exc)
            save('result.json', report); print('Saved:', B/'result.json', flush=True)

if __name__ == '__main__':
    signal.signal(signal.SIGALRM, c.timeout)
    def interrupted(*_): raise RuntimeError('interrupted; loaded modules retained, inspect before continuing')
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, interrupted)
    try: main()
    except Exception as exc: sys.exit('STOP: '+str(exc))
