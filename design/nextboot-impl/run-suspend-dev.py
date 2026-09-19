#!/usr/bin/env python3
"""One-shot supervisor S-2a/S-2b (suspend, pm_test=devices; S-2a blockers kept, S-2b USB LAN unplugged, session over Wi-Fi): prepare + suspend device callbacks, hold 5 s, resume,
without entering a sleep state. The BT UART (bt keeper vote) and the USB host with the LAN adapter stay held, so the device
phase is EXPECTED to abort at one of them after the later-added devices (Wi-Fi PCIe/MHI, USB LAN, touch, sound, hci0) were
suspended; the kernel then resumes those. Whatever happens is recorded.
Sequence: pm_test <- 'devices', state <- 'freeze' (returns after resume or abort), pm_test <- 'none' in finally.
Checks relaxed for the window around the write (recorded, not STOP): Wi-Fi association, USB LAN carrier flap, display
underrun counters. Still STOP: boot/modules/ramoops/battery, reviewed waits, dmesg review (WARNING/BUG/Call trace...),
owner/gpu/bt keepers + pdmapper alive, DRM client/master unchanged, and the native DRM state must be back within 10 s.
Usage: python3 -B run-suspend-dev.py --check | sudo python3 -B run-suspend-dev.py --apply"""
from pathlib import Path
import fcntl, hashlib, json, os, re, signal, sys, time
import bootmon as bm, registry as rg

B = Path(__file__).resolve().parent
P = Path('/sys/power')
AGENT = Path('/home/siwal/y700-agent')
c, need = bm.c, bm.need
c.need = need

def save(path, data):
    with Path(path).open('x') as f: f.write(json.dumps(data, indent=2)); f.flush(); os.fsync(f.fileno())
    os.chmod(path, 0o644)

def stats():
    return {f.name: c.read(f).strip() for f in sorted((P/'suspend_stats').iterdir())}

def selected(text):
    m = re.search(r'\[(\w+)\]', text); return m.group(1) if m else None

def wakeup_active():
    out = {}
    for w in Path('/sys/class/wakeup').iterdir():
        try:
            if int(c.read(w/'active_time_ms')) > 0: out[c.read(w/'name').strip()] = int(c.read(w/'active_time_ms'))
        except (OSError, ValueError): pass
    return out

def net(n):
    p = Path('/sys/class/net')/n
    if not p.exists(): return None
    try: return {'carrier': c.read(p/'carrier').strip(), 'oper': c.read(p/'operstate').strip()}
    except OSError: return {'carrier': None, 'oper': c.read(p/'operstate').strip()}

