#!/usr/bin/env python3
"""One-shot supervisor for bt-keeper (after load-stage bt-a, bt-probe, bt-b). Verifies the /dev/btpower and /dev/ttyHS0 nodes
(sysfs major:minor, 0600 root; created by bt-probe or here), releases one GO, follows the keeper log (power on, version,
rampatch/NVM download, HCI checks, H4 attach), then observes hci0 for 30 s and writes registry-bt-<boot>.json.
Display owner/native state/underruns, gpu-keeper, USB link and Wi-Fi are checked every tick. Never kills the keeper.
Usage: python3 -B run-btkeeper.py --check | sudo python3 -B run-btkeeper.py --apply"""
from pathlib import Path
import fcntl, hashlib, json, os, signal, stat, subprocess, sys, time
import bootmon as bm, registry as rg

B = Path(__file__).resolve().parent
AGENT = Path('/home/siwal/y700-agent')
c, need = bm.c, bm.need
c.need = need
interrupted = False
NODES = {'/dev/btpower': '/sys/class/bt-dev/btpower/dev', '/dev/ttyHS0': '/sys/class/tty/ttyHS0/dev'}

def save(path, data):
    with Path(path).open('x') as f: f.write(json.dumps(data, indent=2)); f.flush(); os.fsync(f.fileno())
    os.chmod(path, 0o644)

def node_ok(path, sysdev, create=False):
    dev = c.read(sysdev).strip(); mj, mn = (int(x) for x in dev.split(':')); p = Path(path)
    if create and not p.exists(): os.mknod(p, stat.S_IFCHR | 0o600, os.makedev(mj, mn))
    if not p.exists(): return dev
    st = os.lstat(p)
    need(stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(mj, mn) and st.st_uid == 0 and st.st_mode & 0o777 == 0o600, 'node mismatch: ' + path)
    return dev

def holders(devs, allowed=()):
    out = []
    for fd in Path('/proc').glob('[0-9]*/fd/*'):
        try:
            st = os.stat(fd)
            if stat.S_ISCHR(st.st_mode) and '%d:%d' % (os.major(st.st_rdev), os.minor(st.st_rdev)) in devs and fd.parts[2] not in allowed: out.append(str(fd))
        except OSError: pass
    return out

def keeper_ok(child, identity):
    need(child.poll() is None, 'bt-keeper exited (fds closed by exit); inspect, no retry')
    need(c.task_stat(c.read(Path('/proc')/str(child.pid)/'stat'))['starttime'] == identity['starttime'], 'keeper PID reused')

def hci_list():
    p = Path('/sys/class/bluetooth'); return sorted(x.name for x in p.iterdir()) if p.is_dir() else None

