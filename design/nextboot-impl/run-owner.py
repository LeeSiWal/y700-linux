#!/usr/bin/env python3
"""One-shot supervisor for display-owner on a new boot (after load-stage display). Creates /dev/dri/card0 (226:0, 0600) if
absent, requires that no DRM client exists (owner must be the first master), releases gate A (modeset) and gate B (native
two-plane) after quiet observation, then writes the boot registry. Never kills the owner, never closes a DRM fd.
Usage: python3 -B run-owner.py --check | sudo python3 -B run-owner.py --apply"""
from pathlib import Path
import fcntl, hashlib, json, os, signal, stat, subprocess, sys, time
import bootmon as bm
from ownerlog import OwnerLog

B = Path(__file__).resolve().parent
AGENT = Path('/home/siwal/y700-agent')
c, need = bm.c, bm.need
DEBUG = Path('/sys/kernel/debug/dri/0')
interrupted = False

def save(path, data, text=False):
    with Path(path).open('x') as f:
        f.write(data if text else json.dumps(data, indent=2)); f.flush(); os.fsync(f.fileno())
    os.chmod(path, 0o644)

def clients():
    lines = c.read(DEBUG/'clients').splitlines()
    need(lines and lines[0].split() == ['command', 'tgid', 'dev', 'master', 'a', 'uid', 'magic'], 'unexpected DRM clients format')
    return [x.split() for x in lines[1:]]

def encoder_status(identity, info):
    """Status file of the DSI connector's encoder, via GETCONNECTOR on a borrowed owner fd (several encoders expose
    underrun counters; b4e16b26/3a1d6dac: 6 such files)."""
    import drmkms as k
    from borrow_owner import borrow_fd
    fd = borrow_fd(c, {'owner': {**identity, 'fd': info['fd'], 'cmd': ['python3', '-B', str(B/'display-owner.py')]}})
    try: enc_id = k.connector_encoder(fd, info['topology']['connector'])
    finally: os.close(fd)
    p = DEBUG/('encoder%d' % enc_id)/'status'; need(p.exists(), 'encoder status missing: %s' % p)
    return p

def underruns(p):
    import re
    d = {int(i): int(n) for i, n in re.findall(r'intf:(\d+)\s+vsync:\s+\d+\s+underrun:\s+(\d+)', c.read(p))}
    need(d, 'no underrun counters'); return d

def make_node():
    need(c.read('/sys/class/drm/card0/dev').strip() == '226:0', 'card0 device number changed')
    d = Path('/dev/dri'); need(not d.is_symlink(), '/dev/dri is a symlink'); d.mkdir(mode=0o755, exist_ok=True)
    p = d/'card0'
    if not p.exists(): os.mknod(p, stat.S_IFCHR | 0o600, os.makedev(226, 0))
    st = os.lstat(p); need(stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(226, 0) and st.st_uid == 0, 'card0 node mismatch')

def owner_ok(child, identity):
    need(child.poll() is None, 'display-owner exited: its DRM fd was closed by exit; inspect, no retry')
    p = Path('/proc')/str(child.pid)
    need(c.task_stat(c.read(p/'stat'))['starttime'] == identity['starttime'], 'owner PID reused')
    need(c.read(p/'cmdline', True).split(b'\0')[:3] == [b'python3', b'-B', str(B/'display-owner.py').encode()], 'owner command changed')