def main():
    need(sys.argv[1:] in (['--check'], ['--apply']), 'use --check or --apply')
    m = json.loads(c.read(B/'manifest.json')); t = json.loads(c.read(B/'review-templates.json')); s = m['suspend']
    for name, digest in m['files'].items():
        need(Path(name).name == name and hashlib.sha256(c.read(B/name, True)).hexdigest() == digest, 'bundle changed: ' + name)
    need(not (B/'attempt.json').exists(), 'already attempted; inspect result, do not retry')
    prev = json.loads(c.read(AGENT/s['previous']/'result.json'))
    need(prev['boot_id'] == m['boot_id'] and all(prev.get(k) == v for k, v in s['previous_expect'].items()),
         'previous suspend step %s did not end as reviewed: %r' % (s['previous'], s['previous_expect']))
    for n in s.get('must_be_absent', []): need(net(n) is None, 'interface must be absent (unplugged): ' + n)
    mon = bm.BootMonitor(m, t); mon.context()
    live = bm.live_modules()
    for mod in m['requires_live']: need(mod in live, 'prerequisite module not live: ' + mod)
    need('devices' in c.read(P/'pm_test') and selected(c.read(P/'pm_test')) == 'none', 'pm_test not none / devices not offered')
    st0 = stats(); need(st0 == s['stats_before'], 'suspend_stats changed since the bundle was built: %r' % st0)
    reg, reg_sha = rg.load(c, m['boot_id']); greg, _ = rg.load(c, m['boot_id'], 'gpu'); breg, _ = rg.load(c, m['boot_id'], 'bt')
    preg, _ = rg.load(c, m['boot_id'], 'pdm')
    for p in (reg['owner'], greg['keeper'], breg['keeper'], preg['pdmapper']): rg.process_ok(c, p)
    need(net(s['net']) == {'carrier': '1', 'oper': 'up'}, 'session link down: ' + s['net'])
    print('PREFLIGHT PASS: S-1 done, pm_test none, stats unchanged, keepers + pdmapper alive, wakeup active: %s'
          % json.dumps(sorted(wakeup_active())), flush=True)
    if sys.argv[1] == '--check': return
    need(os.geteuid() == 0, 'interactive sudo required')
    state = c.read(reg['native_state_file']); need(hashlib.sha256(state.encode()).hexdigest() == reg['native_state_sha256'], 'native state file changed')
    base_ur = {int(k): v for k, v in reg['underrun_base'].items()}
    relaxed = {'on': False}
    def drm_ok(strict_state=True):
        rg.process_ok(c, reg['owner'])
        lines = c.read(rg.DEBUG/'clients').splitlines()
        need(len(lines) == 2 and lines[1].split()[1] == str(reg['owner']['pid']) and lines[1].split()[3] == 'y', 'DRM client/master changed')
        if strict_state: need(c.read(rg.DEBUG/'state') == state, 'native DRM state changed')
    def extra():
        drm_ok(strict_state=not relaxed['on'])
        if not relaxed['on']:
            need(rg.underruns(c, reg['encoder_status']) == base_ur, 'display underrun counter changed')
            need(net(s['net']) == {'carrier': '1', 'oper': 'up'}, 'session link changed: ' + s['net'])
            for n in s.get('must_be_absent', []): need(net(n) is None, 'unplugged interface came back: ' + n)
        for p in (greg['keeper'], breg['keeper'], preg['pdmapper']): rg.process_ok(c, p)
    with (B/'apply.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        mon.bind(['adci', 'hfi', 'fence']); need(set(mon.waits.pid_kinds().values()) == {'adci', 'hfi', 'fence'}, 'reviewed waits not all bound')
        mon.extra = extra; extra()
        print('Observing baseline for 15 seconds.', flush=True); mon.observe(15, quiet=True)
        save(B/'attempt.json', {'boot_id': m['boot_id'], 'time': time.time(), 'registry_sha256': reg_sha})
        report = {'boot_id': m['boot_id'], 'dmesg_before': c.dmesg(), 'stats_before': st0, 'wakeup_active_before': wakeup_active(),
                  'net_before': {n: net(n) for n in ('enx<MAC>', 'wlp1s0')}, 'hci_before': Path('/sys/class/bluetooth/hci0').exists(),
                  'underrun_before': rg.underruns(c, reg['encoder_status'])}
        pm_test_set = False
        try:
            mon.phase = 'devices test'
            (P/'pm_test').write_text('devices'); pm_test_set = True
            need(selected(c.read(P/'pm_test')) == 'devices', 'pm_test readback not devices')
            relaxed['on'] = True
            print('DEVICES_TEST_ENTER (devices suspend, ~5 s hold, resume; screen may blank, SSH may pause)', flush=True)
            t0 = time.monotonic(); err = None
            try: (P/'state').write_text('freeze')
            except OSError as e: err = '%s (errno %d)' % (e.strerror, e.errno)
            report['state_write_s'] = round(time.monotonic() - t0, 2); report['state_write_error'] = err
            print('DEVICES_TEST_RETURNED %.2f s error=%s' % (report['state_write_s'], err), flush=True)
        except BaseException as exc:
            report['error'] = str(exc); report['phase'] = mon.phase; raise
        finally:
            if pm_test_set:
                (P/'pm_test').write_text('none'); report['pm_test_after'] = selected(c.read(P/'pm_test'))
                print('PM_TEST_RESTORED', report['pm_test_after'], flush=True)
            report['stats_after'] = stats()
            save(B/'phase1.json', report)
        try:
            need(report['pm_test_after'] == 'none', 'pm_test not restored')
            mon.phase = 'display return'; t0 = time.monotonic()
            while c.read(rg.DEBUG/'state') != state:
                drm_ok(strict_state=False); need(time.monotonic() - t0 < 10, 'native DRM state not back within 10 s'); time.sleep(.25)
            report['drm_state_back_s'] = round(time.monotonic() - t0, 2)
            report['underrun_after'] = rg.underruns(c, reg['encoder_status']); base_ur.clear(); base_ur.update(report['underrun_after'])
            print('DRM_STATE_BACK %.2f s underrun %s -> %s' % (report['drm_state_back_s'], report['underrun_before'], report['underrun_after']), flush=True)
            mon.phase = 'lan return'; t0 = time.monotonic()
            while net(s['net']) != {'carrier': '1', 'oper': 'up'}:
                need(time.monotonic() - t0 < 90, 'session link %s not back within 90 s: %r' % (s['net'], net(s['net']))); time.sleep(.5)
            report['lan_back_s'] = round(time.monotonic() - t0, 2)
            relaxed['on'] = False
            seen = set(report['dmesg_before'].splitlines()); new = [l for l in c.dmesg().splitlines() if l not in seen]
            report['pm_log'] = [l for l in new if re.search(r'PM: |[Ff]reez|Restarting tasks|suspend|resume|abort|failed|error -', l)][-120:]
            report['failed'] = [l for l in new if re.search(r'failed to suspend|returns -\d+|Some devices failed|abort', l)]
            for l in report['pm_log'][-40:]: print('  ', l, flush=True)
            print('Observing after the test for 30 s.', flush=True); mon.phase = 'after'; mon.observe(30, quiet=True)
            d = {k: (report['stats_before'][k], v) for k, v in report['stats_after'].items() if report['stats_before'].get(k) != v}
            report['stats_diff'] = d; print('SUSPEND_STATS_DIFF', json.dumps(d), flush=True)
            report['net_after'] = {n: net(n) for n in ('enx<MAC>', 'wlp1s0')}; report['hci_after'] = Path('/sys/class/bluetooth/hci0').exists()
            print('NET', json.dumps(report['net_after']), 'hci0', report['hci_after'], flush=True)
            report['status'] = 'DEVICES_TEST_ABORTED_BY_DEVICE' if report['state_write_error'] else 'DEVICES_TEST_DONE'
            print(report['status'], json.dumps(report['failed'][:6]), flush=True)
        except BaseException as exc:
            report['error'] = str(exc); report['phase'] = mon.phase; raise
        finally:
            report.update(audit=mon.audit + mon.review.audit, waits=mon.waits.bound, wakeup_active_after=wakeup_active())
            for key, fn in (('dmesg_after', c.dmesg), ('tasks_after', c.tasks), ('battery_after', c.battery)):
                try: report[key] = fn()
                except Exception as exc: report[key + '_error'] = str(exc)
            save(B/'result.json', report); print('Saved:', B/'result.json', flush=True)

class Tee:
    """stdout -> terminal (errors ignored: the SSH session may drop during the test) + run.log in the bundle"""
    def __init__(self, term): self.term = term; self.log = None
    def write(self, x):
        if self.log is None and (B/'attempt.json').exists(): self.log = (B/'run.log').open('a')
        if self.log: self.log.write(x); self.log.flush()
        try: self.term.write(x)
        except OSError: pass
        return len(x)
    def flush(self):
        try: self.term.flush()
        except OSError: pass

if __name__ == '__main__':
    sys.stdout = Tee(sys.stdout)
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM): signal.signal(sig, signal.SIG_IGN)
    signal.signal(signal.SIGALRM, c.timeout)
    try: main()
    except Exception as exc: sys.exit('STOP: ' + str(exc))
