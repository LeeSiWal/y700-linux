#!/usr/bin/env python3
"""One-shot supervisor (S-2): start hexrpcd (Python hexagonrpcd) on /dev/fastrpc-adsp-secure with INIT_ATTACH_SNS, serve the
persist sensors registry + vendor sensors config READ-ONLY, observe 60 s, then query SSC for sensors (read-only SUID +
attributes). hexrpcd is held for the whole boot (registry-sns). Display/keepers/ADSP/USB checked every tick.
Usage: python3 -B run-sensors.py --check | sudo python3 -B run-sensors.py --apply"""
from pathlib import Path
import fcntl, hashlib, json, os, signal, stat, subprocess, sys, time
import bootmon as bm, registry as rg, qrtrns, sensorsfs

B = Path(__file__).resolve().parent
AGENT = Path('/home/siwal/y700-agent')
c, need = bm.c, bm.need
c.need = need
DEV, SYSDEV = '/dev/fastrpc-adsp-secure', '/sys/class/misc/fastrpc-adsp-secure/dev'
TYPES = ['accel', 'gyro', 'mag', 'proximity', 'ambient_light', 'hall', 'sar', 'sensor_temperature', 'pressure', 'gravity', 'game_rv']

def save(path, data):
    with Path(path).open('x') as f: f.write(json.dumps(data, indent=2, default=str)); f.flush(); os.fsync(f.fileno())
    os.chmod(path, 0o644)

def restart_pd(path, report):
    """linux drivers/soc/qcom/pdr_interface.c pdr_restart_pd(): SERVREG_RESTART_PD_REQ (0x24) to the servreg notifier
    (service 0x42) of the PD's instance, TLV 0x01 = service path; response TLV 0x02 = qmi result."""
    import select, socket, struct
    svc = [x for x in qrtrns.lookup() if x['service'] == 0x42 and x['instance'] == 74]
    need(len(svc) == 1, 'servreg notifier (0x42/74) not unique: %r' % svc)
    s = socket.socket(socket.AF_QIPCRTR, socket.SOCK_DGRAM)
    try:
        body = struct.pack('<BH', 1, len(path)) + path.encode()
        s.sendto(struct.pack('<BHHH', 0, 0x5A5A, 0x24, len(body)) + body, (svc[0]['node'], svc[0]['port']))
        r, _, _ = select.select([s], [], [], 5)
        need(r, 'no response to RESTART_PD'); d = s.recv(4096)
        typ, txn, mid, ln = struct.unpack_from('<BHHH', d); t = d[7:7 + ln]
        res = struct.unpack_from('<HH', t, 3) if len(t) >= 7 and t[0] == 2 else None
        report['restart_pd'] = {'path': path, 'reply': d.hex(), 'result': res}; print('RESTART_PD', path, res, flush=True)
        need(res == (0, 0), 'RESTART_PD refused: %r' % (res,))
    finally: s.close()

def discover(report):
    import pb, sscclient as s
    cl = s.Client(); out = {}
    try:
        for dt in TYPES:
            cl.control(s.request(s.SUID_SENSOR, s.MSG_SUID_REQ, pb.f_bytes(1, dt.encode()) + pb.f_varint(2, 0)))
            uids = []
            for uid, evs in cl.events(6.0):
                for mid, ts, msg in evs:
                    if mid == s.MSG_SUID_RESP:
                        d = pb.decode(msg)
                        if [v for f, _, v in d if f == 1][:1] == [dt.encode()]:
                            for ub in [v for f, _, v in d if f == 2]:
                                u = pb.decode(ub); uids.append((u[0][2], u[1][2]))
            attrs = []
            for uid in uids[:3]:
                cl.control(s.request(uid, s.MSG_ATTR_REQ, b''))
                for u2, evs in cl.events(3.0):
                    for mid, ts, msg in evs:
                        if u2 == uid and mid == s.MSG_ATTR_RESP:
                            a = s.parse_attrs(msg); attrs.append({k: a.get(k) for k in ('name', 'vendor', 'type', 'available', 'rates', 'stream_type', 'physical')})
            out[dt] = {'suids': ['%016x%016x' % (h, l) for l, h in uids], 'attrs': attrs}
            print('SSC %-18s %d %s' % (dt, len(uids), json.dumps(attrs)[:240]), flush=True)
    finally: cl.close()
    report['ssc'] = out; return out

