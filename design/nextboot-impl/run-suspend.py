#!/usr/bin/env python3
"""One-shot supervisor S-1 (suspend, pm_test=freezer): freeze all freezable tasks, hold 5 s (kernel suspend_test), thaw.
No device suspend callbacks run (pm_test stops before dpm_suspend_start), the display, GPU, Wi-Fi, BT and ADSP stay powered.
Sequence: pm_test <- 'freezer', state <- 'freeze' (s2idle; mem_sleep untouched), the write returns after the thaw;
pm_test <- 'none' is restored in finally. wakeup_count is NOT written (reading it blocks while wakeup sources are
active), so active wakeup sources do not abort the attempt; whatever the kernel does is recorded.
Checked every tick: boot/modules/ramoops/battery, bound reviewed waits, dmesg review, display owner + native state +
underruns, gpu/bt keepers, pdmapper, USB Ethernet link, Wi-Fi state. Observes 15 s before and 30 s after.
Usage: python3 -B run-suspend.py --check | sudo python3 -B run-suspend.py --apply"""
from pathlib import Path
import fcntl, hashlib, json, os, re, signal, sys, time
import bootmon as bm, registry as rg

B = Path(__file__).resolve().parent
P = Path('/sys/power')
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

def main():
    need(sys.argv[1:] in (['--check'], ['--apply']), 'use --check or --apply')
    m = json.loads(c.read(B/'manifest.json')); t = json.loads(c.read(B/'review-templates.json')); s = m['suspend']
    for name, digest in m['files'].items():
        need(Path(name).name == name and hashlib.sha256(c.read(B/name, True)).hexdigest() == digest, 'bundle changed: ' + name)
    need(not (B/'attempt.json').exists(), 'already attempted; inspect result, do not retry')
    mon = bm.BootMonitor(m, t); mon.context()
    live = bm.live_modules()
    for mod in m['requires_live']: need(mod in live, 'prerequisite module not live: ' + mod)
    need('freeze' in c.read(P/'state').split(), 'freeze state not offered')
    need(selected(c.read(P/'pm_test')) == 'none', 'pm_test is not none')
    need('freezer' in c.read(P/'pm_test'), 'pm_test freezer not offered')
    st0 = stats(); need(st0 == s['stats_before'], 'suspend_stats changed since the bundle was built: %r' % st0)
    reg, reg_sha = rg.load(c, m['boot_id']); greg, _ = rg.load(c, m['boot_id'], 'gpu'); breg, _ = rg.load(c, m['boot_id'], 'bt')
    preg, _ = rg.load(c, m['boot_id'], 'pdm')
    for p in (reg['owner'], greg['keeper'], breg['keeper'], preg['pdmapper']): rg.process_ok(c, p)
    need(c.read(Path('/sys/class/net')/s['net']/'carrier').strip() == '1', 'USB Ethernet link down')
    print('PREFLIGHT PASS: pm_test none, suspend_stats unchanged, owner/gpu/bt keepers + pdmapper alive, wakeup active: %s'
          % json.dumps(sorted(wakeup_active())), flush=True)
    if sys.argv[1] == '--check': return
    need(os.geteuid() == 0, 'interactive sudo required')
    state = c.read(reg['native_state_file']); need(hashlib.sha256(state.encode()).hexdigest() == reg['native_state_sha256'], 'native state file changed')
    wlan = lambda: c.read('/sys/class/net/wlp1s0/operstate').strip() if Path('/sys/class/net/wlp1s0').exists() else None
    hci = lambda: Path('/sys/class/bluetooth/hci0').exists()
    wlan0, hci0 = wlan(), hci()
    def extra():
        rg.display_ok(c, reg, state)
        for p in (greg['keeper'], breg['keeper'], preg['pdmapper']): rg.process_ok(c, p)
        n = Path('/sys/class/net')/s['net']
        need(c.read(n/'carrier').strip() == '1' and c.read(n/'operstate').strip() == 'up', 'USB Ethernet link changed')
        need(wlan() == wlan0, 'Wi-Fi interface state changed'); need(hci() == hci0, 'hci0 presence changed')
    with (B/'apply.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        mon.bind(['adci', 'hfi', 'fence']); need(set(mon.waits.pid_kinds().values()) == {'adci', 'hfi', 'fence'}, 'reviewed waits not all bound')
        mon.extra = extra; extra()
        print('Observing baseline for 15 seconds.', flush=True); mon.observe(15, quiet=True)
        save(B/'attempt.json', {'boot_id': m['boot_id'], 'time': time.time(), 'registry_sha256': reg_sha})
        report = {'boot_id': m['boot_id'], 'dmesg_before': c.dmesg(), 'stats_before': st0, 'wakeup_active_before': wakeup_active(),
                  'mem_sleep': c.read(P/'mem_sleep').strip(), 'wlan_before': wlan0, 'hci_before': hci0}
        pm_test_set = False
        try:
            mon.phase = 'freezer test'
            (P/'pm_test').write_text('freezer'); pm_test_set = True
            need(selected(c.read(P/'pm_test')) == 'freezer', 'pm_test readback not freezer')
            print('FREEZER_TEST_ENTER (tasks freeze for ~5 s, the SSH session pauses)', flush=True)
            t0 = time.monotonic(); err = None
            try: (P/'state').write_text('freeze')
            except OSError as e: err = '%s (errno %d)' % (e.strerror, e.errno)
            report['state_write_s'] = round(time.monotonic() - t0, 2); report['state_write_error'] = err
            print('FREEZER_TEST_RETURNED %.2f s error=%s' % (report['state_write_s'], err), flush=True)
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
            seen = set(report['dmesg_before'].splitlines()); new = '\n'.join(l for l in c.dmesg().splitlines() if l not in seen)
            report['pm_log'] = [l for l in new.splitlines() if re.search(r'PM: |Freezing|freez|Restarting tasks|suspend|OOM killer|Filesystems sync', l)][-60:]
            for l in report['pm_log']: print('  ', l, flush=True)
            print('Observing after the test for 30 s.', flush=True); mon.observe(30, quiet=True)
            d = {k: (report['stats_before'][k], v) for k, v in report['stats_after'].items() if report['stats_before'].get(k) != v}
            report['stats_diff'] = d; print('SUSPEND_STATS_DIFF', json.dumps(d), flush=True)
            froze = any('Freezing user space processes completed' in l or 'Freezing remaining freezable tasks completed' in l for l in report['pm_log'])
            report['status'] = 'FREEZER_TEST_DONE' if froze and not report['state_write_error'] else 'FREEZER_TEST_NOT_COMPLETED'
            print(report['status'], flush=True)
        except BaseException as exc:
            report['error'] = str(exc); report['phase'] = 'after'; raise
        finally:
            report.update(audit=mon.audit + mon.review.audit, waits=mon.waits.bound, wlan_after=wlan(), hci_after=hci(), wakeup_active_after=wakeup_active())
            for key, fn in (('dmesg_after', c.dmesg), ('tasks_after', c.tasks), ('battery_after', c.battery)):
                try: report[key] = fn()
                except Exception as exc: report[key + '_error'] = str(exc)
            save(B/'result.json', report); print('Saved:', B/'result.json', flush=True)

if __name__ == '__main__':
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM): signal.signal(sig, signal.SIG_IGN)
    signal.signal(signal.SIGALRM, c.timeout)
    try: main()
    except Exception as exc: sys.exit('STOP: ' + str(exc))