def main():
    need(sys.argv[1:] in (['--check'], ['--apply']), 'use --check or --apply')
    m = json.loads(c.read(B/'manifest.json')); t = json.loads(c.read(B/'review-templates.json')); b = m['bt']
    for name, digest in m['files'].items():
        need(Path(name).name == name and hashlib.sha256(c.read(B/name, True)).hexdigest() == digest, 'bundle changed: ' + name)
    need(not (B/'attempt.json').exists(), 'already attempted; inspect result, do not retry')
    mon = bm.BootMonitor(m, t); mon.context()
    live = bm.live_modules()
    for mod in m['requires_live']: need(mod in live, 'prerequisite module not live: ' + mod)
    P = Path('/sys/bus/platform/devices')
    need((P/b['uart']/'driver').resolve().name == 'msm_geni_serial' and (P/b['power']/'driver').resolve().name == 'bt_power', 'bt-a incomplete')
    need(hci_list() == [], 'HCI device already present or bluetooth class missing: %r' % hci_list())
    devs = {path: node_ok(path, sysdev) for path, sysdev in NODES.items()}
    kept = set()
    for k in b['retained_keepers']:
        rg.process_ok(c, k); kept.add(str(k['pid']))                        # still holding (power off confirmed in its log)
        need(hashlib.sha256(c.read(AGENT/k['bundle']/'keeper.log', True)).hexdigest() == k['keeper_log_sha256'], 'retained keeper log changed')
    need(not holders({devs['/dev/ttyHS0']}), 'ttyHS0 already open')
    need(not holders(set(devs.values()), kept), 'btpower/ttyHS0 open by an unreviewed process')
    reg, reg_sha = rg.load(c, m['boot_id']); greg, _ = rg.load(c, m['boot_id'], 'gpu')
    rg.process_ok(c, reg['owner']); rg.process_ok(c, greg['keeper'])
    bt_reg = AGENT/('registry-bt-%s.json' % m['boot_id'][:8]); need(not bt_reg.exists(), 'BT registry already exists')
    need(c.read(Path('/sys/class/net')/b['net']/'carrier').strip() == '1', 'USB Ethernet link down')
    print('PREFLIGHT PASS: bt-a/bt-b live, no hci, nodes free, firmware hashes, owner+gpu keeper alive.', flush=True)
    if sys.argv[1] == '--check': return
    need(os.geteuid() == 0, 'interactive sudo required')
    state = c.read(reg['native_state_file']); need(hashlib.sha256(state.encode()).hexdigest() == reg['native_state_sha256'], 'native state file changed')
    wlan = lambda: c.read(Path('/sys/class/net')/b['wlan']/'operstate').strip() if (Path('/sys/class/net')/b['wlan']).exists() else None
    wlan0 = wlan()
    def extra():
        rg.display_ok(c, reg, state); rg.process_ok(c, greg['keeper'])
        n = Path('/sys/class/net')/b['net']
        need(c.read(n/'carrier').strip() == '1' and c.read(n/'operstate').strip() == 'up', 'USB Ethernet link changed')
        need(wlan() == wlan0, 'Wi-Fi interface state changed')
    with (B/'apply.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        mon.bind(['adci', 'hfi', 'fence']); need(set(mon.waits.pid_kinds().values()) == {'adci', 'hfi', 'fence'}, 'reviewed waits not all bound')
        mon.extra = extra; extra()
        print('Observing baseline for 15 seconds.', flush=True); mon.observe(15, quiet=True)
        save(B/'attempt.json', {'boot_id': m['boot_id'], 'time': time.time(), 'registry_sha256': reg_sha})
        report = {'boot_id': m['boot_id'], 'dmesg_before': c.dmesg(), 'wlan_before': wlan0}
        child = None
        try:
            devs = {path: node_ok(path, sysdev, create=True) for path, sysdev in NODES.items()}; report['nodes'] = devs
            e = b['expect']
            argv = ['python3', '-B', str(B/'bt-keeper.py'), devs['/dev/btpower'], devs['/dev/ttyHS0'], hex(e['product_id']), hex(e['rom_ver']), hex(e['soc_id'])]
            with (B/'keeper.log').open('xb', buffering=0) as out:
                child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT, start_new_session=True, cwd=str(B),
                                         env={'PATH': '/usr/bin:/bin', 'HOME': str(B), 'LANG': 'C.UTF-8'})
            os.chmod(B/'keeper.log', 0o644)
            identity = {'pid': child.pid, 'starttime': c.task_stat(c.read(Path('/proc')/str(child.pid)/'stat'))['starttime'], 'cmd': argv}
            report['keeper'] = identity
            offset = 0; pending = b''; ready = go = attached = None; held = None; lines = []; start = time.monotonic()
            mon.phase = 'bt-keeper'
            while True:
                now = time.monotonic(); mon.tick(); keeper_ok(child, identity)
                need(not interrupted, 'interrupted; keeper retained')
                raw = (B/'keeper.log').read_bytes(); pending += raw[offset:]; offset = len(raw)
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1); line = line.decode(errors='replace'); print(line, flush=True); lines.append(line)
                    if line == 'READY': ready = now
                    if line.startswith('HCI_ATTACHED '): attached = (now, line.split()[1])
                    if line.startswith('HELD '): held = line
                    if line.startswith('STOP'): report['keeper_stop'] = line
                if held and not held.endswith('reason=BT_ATTACHED'):
                    need(False, 'bt-keeper stopped: %s (power off attempted; fds held)' % held)
                if ready is not None and go is None and now - ready >= 2:
                    mon.observe(2, quiet=True); keeper_ok(child, identity); go = now
                    print('GO_RELEASED', flush=True); child.stdin.write(b'GO\n'); child.stdin.flush()
                if go is not None and attached is None: need(now - go < 420, 'download/attach did not finish in 420 s; keeper retained')
                if attached is not None and now - attached[0] >= 30:
                    mon.observe(2, quiet=True); keeper_ok(child, identity)
                    h = attached[1]; hp = Path('/sys/class/bluetooth')/h
                    need(hp.exists(), h + ' not present after 30 s')
                    info = {'hci': hci_list(), 'rfkill': sorted((x.name, c.read(x/'name').strip(), c.read(x/'soft').strip(), c.read(x/'hard').strip())
                                                                for x in Path('/sys/class/rfkill').iterdir()),
                            'tty_holders': holders({devs['/dev/ttyHS0']}),
                            'log': [l for l in c.dmesg().splitlines() if 'Bluetooth' in l or 'hci' in l.lower() or 'bt_power' in l or 'BTON' in l][-80:]}
                    report['bt'] = info
                    save(bt_reg, {'boot_id': m['boot_id'], 'created': time.time(), 'bundle': B.name, 'keeper': identity, 'hci': h,
                                  'nodes': devs, 'display_registry_sha256': reg_sha})
                    report.update(status='BT_KEEPER_READY', bt_registry=str(bt_reg))
                    print('BT', json.dumps({k: v for k, v in info.items() if k != 'log'}), flush=True)
                    print('BT_KEEPER_READY', h, flush=True)
                    break
                need(now - start < 540, 'bt-keeper trial timeout; keeper retained')
                time.sleep(.25)
        except BaseException as exc:
            report['error'] = str(exc); report['phase'] = mon.phase; raise
        finally:
            if child is not None:
                try: child.stdin.close()
                except Exception: pass
            report.update(audit=mon.audit + mon.review.audit, waits=mon.waits.bound, wlan_after=wlan())
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