def main():
    need(sys.argv[1:] in (['--check'], ['--apply']), 'use --check or --apply')
    m = json.loads(c.read(B/'manifest.json')); t = json.loads(c.read(B/'review-templates.json'))
    for name, digest in m['files'].items():
        need(Path(name).name == name and hashlib.sha256(c.read(B/name, True)).hexdigest() == digest, 'bundle changed: ' + name)
    need(not (B/'attempt.json').exists(), 'already attempted; inspect result, do not retry')
    mon = bm.BootMonitor(m, t); mon.context()
    need('msm_drm' in bm.live_modules() and Path('/sys/class/drm/card0').exists(), 'display stage not loaded')
    need(c.read('/sys/class/drm/card0-DSI-1/status').strip() == 'connected', 'panel not connected')
    registry = AGENT/('registry-%s.json' % m['boot_id'][:8]); need(not registry.exists(), 'registry already exists for this boot')
    print('PREFLIGHT PASS: display stage loaded, panel connected, no registry yet.', flush=True)
    if sys.argv[1] == '--check': return
    need(os.geteuid() == 0, 'interactive sudo required')
    with (B/'apply.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        mon.bind(['adci', 'hfi', 'fence']); need(set(mon.waits.pid_kinds().values()) == {'adci', 'hfi', 'fence'}, 'reviewed waits not all bound')
        need(clients() == [], 'a DRM client already exists; the owner must be the first master')
        mon.observe(15, quiet=True)
        save(B/'attempt.json', {'boot_id': m['boot_id'], 'time': time.time()})
        report = {'boot_id': m['boot_id'], 'dmesg_before': c.dmesg(), 'state_before': c.read(DEBUG/'state')}
        child = None
        try:
            make_node(); print('CARD0_NODE_READY', flush=True)
            with (B/'owner.log').open('xb', buffering=0) as out:
                child = subprocess.Popen(['python3', '-B', str(B/'display-owner.py')], stdin=subprocess.PIPE, stdout=out,
                                         stderr=subprocess.STDOUT, start_new_session=True, cwd=str(B),
                                         env={'PATH': '/usr/bin:/bin', 'HOME': str(B), 'LANG': 'C.UTF-8'})
            os.chmod(B/'owner.log', 0o644)
            identity = {'pid': child.pid, 'starttime': c.task_stat(c.read(Path('/proc')/str(child.pid)/'stat'))['starttime']}
            report['owner'] = identity; save(B/'owner.json', identity)
            log = OwnerLog(); offset = 0; pending = b''; gate_at = None; retA = retB = None; start = time.monotonic(); enc = None; base = None
            while True:
                now = time.monotonic(); mon.tick(); owner_ok(child, identity)
                need(not interrupted, 'interrupted; owner retained')
                raw = (B/'owner.log').read_bytes(); pending += raw[offset:]; offset = len(raw)
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1); line = line.decode(errors='replace'); print(line, flush=True)
                    ev = log.feed(line)
                    if ev in ('gate1', 'gate2'): gate_at = now
                    if ev == 'returnedA': retA = now; report['state_after_A'] = c.read(DEBUG/'state')
                    if ev == 'returnedB':
                        retB = now; enc = encoder_status(identity, log.info); base = underruns(enc); report['underrun_base'] = base
                        need(set(base) == {1, 2}, 'DSI encoder lacks dual-intf underrun counters: %r' % base)
                    if ev == 'held': report['owner_info'] = log.info
                if log.i >= 1:   # after FIRST_DRM_OPEN: exactly one client, the owner, as master
                    cl = clients(); need(len(cl) == 1 and cl[0][1] == str(child.pid) and cl[0][3] == 'y', 'DRM client/master is not the owner: %r' % cl)
                if enc: need(underruns(enc) == base, 'underrun counter changed after commit B')
                if gate_at is not None and log.gates == 0 and now - gate_at >= 2:
                    mon.observe(2, quiet=True); owner_ok(child, identity); log.gates = 1; gate_at = None
                    print('COMMIT_A_GATE_RELEASED', flush=True); child.stdin.write(b'GO\n'); child.stdin.flush()
                if gate_at is not None and log.gates == 1 and retA is not None and now - retA >= 20:
                    mon.observe(2, quiet=True); owner_ok(child, identity); log.gates = 2; gate_at = None
                    print('COMMIT_B_GATE_RELEASED', flush=True); child.stdin.write(b'GO\n'); child.stdin.flush()
                if log.held is not None and retB is not None and now - retB >= 30:
                    mon.observe(2, quiet=True); owner_ok(child, identity)
                    state = c.read(DEBUG/'state'); save(B/'native-state.txt', state, text=True)
                    reg = {'boot_id': m['boot_id'], 'created': time.time(), 'bundle': B.name, 'encoder_id': int(enc.parent.name[7:]),
                           'owner': {**identity, 'fd': log.info['fd'], 'cmd': ['python3', '-B', str(B/'display-owner.py')]},
                           'topology': log.info['topology'], 'mode_blob': log.info['mode_blob'], 'small_fb': log.info['small_fb'],
                           'native_fb': log.info['native_fb'], 'native_state_file': str(B/'native-state.txt'),
                           'native_state_sha256': hashlib.sha256(state.encode()).hexdigest(), 'encoder_status': str(enc),
                           'underrun_base': base, 'waits': mon.waits.bound}
                    save(registry, reg); report['registry'] = str(registry)
                    report['status'] = 'DISPLAY_OWNER_READY_AWAITING_VISUAL_CONFIRMATION'; print(report['status'], registry, flush=True)
                    break
                need(now - start < 240, 'owner trial timeout; owner retained')
                time.sleep(.25)
        except BaseException as exc:
            report['error'] = str(exc); report['phase'] = mon.phase; raise
        finally:
            if child is not None:
                try: child.stdin.close()   # EOF denies any unsent gate; the owner holds
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
