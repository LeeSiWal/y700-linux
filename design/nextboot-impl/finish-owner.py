#!/usr/bin/env python3
"""Complete the display-owner stage whose supervisor stopped only at registry creation (encoder selection bug).
Read-only for KMS: re-validates owner.log with the strict parser, checks the owner process, borrows its DRM fd to ask
GETCONNECTOR for the DSI connector's encoder (counts-only), observes 30 s (template guard, client = owner master,
DRM state and that encoder's underrun counters unchanged), then writes native-state.txt and the boot registry.
Usage: python3 -B finish-owner.py --check | sudo python3 -B finish-owner.py --apply"""
from pathlib import Path
import fcntl, hashlib, json, os, re, signal, sys, time
import bootmon as bm, registry as rg, drmkms as k
from ownerlog import OwnerLog
from borrow_owner import borrow_fd

B = Path(__file__).resolve().parent
AGENT = Path('/home/siwal/y700-agent')
c, need = bm.c, bm.need

def save(path, data, text=False):
    with Path(path).open('x') as f: f.write(data if text else json.dumps(data, indent=2)); f.flush(); os.fsync(f.fileno())
    os.chmod(path, 0o644)

def owner_record(m):
    ob = AGENT/m['owner_bundle']
    for name, digest in m['owner_files'].items():
        need(hashlib.sha256(c.read(ob/name, True)).hexdigest() == digest, 'owner bundle evidence changed: ' + name)
    log = OwnerLog()
    for line in c.read(ob/'owner.log').splitlines():
        ev = log.feed(line)
        if ev == 'gate1': log.gates = 1
        if ev == 'gate2': log.gates = 2
    need(log.held is not None and log.info.get('native_fb'), 'owner log incomplete')
    ident = json.loads(c.read(ob/'owner.json'))
    return log.info, {'pid': ident['pid'], 'starttime': ident['starttime'], 'fd': log.info['fd'], 'cmd': ['python3', '-B', str(ob/'display-owner.py')]}

def main():
    need(sys.argv[1:] in (['--check'], ['--apply']), 'use --check or --apply')
    m = json.loads(c.read(B/'manifest.json')); t = json.loads(c.read(B/'review-templates.json'))
    for name, digest in m['files'].items():
        need(Path(name).name == name and hashlib.sha256(c.read(B/name, True)).hexdigest() == digest, 'bundle changed: ' + name)
    need(not (B/'attempt.json').exists(), 'already attempted; inspect result, do not retry')
    mon = bm.BootMonitor(m, t); mon.context()
    info, owner = owner_record(m); rg.process_ok(c, owner)
    registry = AGENT/('registry-%s.json' % m['boot_id'][:8]); need(not registry.exists(), 'registry already exists')
    print('PREFLIGHT PASS: owner log valid (native_fb=%d), owner process alive, no registry yet.' % info['native_fb'], flush=True)
    if sys.argv[1] == '--check': return
    need(os.geteuid() == 0, 'interactive sudo required')
    with (B/'apply.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        save(B/'attempt.json', {'boot_id': m['boot_id'], 'time': time.time()})
        report = {'boot_id': m['boot_id'], 'owner': owner, 'owner_info': info}
        try:
            mon.bind(['adci', 'hfi', 'fence']); need(set(mon.waits.pid_kinds().values()) == {'adci', 'hfi', 'fence'}, 'reviewed waits not all bound')
            lines = c.read(rg.DEBUG/'clients').splitlines()
            need(len(lines) == 2 and lines[1].split()[1] == str(owner['pid']) and lines[1].split()[3] == 'y', 'owner is not the only DRM client/master')
            fd = borrow_fd(c, {'owner': owner})
            try: enc_id = k.connector_encoder(fd, info['topology']['connector'])
            finally: os.close(fd)            # duplicate only; the owner keeps its DRM file
            enc = rg.DEBUG/('encoder%d' % enc_id)/'status'
            base = rg.underruns(c, enc); need(set(base) == {1, 2}, 'encoder %d has no dual-intf underrun counters: %r' % (enc_id, base))
            state = c.read(rg.DEBUG/'state')
            need(state.count('\tfb=%d\n' % info['native_fb']) == 2, 'both planes are not on the native fb')
            report.update(encoder_id=enc_id, underrun_base=base); print('ENCODER', enc_id, 'UNDERRUN_BASE', base, flush=True)
            end = time.monotonic() + 30
            while time.monotonic() < end:
                mon.tick(); rg.process_ok(c, owner)
                need(c.read(rg.DEBUG/'state') == state, 'DRM state changed during observation')
                need(rg.underruns(c, enc) == base, 'underrun changed during observation')
                time.sleep(.5)
            save(B/'native-state.txt', state, text=True)
            reg = {'boot_id': m['boot_id'], 'created': time.time(), 'bundle': m['owner_bundle'], 'finished_by': B.name, 'owner': owner,
                   'topology': info['topology'], 'mode_blob': info['mode_blob'], 'small_fb': info['small_fb'], 'native_fb': info['native_fb'],
                   'native_state_file': str(B/'native-state.txt'), 'native_state_sha256': hashlib.sha256(state.encode()).hexdigest(),
                   'encoder_id': enc_id, 'encoder_status': str(enc), 'underrun_base': base, 'waits': mon.waits.bound}
            save(registry, reg); report.update(status='DISPLAY_OWNER_READY_AWAITING_VISUAL_CONFIRMATION', registry=str(registry))
            print(report['status'], registry, flush=True)
        except BaseException as exc:
            report['error'] = str(exc); raise
        finally:
            report.update(audit=mon.audit + mon.review.audit)
            for key, fn in (('dmesg_after', c.dmesg), ('tasks_after', c.tasks), ('battery_after', c.battery)):
                try: report[key] = fn()
                except Exception as exc: report[key + '_error'] = str(exc)
            save(B/'result.json', report); print('Saved:', B/'result.json', flush=True)

if __name__ == '__main__':
    signal.signal(signal.SIGALRM, c.timeout)
    try: main()
    except Exception as exc: sys.exit('STOP: ' + str(exc))
