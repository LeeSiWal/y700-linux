#!/usr/bin/env python3
"""One-shot supervisor for gpu-keeper on a new boot (after load-stage gpu and display-owner). Creates /dev/kgsl-3d0 from the
sysfs (dynamic) major:minor (0600 root), releases one GO, verifies DEVICE_INFO (chip 0x44050a31) and writes the GPU registry.
The display owner, its native state and underrun counters are checked every tick. Never closes the kgsl fd.
Usage: python3 -B run-keeper.py --check | sudo python3 -B run-keeper.py --apply"""
from pathlib import Path
import fcntl, hashlib, json, os, signal, stat, subprocess, sys, time
import bootmon as bm, registry as rg
from keeperlog import KeeperLog

B = Path(__file__).resolve().parent
AGENT = Path('/home/siwal/y700-agent')
c, need = bm.c, bm.need
c.need = need
interrupted = False
GPU = {'kgsl': '3d00000.qcom,kgsl-3d0', 'gmu': '3d37000.qcom,gmu', 'iommu': '3da0000.qcom,kgsl-iommu'}
EXPECT = {'kgsl': 'kgsl-3d', 'gmu': 'adreno-gen8-gmu', 'iommu': 'kgsl-iommu'}

def save(path, data):
    with Path(path).open('x') as f: f.write(json.dumps(data, indent=2)); f.flush(); os.fsync(f.fileno())
    os.chmod(path, 0o644)

def gpu_bound():
    return {k: ((Path('/sys/bus/platform/devices')/d/'driver').resolve().name if (Path('/sys/bus/platform/devices')/d/'driver').is_symlink() else None) for k, d in GPU.items()}

def keeper_ok(child, identity):
    need(child.poll() is None, 'gpu-keeper exited: its kgsl fd was closed by exit; inspect, no retry')
    p = Path('/proc')/str(child.pid)
    need(c.task_stat(c.read(p/'stat'))['starttime'] == identity['starttime'], 'keeper PID reused')

def main():
    need(sys.argv[1:] in (['--check'], ['--apply']), 'use --check or --apply')
    m = json.loads(c.read(B/'manifest.json')); t = json.loads(c.read(B/'review-templates.json'))
    for name, digest in m['files'].items():
        need(Path(name).name == name and hashlib.sha256(c.read(B/name, True)).hexdigest() == digest, 'bundle changed: ' + name)
    need(not (B/'attempt.json').exists(), 'already attempted; inspect result, do not retry')
    mon = bm.BootMonitor(m, t); mon.context()
    need('msm_kgsl' in bm.live_modules() and gpu_bound() == EXPECT, 'GPU stage not loaded/bound: %r' % gpu_bound())
    reg, reg_sha = rg.load(c, m['boot_id']); rg.process_ok(c, reg['owner'])
    gpu_reg = AGENT/('registry-gpu-%s.json' % m['boot_id'][:8]); need(not gpu_reg.exists(), 'GPU registry already exists')
    node = Path('/dev/kgsl-3d0'); need(not node.exists() and not node.is_symlink(), '/dev/kgsl-3d0 already exists')
    for name, digest in m['gpu']['firmware'].items():
        p = Path('/lib/firmware')/name
        need(p.is_file() and hashlib.sha256(c.read(p, True)).hexdigest() == digest, 'firmware missing/changed: ' + name)
    print('PREFLIGHT PASS: KGSL bound, owner alive, firmware hashes, no kgsl node, no GPU registry.', flush=True)
    if sys.argv[1] == '--check': return
    need(os.geteuid() == 0, 'interactive sudo required')
    state = c.read(reg['native_state_file']); need(hashlib.sha256(state.encode()).hexdigest() == reg['native_state_sha256'], 'native state file changed')
    with (B/'apply.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        mon.bind(['adci', 'hfi', 'fence']); need(set(mon.waits.pid_kinds().values()) == {'adci', 'hfi', 'fence'}, 'reviewed waits not all bound')
        rg.display_ok(c, reg, state)
        mon.observe(15, quiet=True); rg.display_ok(c, reg, state)
        save(B/'attempt.json', {'boot_id': m['boot_id'], 'time': time.time(), 'registry_sha256': reg_sha})
        report = {'boot_id': m['boot_id'], 'dmesg_before': c.dmesg(), 'gpu_before': gpu_bound()}
        child = None
        try:
            dev = c.read('/sys/class/kgsl/kgsl-3d0/dev').strip(); major, minor = (int(x) for x in dev.split(':'))
            os.mknod(node, stat.S_IFCHR | 0o600, os.makedev(major, minor))
            st = os.lstat(node); need(stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(major, minor) and st.st_uid == 0, 'node readback')
            report['node'] = dev; print('KGSL_NODE', dev, flush=True)
            with (B/'keeper.log').open('xb', buffering=0) as out:
                child = subprocess.Popen(['python3', '-B', str(B/'gpu-keeper.py'), dev], stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT,
                                         start_new_session=True, cwd=str(B), env={'PATH': '/usr/bin:/bin', 'HOME': str(B), 'LANG': 'C.UTF-8'})
            os.chmod(B/'keeper.log', 0o644)
            identity = {'pid': child.pid, 'starttime': c.task_stat(c.read(Path('/proc')/str(child.pid)/'stat'))['starttime'],
                        'cmd': ['python3', '-B', str(B/'gpu-keeper.py'), dev]}
            report['keeper'] = identity
            log = KeeperLog(); offset = 0; pending = b''; ready = go = held = None; start = time.monotonic()
            while True:
                now = time.monotonic(); mon.tick(); keeper_ok(child, identity); rg.display_ok(c, reg, state)
                need(not interrupted, 'interrupted; keeper retained')
                raw = (B/'keeper.log').read_bytes(); pending += raw[offset:]; offset = len(raw)
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1); line = line.decode(errors='replace'); print(line, flush=True)
                    ev = log.feed(line)
                    if ev == 'ready': ready = now
                    if ev == 'held': held = now
                if ready is not None and go is None and now - ready >= 2:
                    mon.observe(2, quiet=True); keeper_ok(child, identity); go = now; log.go_sent = True
                    print('OPEN_GATE_RELEASED', flush=True); child.stdin.write(b'GO\n'); child.stdin.flush()
                if go is not None and held is None: need(now - go < 30, 'first open/devinfo did not finish in 30 s; keeper retained')
                if held is not None and now - held >= 60:
                    mon.observe(2, quiet=True); keeper_ok(child, identity); rg.display_ok(c, reg, state)
                    greg = {'boot_id': m['boot_id'], 'created': time.time(), 'bundle': B.name, 'keeper': {**identity, 'fd': log.fd},
                            'node': dev, 'devinfo': log.devinfo, 'open_ms': log.open_ms, 'display_registry_sha256': reg_sha}
                    save(gpu_reg, greg); report.update(status='GPU_KEEPER_READY', devinfo=log.devinfo, open_ms=log.open_ms, gpu_registry=str(gpu_reg))
                    print('GPU_KEEPER_READY', json.dumps(log.devinfo), flush=True)
                    break
                need(now - start < 150, 'keeper trial timeout; keeper retained')
                time.sleep(.25)
        except BaseException as exc:
            report['error'] = str(exc); report['phase'] = mon.phase; raise
        finally:
            if child is not None:
                try: child.stdin.close()
                except Exception: pass
            report.update(audit=mon.audit + mon.review.audit, waits=mon.waits.bound)
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
