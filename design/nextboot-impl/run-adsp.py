#!/usr/bin/env python3
"""One-shot supervisor (B-2): install the reviewed ADSP firmware into /lib/firmware (only absent or identical files),
start pdmapper (servreg locator, held for the whole boot), set remoteproc1 recovery=disabled (a crash stays visible
instead of looping), write 'start' to remoteproc1 and observe 90 s: state running, ADSP glink/QRTR services,
locator lookups. Display owner/native state/underruns, gpu-keeper, bt-keeper, USB link, Wi-Fi and battery are checked
every tick. No audio modules are loaded here. Never stops the ADSP or the pdmapper.
Usage: python3 -B run-adsp.py --check | sudo python3 -B run-adsp.py --apply"""
from pathlib import Path
import fcntl, hashlib, json, os, signal, subprocess, sys, time
import bootmon as bm, registry as rg, qrtrns

B = Path(__file__).resolve().parent
AGENT = Path('/home/siwal/y700-agent')
FW = Path('/lib/firmware')
c, need = bm.c, bm.need
c.need = need
interrupted = False

def save(path, data):
    with Path(path).open('x') as f: f.write(json.dumps(data, indent=2)); f.flush(); os.fsync(f.fileno())
    os.chmod(path, 0o644)

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def fw_state(files):
    """{name: 'absent' | 'same' | 'DIFFERENT'} for the firmware set."""
    out = {}
    for n, h in files.items():
        p = FW/n
        out[n] = 'absent' if not p.exists() else ('same' if p.is_file() and not p.is_symlink() and sha(p) == h else 'DIFFERENT')
    return out

def proc_ok(child, identity, what):
    need(child.poll() is None, what + ' exited; inspect, no retry')
    need(c.task_stat(c.read(Path('/proc')/str(child.pid)/'stat'))['starttime'] == identity['starttime'], what + ' PID reused')