def main():
    need(sys.argv[1:] in (['--check'], ['--apply']), 'use --check or --apply')
    m = json.loads(c.read(B/'manifest.json')); t = json.loads(c.read(B/'review-templates.json')); sn = m['sensors']
    for name, digest in m['files'].items():
        need(Path(name).name == name and hashlib.sha256(c.read(B/name, True)).hexdigest() == digest, 'bundle changed: ' + name)
    need(not (B/'attempt.json').exists(), 'already attempted; inspect result, do not retry')
    mon = bm.BootMonitor(m, t); mon.context()
    need('frpc_adsprpc' in bm.live_modules(), 'frpc not loaded')
    need(c.read('/sys/class/remoteproc/remoteproc1/state').strip() == 'running', 'ADSP not running')
    need(Path(SYSDEV).exists(), 'fastrpc-adsp-secure missing')
    need(sensorsfs.tree_digest(B/'rootmap.json') == tuple(sn['tree']), 'served tree changed')
    need([x for x in qrtrns.lookup() if x['service'] == 400], 'SSC service missing')
    reg, reg_sha = rg.load(c, m['boot_id'])
    others = [rg.load(c, m['boot_id'], k)[0] for k in ('gpu', 'bt', 'pdm')]
    procs = [reg['owner'], others[0]['keeper'], others[1]['keeper'], others[2]['pdmapper']]
    for p in procs: rg.process_ok(c, p)
    prev = sn.get('previous')                  # an earlier hexrpcd of this boot that this bundle replaces (SIGTERM, then re-attach)
    if prev:
        rg.process_ok(c, prev['hexrpcd']); need(hashlib.sha256(c.read(AGENT/prev['registry'], True)).hexdigest() == prev['registry_sha256'], 'previous sensors registry changed')
    sreg = AGENT/('registry-sns-%s%s.json' % (m['boot_id'][:8], '-' + B.name[-8:] if prev else '')); need(not sreg.exists(), 'sensors registry already exists')
    print('PREFLIGHT PASS: frpc bound, secure node, ADSP running, SSC present, tree %s (%d files), keepers alive.' % (sn['tree'][0][:12], sn['tree'][1]), flush=True)
    if sys.argv[1] == '--check': return
    need(os.geteuid() == 0, 'interactive sudo required')
    state = c.read(reg['native_state_file'])
    child = [None]
    def extra():
        rg.display_ok(c, reg, state)
        for p in procs[1:]: rg.process_ok(c, p)
        # (the replaced hexrpcd is not in procs)
        need(c.read('/sys/class/remoteproc/remoteproc1/state').strip() == 'running', 'ADSP left running state')
        need(c.read('/sys/class/net/%s/carrier' % sn['net']).strip() == '1', 'USB Ethernet link changed')
        if child[0]: need(child[0].poll() is None, 'hexrpcd exited')
    with (B/'apply.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        mon.bind(['adci', 'hfi', 'fence']); mon.extra = extra; extra()
        print('Observing baseline for 15 seconds.', flush=True); mon.observe(15, quiet=True)
        save(B/'attempt.json', {'boot_id': m['boot_id'], 'time': time.time()})
        report = {'boot_id': m['boot_id'], 'dmesg_before': c.dmesg()}
        try:
            if prev:
                mon.phase = 'replace hexrpcd'; pid = prev['hexrpcd']['pid']
                os.kill(pid, signal.SIGTERM); t0 = time.monotonic()
                while Path('/proc/%d' % pid).exists() and c.task_stat(c.read('/proc/%d/stat' % pid))['starttime'] == prev['hexrpcd']['starttime']:
                    need(time.monotonic() - t0 < 10, 'previous hexrpcd did not exit'); time.sleep(.2)
                report['replaced'] = pid; print('PREVIOUS_HEXRPCD_STOPPED', pid, flush=True); mon.observe(3, quiet=True)
            if sn.get('restart_pd'):
                # 7bc737b3: after the earlier refused registry write the sensors PD never asked again -> restart only that PD
                mon.phase = 'restart sensor_pd'; base = set(c.dmesg().splitlines())
                restart_pd(sn['restart_pd'], report); t0 = time.monotonic(); seen = []
                while True:
                    mon.tick(); new = [l for l in c.dmesg().splitlines() if l not in base and 'sensor_pd' in l]
                    seen = new
                    if any('is up' in l for l in new) and any('down' in l.lower() for l in new): break
                    need(time.monotonic() - t0 < 40, 'sensor_pd did not come back in 40 s: %r' % new[-5:]); time.sleep(.3)
                report['pd_log'] = seen; print('SENSOR_PD_RESTARTED', json.dumps([l.split('] ', 2)[-1][:90] for l in seen][-4:]), flush=True)
            mj, mn = (int(x) for x in c.read(SYSDEV).split(':'))
            if not Path(DEV).exists(): os.mknod(DEV, stat.S_IFCHR | 0o600, os.makedev(mj, mn))
            st = os.lstat(DEV); need(stat.S_ISCHR(st.st_mode) and st.st_rdev == os.makedev(mj, mn), 'node mismatch')
            # 9eaf2e86: the PD opens registry/DIR for WRITING first (registry regeneration). Writes go to a scratch copy of the
            # persist sensors tree inside this bundle; the device persist partition and the design copy are never written.
            import shutil
            rm = json.loads(c.read(B/'rootmap.json')); src = rm['/mnt/vendor/persist/sensors']; rw = B/'persist-rw'/'sensors'
            shutil.copytree(src, rw)
            run = {k: ({'dir': str(rw), 'rw': True} if v == src else v) for k, v in rm.items()}
            (B/'rootmap-run.json').write_text(json.dumps(run, indent=1)); report['rootmap_run'] = run
            argv = ['python3', '-B', str(B/'hexrpcd.py'), DEV, str(B/'rootmap-run.json')]
            with (B/'hexrpcd.log').open('xb', buffering=0) as out:
                child[0] = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True, cwd=str(B),
                                            env={'PATH': '/usr/bin:/bin', 'HOME': str(B), 'LANG': 'C.UTF-8'})
            os.chmod(B/'hexrpcd.log', 0o644)
            ident = {'pid': child[0].pid, 'starttime': c.task_stat(c.read('/proc/%d/stat' % child[0].pid))['starttime'], 'cmd': argv}
            report['hexrpcd'] = ident; mon.phase = 'hexrpcd'; t0 = time.monotonic()
            while b'LISTENER_READY' not in (B/'hexrpcd.log').read_bytes():
                log = (B/'hexrpcd.log').read_bytes()
                need(b'STOP' not in log, 'hexrpcd stopped: %s' % log.decode(errors='replace')[-400:])
                mon.tick(); need(time.monotonic() - t0 < 20, 'listener not ready in 20 s: %s' % log.decode(errors='replace')[-400:]); time.sleep(.3)
            print('LISTENER_READY', flush=True)
            print('Observing the sensors PD file requests for 60 s.', flush=True); mon.observe(60, quiet=True)
            report['requests'] = (B/'hexrpcd.log').read_text().splitlines()[-300:]
            print('REQUESTS', len(report['requests']), flush=True)
            mon.phase = 'ssc discovery'; found = discover(report)
            save(sreg, {'boot_id': m['boot_id'], 'created': time.time(), 'bundle': B.name, 'hexrpcd': ident})
            n = sum(1 for v in found.values() if v['suids'])
            report['status'] = 'SENSORS_%d_TYPES' % n; print(report['status'], flush=True)
        except BaseException as exc:
            report['error'] = str(exc); report['phase'] = mon.phase; raise
        finally:
            report.update(audit=mon.audit + mon.review.audit, waits=mon.waits.bound)
            try: report['hexrpcd_log'] = (B/'hexrpcd.log').read_text().splitlines()[-400:]
            except Exception: pass
            for key, fn in (('dmesg_after', c.dmesg), ('tasks_after', c.tasks), ('battery_after', c.battery)):
                try: report[key] = fn()
                except Exception as exc: report[key + '_error'] = str(exc)
            save(B/'result.json', report); print('Saved:', B/'result.json', flush=True)

if __name__ == '__main__':
    signal.signal(signal.SIGALRM, c.timeout)
    try: main()
    except Exception as exc: sys.exit('STOP: ' + str(exc))