def main():
    need(sys.argv[1:] in (['--check'], ['--apply']), 'use --check or --apply')
    m = json.loads(c.read(B/'manifest.json')); t = json.loads(c.read(B/'review-templates.json')); a = m['adsp']
    for name, digest in m['files'].items():
        need(Path(name).name == name and hashlib.sha256(c.read(B/name, True)).hexdigest() == digest, 'bundle changed: ' + name)
    need(not (B/'attempt.json').exists(), 'already attempted; inspect result, do not retry')
    mon = bm.BootMonitor(m, t); mon.context()
    live = bm.live_modules()
    for mod in m['requires_live']: need(mod in live, 'prerequisite module not live: ' + mod)
    rp = Path('/sys/class/remoteproc')/a['rproc']
    need(c.read(rp/'name').strip() == a['rproc_name'] and c.read(rp/'firmware').strip() == 'adsp.mdt', 'remoteproc identity changed')
    need(c.read(rp/'state').strip() == 'offline', 'ADSP not offline')
    fs = fw_state(a['firmware']); need('DIFFERENT' not in fs.values(), 'a different firmware file is already installed: %r' % {k: v for k, v in fs.items() if v == 'DIFFERENT'})
    svcs = qrtrns.lookup(); need(not [s for s in svcs if s['service'] == 0x40], 'a servreg locator is already published: %r' % svcs)
    reg, reg_sha = rg.load(c, m['boot_id']); greg, _ = rg.load(c, m['boot_id'], 'gpu'); breg, _ = rg.load(c, m['boot_id'], 'bt')
    for p in (reg['owner'], greg['keeper'], breg['keeper']): rg.process_ok(c, p)
    pdm_reg = AGENT/('registry-pdm-%s.json' % m['boot_id'][:8]); need(not pdm_reg.exists(), 'pdmapper registry already exists')
    need(c.read(Path('/sys/class/net')/a['net']/'carrier').strip() == '1', 'USB Ethernet link down')
    print('PREFLIGHT PASS: ADSP offline, firmware %s, no locator, owner/gpu/bt keepers alive.' % json.dumps(sorted(set(fs.values()))), flush=True)
    if sys.argv[1] == '--check': return
    need(os.geteuid() == 0, 'interactive sudo required')
    state = c.read(reg['native_state_file']); need(hashlib.sha256(state.encode()).hexdigest() == reg['native_state_sha256'], 'native state file changed')
    wlan = lambda: c.read('/sys/class/net/wlp1s0/operstate').strip() if Path('/sys/class/net/wlp1s0').exists() else None
    wlan0 = wlan()
    children = []
    def extra():
        rg.display_ok(c, reg, state); rg.process_ok(c, greg['keeper']); rg.process_ok(c, breg['keeper'])
        n = Path('/sys/class/net')/a['net']
        need(c.read(n/'carrier').strip() == '1' and c.read(n/'operstate').strip() == 'up', 'USB Ethernet link changed')
        need(wlan() == wlan0, 'Wi-Fi interface state changed')
        for ch, ident, what in children: proc_ok(ch, ident, what)
    with (B/'apply.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        mon.bind(['adci', 'hfi', 'fence']); need(set(mon.waits.pid_kinds().values()) == {'adci', 'hfi', 'fence'}, 'reviewed waits not all bound')
        mon.extra = extra; extra()
        print('Observing baseline for 15 seconds.', flush=True); mon.observe(15, quiet=True)
        save(B/'attempt.json', {'boot_id': m['boot_id'], 'time': time.time(), 'registry_sha256': reg_sha})
        report = {'boot_id': m['boot_id'], 'dmesg_before': c.dmesg(), 'services_before': svcs, 'wlan_before': wlan0,
                  'rpmsg_before': sorted(p.name for p in Path('/sys/bus/rpmsg/devices').iterdir())}
        try:
            mon.phase = 'firmware install'; installed = []
            for n, h in a['firmware'].items():
                if (FW/n).exists(): continue
                data = (B/n).read_bytes(); need(hashlib.sha256(data).hexdigest() == h, 'bundle firmware changed: ' + n)
                with open(FW/n, 'xb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
                os.chmod(FW/n, 0o644); installed.append(n)
            need(set(fw_state(a['firmware']).values()) == {'same'}, 'firmware readback mismatch')
            report['firmware_installed'] = installed; print('FIRMWARE installed=%d present=%d' % (len(installed), len(a['firmware'])), flush=True)
            mon.phase = 'pdmapper'
            argv = ['python3', '-B', str(B/'pdmapper.py')] + [str(B/j) for j in a['jsn']]
            with (B/'pdmapper.log').open('xb', buffering=0) as out:
                child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True, cwd=str(B),
                                         env={'PATH': '/usr/bin:/bin', 'HOME': str(B), 'LANG': 'C.UTF-8'})
            os.chmod(B/'pdmapper.log', 0o644)
            ident = {'pid': child.pid, 'starttime': c.task_stat(c.read(Path('/proc')/str(child.pid)/'stat'))['starttime'], 'cmd': argv}
            children.append((child, ident, 'pdmapper')); report['pdmapper'] = ident
            t0 = time.monotonic()
            while b'READY' not in (B/'pdmapper.log').read_bytes():
                mon.tick(); need(time.monotonic() - t0 < 10, 'pdmapper not ready in 10 s'); time.sleep(.2)
            loc = [s for s in qrtrns.lookup() if s['service'] == 0x40]; report['locator'] = loc
            need(sorted(s['instance'] for s in loc) == [0, 1], 'locator not visible in the name service: %r' % loc)
            save(pdm_reg, {'boot_id': m['boot_id'], 'created': time.time(), 'bundle': B.name, 'pdmapper': ident})
            print('PDMAPPER_READY', json.dumps(loc), flush=True)
            mon.observe(3, quiet=True)
            mon.phase = 'adsp start'
            (rp/'recovery').write_text('disabled'); report['recovery'] = c.read(rp/'recovery').strip()
            need(report['recovery'] == 'disabled', 'recovery not disabled')
            t0 = time.monotonic(); (rp/'state').write_text('start'); report['start_write_ms'] = round((time.monotonic() - t0) * 1e3, 1)
            print('ADSP_START written (%.1f ms)' % report['start_write_ms'], flush=True)
            while c.read(rp/'state').strip() != 'running':
                mon.tick(); need(time.monotonic() - t0 < 30, 'ADSP not running in 30 s: ' + c.read(rp/'state').strip()); time.sleep(.2)
            report['running_ms'] = round((time.monotonic() - t0) * 1e3, 1); print('ADSP_RUNNING %.1f ms' % report['running_ms'], flush=True)
            print('Observing ADSP for 90 s.', flush=True); mon.observe(90, quiet=True)
            need(c.read(rp/'state').strip() == 'running', 'ADSP left running state')
            report['adsp'] = {'state': c.read(rp/'state').strip(), 'services': qrtrns.lookup(),
                              'rpmsg_new': sorted(set(p.name for p in Path('/sys/bus/rpmsg/devices').iterdir()) - set(report['rpmsg_before'])),
                              'pdmapper_log': (B/'pdmapper.log').read_text().splitlines()[-60:],
                              'battery': {k: c.read('/sys/class/power_supply/battery/' + k).strip() for k in ('capacity', 'status', 'temp')},
                              'log': [l for l in c.dmesg().splitlines() if any(k in l.lower() for k in ('adsp', 'remoteproc', 'glink', 'qrtr', 'pdr', 'servreg', 'sysmon', 'q6v5', 'ssr'))][-150:]}
            print('ADSP', json.dumps({k: v for k, v in report['adsp'].items() if k not in ('log', 'pdmapper_log')}), flush=True)
            report['status'] = 'ADSP_RUNNING_OBSERVED'; print('ADSP_RUNNING_OBSERVED', flush=True)
        except BaseException as exc:
            report['error'] = str(exc); report['phase'] = mon.phase; raise
        finally:
            report.update(audit=mon.audit + mon.review.audit, waits=mon.waits.bound, wlan_after=wlan(), adsp_state_final=c.read(rp/'state').strip())
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
