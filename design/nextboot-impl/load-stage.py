#!/usr/bin/env python3
"""One-shot boot-stage module loader (pstore | display | gpu) for a NEW boot. Loads the reviewed modules in the fixed order
with insmod, binds reviewed idle waits by template, reviews the kernel log by template, and never unloads anything.
Usage: python3 -B load-stage.py --check | sudo python3 -B load-stage.py --apply"""
from pathlib import Path
import fcntl, hashlib, json, os, signal, subprocess, sys, time
import bootmon as bm

B = Path(__file__).resolve().parent
c, need = bm.c, bm.need
TCK = os.sysconf('SC_CLK_TCK')

def save(name, data):
    with (B/name).open('x') as f: json.dump(data, f, indent=2); f.flush(); os.fsync(f.fileno())
    os.chmod(B/name, 0o644)

def ticks_now(): return int(float(c.read('/proc/uptime').split()[0]) * TCK)

def gpu_view():
    v = {}
    for k, d in (('kgsl', '3d00000.qcom,kgsl-3d0'), ('gmu', '3d37000.qcom,gmu'), ('iommu', '3da0000.qcom,kgsl-iommu')):
        p = Path('/sys/bus/platform/devices')/d/'driver'; v[k] = p.resolve().name if p.is_symlink() else None
    v['gpucc_state_synced'] = c.read('/sys/bus/platform/devices/3d90000.clock-controller/state_synced').strip()
    return v

def unbound_consumers(supplier):
    out = []
    for l in Path('/sys/bus/platform/devices', supplier).glob('consumer:*'):
        dev = (l/'consumer').resolve()
        if not (dev/'driver').is_symlink(): out.append(l.name.split(':', 1)[1])
    return sorted(out)

def drm_state():
    p = Path('/sys/kernel/debug/dri/0/state')
    return c.read(p) if p.exists() else None

def preflight(m, mon):
    for name, digest in m['files'].items():
        need(Path(name).name == name and hashlib.sha256(c.read(B/name, True)).hexdigest() == digest, 'bundle changed: ' + name)
    need(not (B/'attempt.json').exists(), 'already attempted; inspect result, do not retry')
    mon.context()
    live = bm.live_modules()
    need(not ({f[:-3].replace('-', '_') for f in m['load_order']} & live), 'a stage module is already loaded')
    for mod in m['requires_live']: need(mod in live, 'prerequisite module not live: ' + mod)
    if m['stage'] in ('wifi-a', 'wifi-b'):
        w = m['wifi']
        for rel, digest in w['firmware'].items():
            p = Path('/lib/firmware')/rel
            need(p.is_file() and not p.is_symlink() and hashlib.sha256(c.read(p, True)).hexdigest() == digest, 'Wi-Fi firmware missing/changed: ' + rel)
        need(w['net'] is None or c.read(Path('/sys/class/net')/w['net']/'carrier').strip() == '1', 'management link down')
        for sup in w['guard_suppliers']:
            others = [x for x in unbound_consumers(sup) if 'cnss' not in x and 'pcie' not in x]
            need(others, 'only Wi-Fi/PCIe consumers left unbound for %s: binding could trigger its sync_state; review first' % sup)
        for sup, allowed in w['pcie_only_suppliers'].items():
            cons = sorted(l.name.split(':', 1)[1].split(':', 1)[1] for l in Path('/sys/bus/platform/devices', sup).glob('consumer:*'))
            need(cons == sorted(allowed), 'consumer set of %s changed: %r' % (sup, cons))
            need((Path('/sys/bus/platform/devices')/allowed[0]/'driver').is_symlink(), allowed[0] + ' not bound')
        regs = list((Path('/sys/bus/platform/devices')/w['wcal']/'regulator').glob('regulator.*'))
        need(len(regs) == 1 and c.read(regs[0]/'num_users').strip() == '0' and c.read(regs[0]/'state').strip() == 'disabled',
             'vrm-wcal (sync_state follows the cnss bind) is not idle/disabled')
        if m['stage'] == 'wifi-a':
            for dev in (w['pcie'], w['cnss']):
                need(not (Path('/sys/bus/platform/devices')/dev/'driver').exists(), dev + ' already bound')
            need(not list(Path('/sys/bus/pci/devices').iterdir()), 'PCI devices already present')
        else:
            for dev in (w['pcie'], w['cnss']):
                need((Path('/sys/bus/platform/devices')/dev/'driver').is_symlink(), dev + ' not bound (wifi-a incomplete)')
            need(not Path('/sys/class/net/wlan0').exists(), 'wlan0 already exists')
    if m['stage'] == 'adc':
        ad = m['adc']; vd = Path('/sys/bus/platform/devices')/ad['vadc']
        need(vd.exists() and not (vd/'driver').exists(), 'vadc device missing or already bound')
        need(not list(Path('/sys/bus/iio/devices').iterdir()), 'IIO devices already present')
        have = {c.read(z/'type').strip() for z in Path('/sys/class/thermal').glob('thermal_zone*')}
        need(not have & set(ad['zones']), 'ADC thermal zones already registered: %r' % sorted(have & set(ad['zones'])))
        need(ad['net'] is None or c.read(Path('/sys/class/net')/ad['net']/'carrier').strip() == '1', 'management link down')
    if m['stage'] == 'frpc':
        fr = m['frpc']; rp = Path('/sys/bus/rpmsg/devices')/fr['rpmsg']
        need(c.read(Path('/sys/class/remoteproc')/fr['rproc']/'state').strip() == 'running', 'ADSP not running')
        need(rp.exists() and not (rp/'driver').exists(), 'fastrpc rpmsg channel missing or already bound')
        need(not list(Path('/sys/class/misc').glob('fastrpc*')), 'fastrpc device already exists')
        need(fr['net'] is None or c.read(Path('/sys/class/net')/fr['net']/'carrier').strip() == '1', 'management link down')
    if m['stage'] in ('audio-d1', 'audio-d2', 'audio-d3', 'audio-d4', 'audio-d5'):
        au = m['audio']; import registry as rg1
        need(c.read(Path('/sys/class/remoteproc')/au['rproc']/'state').strip() == 'running', 'ADSP not running')
        for kind, key in (('pdm', 'pdmapper'), ('bt', 'keeper')): rg1.process_ok(c, rg1.load(c, m['boot_id'], kind)[0][key])
        need(Path('/sys/class/aud_pasthru_adsp/aud_pasthru_adsp/dev').exists(), 'audio-pkt device missing')
        if m['stage'] in ('audio-d3', 'audio-d4', 'audio-d5'):
            need(Path('/sys/class/sound/pcmC0D14p/dev').exists(), 'speaker BE pcm missing')
            need(not [x for x in Path('/proc/asound/card0').glob('pcm*/sub*/status') if 'closed' not in c.read(x)], 'a PCM substream is open')
        if m['stage'] == 'audio-d2':
            need(Path('/sys/class/msm_audio_ion/msm_audio_ion/dev').exists() and Path('/sys/class/dma_heap/system/dev').exists(), 'ion/dma-heap device missing')
        need('canoeqrdsndcard' in c.read('/proc/asound/cards'), 'sound card missing')
        need(au['net'] is None or c.read(Path('/sys/class/net')/au['net']/'carrier').strip() == '1', 'management link down')
    if m['stage'] == 'audio-c4':
        au = m['audio']; import registry as rg1; P = Path('/sys/bus/platform/devices')
        need(c.read(Path('/sys/class/remoteproc')/au['rproc']/'state').strip() == 'running', 'ADSP not running')
        for kind, key in (('pdm', 'pdmapper'), ('bt', 'keeper')): rg1.process_ok(c, rg1.load(c, m['boot_id'], kind)[0][key])
        need((P/au['extdisp_codec']/'driver').is_symlink() and not (P/au['sound']/'driver').exists() and not (P/au['btswr']/'driver').exists(), 'C-3 state changed')
        need(Path('/sys/class/bluetooth/hci0').exists(), 'hci0 missing')
        need(not Path('/proc/asound/cards').exists() or 'no soundcards' in c.read('/proc/asound/cards'), 'a sound card already exists')
        need(au['net'] is None or c.read(Path('/sys/class/net')/au['net']/'carrier').strip() == '1', 'management link down')
    if m['stage'] == 'audio-c3':
        au = m['audio']; import registry as rg1; P = Path('/sys/bus/platform/devices')
        need(c.read(Path('/sys/class/remoteproc')/au['rproc']/'state').strip() == 'running', 'ADSP not running')
        preg, _ = rg1.load(c, m['boot_id'], 'pdm'); rg1.process_ok(c, preg['pdmapper'])
        need(not (P/au['sound']/'driver').exists() and not (P/au['extdisp_codec']/'driver').exists(), 'card or ext-disp codec already bound')
        for a in au['amps']: need((Path('/sys/bus/i2c/devices')/a/'driver').is_symlink(), 'amp not bound (C-2 state changed): ' + a)
        need(not Path('/proc/asound/cards').exists() or 'no soundcards' in c.read('/proc/asound/cards'), 'a sound card already exists')
        need(au['net'] is None or c.read(Path('/sys/class/net')/au['net']/'carrier').strip() == '1', 'management link down')
    if m['stage'] in ('audio-c2', 'audio-c'):
        au = m['audio']; import registry as rg1; P = Path('/sys/bus/platform/devices')
        need(c.read(Path('/sys/class/remoteproc')/au['rproc']/'state').strip() == 'running', 'ADSP not running')
        preg, _ = rg1.load(c, m['boot_id'], 'pdm'); rg1.process_ok(c, preg['pdmapper'])
        need((P/'soc:spf_core_platform'/'driver').is_symlink() and not (P/au['lpi']/'driver').exists(), 'C-1 state changed (spf_core_platform bound, lpi waiting)')
        for a in au['amps']: need(not (Path('/sys/bus/i2c/devices')/a/'driver').exists(), 'amp already bound: ' + a)
        for n, h in au['firmware_install'].items():
            fp = Path('/lib/firmware')/n; need(not fp.exists() or hashlib.sha256(c.read(fp, True)).hexdigest() == h, 'different firmware installed: ' + n)
        need(not Path('/proc/asound/cards').exists() or 'no soundcards' in c.read('/proc/asound/cards'), 'a sound card already exists')
        need(au['net'] is None or c.read(Path('/sys/class/net')/au['net']/'carrier').strip() == '1', 'management link down')
    if m['stage'] == 'audio-c1':
        au = m['audio']; import registry as rg1
        need(c.read(Path('/sys/class/remoteproc')/au['rproc']/'state').strip() == 'running', 'ADSP not running')
        preg, _ = rg1.load(c, m['boot_id'], 'pdm'); rg1.process_ok(c, preg['pdmapper'])
        gp = Path('/sys/bus/rpmsg/devices')/au['gpr_rpmsg']; need(gp.exists() and not (gp/'driver').exists(), 'adsp_apps channel missing or already bound')
        need(not Path('/proc/asound/cards').exists() or 'no soundcards' in c.read('/proc/asound/cards'), 'a sound card already exists')
        need(au['net'] is None or c.read(Path('/sys/class/net')/au['net']/'carrier').strip() == '1', 'management link down')
    if m['stage'] == 'qrtr-smd':
        q = m['qrtr']; rp = Path('/sys/bus/rpmsg/devices')/q['soccp_rpmsg']
        need(rp.exists() and not (rp/'driver').exists(), 'SoCCP IPCRTR channel missing or already bound')
        need(sorted(p.name for p in Path('/sys/bus/rpmsg/devices').iterdir() if p.name.endswith('.IPCRTR.-1.-1')) == [q['soccp_rpmsg']], 'unexpected IPCRTR channel set')
        need(c.read(Path('/sys/class/remoteproc')/q['soccp_rproc']/'state').strip() == 'attached', 'SoCCP not attached')
        need(q['net'] is None or c.read(Path('/sys/class/net')/q['net']/'carrier').strip() == '1', 'management link down')
    if m['stage'] == 'blescan':
        import registry as rg0
        breg, _ = rg0.load(c, m['boot_id'], 'bt'); rg0.process_ok(c, breg['keeper'])
        need(breg['hci'] == m['bt']['hci'] and sorted(p.name for p in Path('/sys/class/bluetooth').iterdir()) == [m['bt']['hci']], 'hci set changed')
        need(m['bt']['net'] is None or c.read(Path('/sys/class/net')/m['bt']['net']/'carrier').strip() == '1', 'management link down')
    if m['stage'] == 'bt-b':
        P = Path('/sys/bus/platform/devices')
        need((P/m['bt']['uart']/'driver').resolve().name == 'msm_geni_serial' and (P/m['bt']['power']/'driver').resolve().name == 'bt_power', 'bt-a incomplete')
        need(not Path('/sys/class/bluetooth').exists(), '/sys/class/bluetooth already exists')
        need(m['bt']['net'] is None or c.read(Path('/sys/class/net')/m['bt']['net']/'carrier').strip() == '1', 'management link down')
    if m['stage'] == 'bt-probe':
        P = Path('/sys/bus/platform/devices')
        need((P/m['bt']['uart']/'driver').resolve().name == 'msm_geni_serial' and (P/m['bt']['power']/'driver').resolve().name == 'bt_power', 'bt-a incomplete')
        need(Path('/sys/class/tty/ttyHS0/dev').exists() and Path('/sys/class/bt-dev/btpower/dev').exists(), 'ttyHS0/btpower missing')
        need(not list(Path('/sys/class').glob('bluetooth/hci*')), 'an HCI device already exists')
        need(m['bt']['net'] is None or c.read(Path('/sys/class/net')/m['bt']['net']/'carrier').strip() == '1', 'management link down')
    if m['stage'] == 'bt-a':
        b = m['bt']; P = Path('/sys/bus/platform/devices')
        for dev in (b['uart'], b['power']): need(not (P/dev/'driver').exists(), dev + ' already bound')
        need(not list(Path('/sys/class/tty').glob('ttyHS*')), 'ttyHS already exists')
        need(b['net'] is None or c.read(Path('/sys/class/net')/b['net']/'carrier').strip() == '1', 'management link down')
        cv = P/b['clk_virt']
        cons = sorted(l.name.split(':', 1)[1].split(':', 1)[1] for l in cv.glob('consumer:*'))
        need(cons == sorted(b['clk_virt_others'] + [b['uart']]), 'clk_virt consumer set changed: %r' % cons)
        need(all((P/d/'driver').is_symlink() for d in b['clk_virt_others']), 'a reviewed clk_virt consumer is not bound')
        need(c.read(cv/'state_synced').strip() == '0', 'clk_virt already synced')
        for r in b['regulators']:
            ub = [x.split(':', 1)[1] for x in unbound_consumers(r) if x.split(':', 1)[1] != b['power']]
            need(ub == [b['regulators_pending_via']], 'regulator %s would sync_state (unbound consumers %r)' % (r, ub))
    if m['stage'] == 'touch':
        for name, digest in m['touch']['firmware'].items():
            p = Path('/lib/firmware')/name
            need(p.is_file() and not p.is_symlink() and hashlib.sha256(c.read(p, True)).hexdigest() == digest, 'touch firmware missing/changed: ' + name)
        need(not list(Path('/sys/bus/spi/devices').iterdir()), 'SPI devices already exist')
        need(not (Path('/sys/bus/platform/devices')/m['touch']['spi_controller']/'driver').exists(), 'SPI controller already bound')
        for sup in m['touch']['must_keep_pending']:
            others = [x for x in unbound_consumers(sup) if 'spi' not in x]
            need(others, 'only the SPI controller left unbound for %s: its bind could trigger a global sync_state; review first' % sup)
    if m['stage'] == 'gpu':
        for name, digest in m['gpu']['firmware'].items():
            p = Path('/lib/firmware')/name
            need(p.is_file() and not p.is_symlink() and hashlib.sha256(c.read(p, True)).hexdigest() == digest, 'firmware missing/changed: ' + name)
        need(gpu_view() == m['gpu']['expected_before'], 'GPU devices not in reviewed pre-load state: ' + json.dumps(gpu_view()))
        for sup in m['gpu']['must_keep_pending']:
            others = [x for x in unbound_consumers(sup) if 'kgsl' not in x and 'gmu' not in x]
            need(others, 'only GPU consumers left for %s: loading KGSL could trigger its global sync_state; review first' % sup)

def load(mon, m, filename, report):
    mon.observe(2, quiet=True)
    mod = filename[:-3].replace('-', '_'); mon.phase = filename
    start = ticks_now()
    print('LOAD', filename, flush=True)
    with (B/(filename + '.log')).open('x') as f:
        p = subprocess.Popen(['/sbin/insmod', str(B/filename)], stdout=f, stderr=subprocess.STDOUT)
        mon.insmod_mod = mod; mon.review.load_module = mod; mon.review.insmod_tid = p.pid; t0 = time.monotonic()
        try:
            while p.poll() is None:
                mon.tick(); need(time.monotonic() - t0 < 25, 'insmod timeout; process retained, no retry'); time.sleep(.2)
            f.flush(); os.fsync(f.fileno())
            need(p.returncode == 0, 'insmod failed: ' + filename)
            mon.loaded[mod] = m['load_notes'][mod]; mon.refresh_buildids(); mon.tick()
        finally:
            if p.poll() is None: mon.audit.append({'event': 'incomplete_insmod', 'pid': p.pid, 'module': mod})
    mon.insmod_mod = None; mon.review.load_module = None; mon.review.insmod_tid = None
    need(c.read(Path('/sys/module')/mod/'initstate').strip() == 'live', 'module not live: ' + mod)
    for kind, after in m['bind_after'].items():
        if after == mod: mon.pending[kind] = start
    mon.observe(3, quiet=True)
    for kind, after in m['bind_after'].items():
        if after == mod:
            print('Waiting up to 130 s for the reviewed %s idle wait to bind.' % kind, flush=True)
            t1 = time.monotonic()
            while kind in mon.pending:
                mon.tick(); need(time.monotonic() - t1 < 130, 'expected reviewed wait not observed: ' + kind); time.sleep(.5)
    if mod == 'qcom_dynamic_ramoops':
        need(Path('/sys/bus/platform/drivers/ramoops/ramoops').is_symlink(), 'ramoops not bound'); mon.ramoops_required = True
    report['loads'].append({'module': mod, 'start_ticks': start})

def main():
    need(sys.argv[1:] in (['--check'], ['--apply']), 'use --check or --apply')
    m = json.loads(c.read(B/'manifest.json')); t = json.loads(c.read(B/'review-templates.json'))
    mon = bm.BootMonitor(m, t); mon.ramoops_required = m['stage'] != 'pstore'
    preflight(m, mon)
    print('PREFLIGHT PASS: stage=%s boot=%s modules=%d files=%d' % (m['stage'], m['boot_id'][:8], len(m['module_names']), len(m['files'])), flush=True)
    if sys.argv[1] == '--check': return
    need(os.geteuid() == 0, 'interactive sudo required')
    with (B/'apply.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        mon.bind(['adci']); need(mon.waits.pid_kinds(), 'ADCI idle wait not found or not unique; review before loading')
        for kind in m['bind_now']: mon.bind([kind]); need(kind in mon.waits.pid_kinds().values(), 'reviewed wait missing: ' + kind)
        drm_before = drm_state()
        if m['stage'] in ('touch', 'wifi-a', 'wifi-b', 'bt-a', 'bt-probe', 'bt-b', 'blescan', 'qrtr-smd', 'audio-c1', 'audio-c', 'audio-c2', 'audio-c3', 'audio-c4', 'audio-d1', 'audio-d2', 'audio-d3', 'audio-d4', 'audio-d5', 'frpc', 'adc'):
            import registry as rg
            c.need = need
            reg, _ = rg.load(c, m['boot_id']); greg, _ = rg.load(c, m['boot_id'], 'gpu')
            native = c.read(reg['native_state_file'])
            def display_extra():
                rg.display_ok(c, reg, native); rg.process_ok(c, greg['keeper'])
                if 'audio' in m:
                    need(c.read(Path('/sys/class/remoteproc')/m['audio']['rproc']/'state').strip() == 'running', 'ADSP left running state')
                    for kind, key in (('pdm', 'pdmapper'), ('bt', 'keeper')): rg.process_ok(c, rg.load(c, m['boot_id'], kind)[0][key])
                if 'wifi' in m or 'bt' in m or 'qrtr' in m or 'audio' in m or 'frpc' in m or 'adc' in m:
                    net = (m.get('wifi') or m.get('bt') or m.get('qrtr') or m.get('audio') or m.get('frpc') or m.get('adc'))['net']
                    n = net and Path('/sys/class/net')/net
                    need(not n or c.read(n/'carrier').strip() == '1' and c.read(n/'operstate').strip() == 'up', 'management link changed')
            display_extra(); mon.extra = display_extra; drm_before = None   # exact native state is checked every tick instead
        print('Observing baseline for 15 seconds.', flush=True); mon.observe(15, quiet=True)
        report = {'boot_id': m['boot_id'], 'stage': m['stage'], 'loads': [], 'dmesg_before': c.dmesg(), 'clocks_before': c.clocks(),
                  'drm_state_before': drm_before, 'gpu_before': gpu_view() if m['stage'] == 'gpu' else None}
        save('attempt.json', {'boot_id': m['boot_id'], 'time': time.time(), 'load_order': m['load_order']})
        try:
            if m['stage'] == 'wifi-b' and m['wifi'].get('calibration_done'):
                report['calibration'] = {'outcome': 'ok', 'already_done_this_boot': m['wifi']['calibration_done']}
                print('CALIBRATION already done this boot; fs_ready not written:', m['wifi']['calibration_done'], flush=True)
            elif m['stage'] == 'wifi-b':
                # cnss2 defers the WLAN driver until cold-boot calibration (qcom,wlan-cbc-enabled) finishes; calibration starts
                # when userspace declares the filesystem ready (Android init/cnss-daemon write 1 to fs_ready).
                import re
                base = set(c.dmesg().splitlines()); mon.phase = 'cnss calibration'
                Path('/sys/kernel/cnss/fs_ready').write_text('1'); report['fs_ready_written'] = time.time()
                print('FS_READY written; waiting up to 180 s for cold-boot calibration.', flush=True)
                t0 = time.monotonic(); outcome = None
                while outcome is None:
                    mon.tick(); need(time.monotonic() - t0 < 180, 'calibration outcome not seen in 180 s; nothing further loaded')
                    new = [l for l in c.dmesg().splitlines() if l not in base and 'cnss' in l.lower()]
                    for l in new:
                        if re.search(r'Calibration completed successfully|Calibration took', l): outcome = 'ok'
                        if re.search(r'Calibration failed|Timeout waiting for calibration|Calibration deferred|Calibration start failed', l): outcome = 'fail: ' + l
                    time.sleep(.5)
                report['calibration'] = {'outcome': outcome, 'seconds': round(time.monotonic() - t0, 1),
                                         'log': [l for l in c.dmesg().splitlines() if l not in base and 'cnss' in l.lower()][-120:]}
                print('CALIBRATION', outcome, flush=True)
                need(outcome == 'ok', 'calibration did not succeed: %s' % outcome)
                mon.observe(5, quiet=True)
            for n, h in m.get('audio', {}).get('firmware_install', {}).items():
                fp = Path('/lib/firmware')/n
                if not fp.exists():
                    data = c.read(B/n, True); need(hashlib.sha256(data).hexdigest() == h, 'bundle firmware changed: ' + n)
                    with open(fp, 'xb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
                    os.chmod(fp, 0o644); report.setdefault('firmware_installed', []).append(n)
                need(hashlib.sha256(c.read(fp, True)).hexdigest() == h, 'firmware readback mismatch: ' + n)
            for filename in m['load_order']:
                load(mon, m, filename, report)
                if drm_before is not None: need(drm_state() == drm_before, 'DRM state changed during ' + filename)
            mon.phase = 'post-load observation'
            if m['stage'] == 'display':
                print('Observing display initialisation for 130 s. No KMS.', flush=True); mon.observe(130, quiet=True)
                need(Path('/sys/class/drm/card0').exists(), 'DRM card not registered')
                need(c.read('/sys/class/drm/card0-DSI-1/status').strip() == 'connected', 'panel not connected')
                need(c.read('/sys/class/drm/card0-DSI-1/modes').splitlines()[0] == '1904x3040x120vid', 'native mode missing')
                report['card0_dev'] = c.read('/sys/class/drm/card0/dev').strip()
            if m['stage'] == 'gpu':
                print('Observing KGSL probe/bind for 60 s. /dev/kgsl-3d0 is not opened.', flush=True); mon.observe(60, quiet=True)
                v = gpu_view(); report['gpu_after'] = v
                need(v == m['gpu']['expected_after'], 'GPU bind state differs: ' + json.dumps(v))
                report['kgsl_dev'] = c.read('/sys/class/kgsl/kgsl-3d0/dev').strip()
            if m['stage'] == 'touch':
                print('Observing SPI/touch probe for 60 s (firmware download may run asynchronously).', flush=True); mon.observe(60, quiet=True)
                spi = sorted(p.name for p in Path('/sys/bus/spi/devices').iterdir())
                drv = {d: ((Path('/sys/bus/spi/devices')/d/'driver').resolve().name if (Path('/sys/bus/spi/devices')/d/'driver').is_symlink() else None) for d in spi}
                report['spi_devices'] = drv; report['input_devices'] = c.read('/proc/bus/input/devices')
                report['nvt_log'] = [l for l in c.dmesg().splitlines() if 'NVT' in l or 'nvt' in l]
                report['dev_input'] = sorted(str(p) for p in Path('/dev/input').rglob('*'))
                print('SPI_DEVICES', json.dumps(drv), flush=True)
                need(drv and all(v == 'NVT-ts' for v in drv.values()), 'touch SPI device not bound to NVT-ts')
                need('NVT' in report['input_devices'] or 'nvt' in report['input_devices'].lower(), 'no NVT input device registered')
            if m['stage'] in ('wifi-a', 'wifi-b'):
                w = m['wifi']; secs = 90 if m['stage'] == 'wifi-a' else 60
                print('Observing Wi-Fi bring-up for %d s.' % secs, flush=True); mon.observe(secs, quiet=True)
                bound = {d: ((Path('/sys/bus/platform/devices')/d/'driver').resolve().name if (Path('/sys/bus/platform/devices')/d/'driver').is_symlink() else None)
                         for d in (w['pcie'], w['cnss'])}
                report['wifi'] = {'bound': bound, 'pci': sorted(p.name for p in Path('/sys/bus/pci/devices').iterdir()),
                                  'mhi': sorted(p.name for p in Path('/sys/bus/mhi/devices').iterdir()) if Path('/sys/bus/mhi/devices').is_dir() else None,
                                  'net': sorted(p.name for p in Path('/sys/class/net').iterdir()),
                                  'ieee80211': sorted(p.name for p in Path('/sys/class/ieee80211').iterdir()) if Path('/sys/class/ieee80211').is_dir() else None,
                                  'log': [l for l in c.dmesg().splitlines() if any(k in l.lower() for k in ('cnss', 'mhi', 'pcie', 'wlan', 'qcacld', 'hdd'))][-200:]}
                print('WIFI', json.dumps({k: v for k, v in report['wifi'].items() if k != 'log'}), flush=True)
                need(all(bound.values()), 'PCIe/cnss not bound: %r' % bound)
                if m['stage'] == 'wifi-b':
                    # udev renames wlan0 (3a1d6dac: wlp1s0); accept any netdev whose device is the WLAN PCI function
                    wl = sorted(n.name for n in Path('/sys/class/net').iterdir() if (n/'wireless').exists() and (n/'device').exists()
                                and (n/'device').resolve().name == '0000:01:00.0')
                    report['wifi']['wlan_ifaces'] = wl; need(wl, 'no wireless netdev on 0000:01:00.0')
            if m['stage'] == 'audio-d1':
                import audiod1
                mon.phase = 'gpr round trip'; base = set(c.dmesg().splitlines())
                r = audiod1.run(report); mon.observe(5, quiet=True)
                report['gpr_log'] = [l for l in c.dmesg().splitlines() if l not in base][-40:]
                need(r.get('reply_opcode') == hex(audiod1.APM_CMD_RSP_GET_SPF_STATE), 'no APM_CMD_RSP_GET_SPF_STATE reply: %r' % r.get('seen'))
            if m['stage'] == 'adc':
                ad = m['adc']; vd = Path('/sys/bus/platform/devices')/ad['vadc']
                t_load = float(c.read('/proc/uptime').split()[0])
                print('Observing ADC probe / thermal zone registration for 60 s. Nothing is written.', flush=True); mon.observe(60, quiet=True)
                zones = {}
                for z in sorted(Path('/sys/class/thermal').glob('thermal_zone*')):
                    ty = c.read(z/'type').strip()
                    if ty in ad['zones']:
                        try: zones[ty] = int(c.read(z/'temp'))
                        except (OSError, ValueError) as e: zones[ty] = 'ERR %s' % e
                late = [l for l in c.dmesg().splitlines() if ad['charger_msg'] in l and (lambda u: u is not None and u > t_load + 15)(
                        float(l[1:l.index(']')]) if l.startswith('[') else None)]
                report['adc'] = {'driver': (vd/'driver').resolve().name if (vd/'driver').is_symlink() else None,
                                 'iio': sorted(x.name for x in Path('/sys/bus/iio/devices').iterdir()), 'zones': zones,
                                 'charger_msg_after_15s': len(late),
                                 'battery': {k: c.read('/sys/class/power_supply/battery/' + k).strip() for k in ('status', 'capacity', 'temp', 'current_now', 'health')},
                                 'log': [l for l in c.dmesg().splitlines() if any(k in l.lower() for k in ('adc', 'vadc', 'thermal', 'iio', 'spmi', 'therm'))
                                         and ad['charger_msg'] not in l][-80:]}
                print('ADC', json.dumps({k: v for k, v in report['adc'].items() if k != 'log'}), flush=True)
                need(report['adc']['driver'] and report['adc']['iio'], 'vadc not bound or no IIO device')
                missing = sorted(set(ad['zones']) - set(zones)); need(not missing, 'thermal zones not registered: %r' % missing)
                lo, hi = ad['temp_range_mC']
                bad = {k: v for k, v in zones.items() if not isinstance(v, int) or not lo <= v <= hi}; need(not bad, 'zone temperature outside %r: %r' % (ad['temp_range_mC'], bad))
                need(report['adc']['charger_msg_after_15s'] == 0, 'charger still cannot get usb1-conn-therm')
                print('ADC_ZONES_OK', flush=True)
            if m['stage'] == 'frpc':
                fr = m['frpc']; rp = Path('/sys/bus/rpmsg/devices')/fr['rpmsg']
                print('Observing FastRPC probe for 30 s. No device is opened.', flush=True); mon.observe(30, quiet=True)
                P = Path('/sys/bus/platform/devices')
                report['frpc'] = {'rpmsg_driver': (rp/'driver').resolve().name if (rp/'driver').is_symlink() else None,
                                  'misc': {x.name: c.read(x/'dev').strip() for x in Path('/sys/class/misc').glob('fastrpc*')},
                                  'cbs': {d.name: ((d/'driver').resolve().name if (d/'driver').is_symlink() else None) for d in P.iterdir() if 'compute-cb' in d.name},
                                  'log': [l for l in c.dmesg().splitlines() if 'fastrpc' in l.lower() or 'adsprpc' in l.lower()][-60:]}
                print('FRPC', json.dumps({k: v for k, v in report['frpc'].items() if k != 'log'}), flush=True)
                need(report['frpc']['rpmsg_driver'] and report['frpc']['misc'], 'fastrpc not bound or no device node')
            if m['stage'] == 'audio-d5':
                import audiod5
                mon.phase = 'channel check + wav'; base = set(c.dmesg().splitlines())
                print('Three clips follow, 6 s apart: L = ONE long beep (left channel), R = THREE short beeps (right channel), W = a melody (WAV).', flush=True)
                try: segs = audiod5.run_all(mon, report, B)
                finally:
                    report['play_log'] = [l for l in c.dmesg().splitlines() if l not in base and not any(k in l for k in ('bht', 'ggc_reg', 'BHT', 'bh201', 'nl_srv', 'BATTERY_CHG', 'wlan_hdd', 'reg_dump', 'Networ', 'wlan_l', 'mmc1'))][-200:]
                mon.observe(3, quiet=True)
                need(all(any('buffers_done' in x for x in sg['steps']) for sg in segs), 'a clip did not complete')
                report['amp_start'] = [l for l in report['play_log'] if 'start success' in l or 'start failed' in l]
                print('AMP_START', json.dumps([l.split('] ', 2)[-1][:80] for l in report['amp_start']][:12]), flush=True)
            if m['stage'] == 'audio-d4':
                import audiod4
                mon.phase = 'first sound'; base = set(c.dmesg().splitlines())
                print('Two segments follow, 6 s apart: A = ONE long beep, B = THREE short beeps. Tell which one you heard.', flush=True)
                try: segs = audiod4.run_all(mon, report); r = segs[-1]
                finally:
                    report['play_log'] = [l for l in c.dmesg().splitlines() if l not in base and not any(k in l for k in ('bht', 'ggc_reg', 'BHT', 'bh201', 'nl_srv', 'BATTERY_CHG', 'wlan_hdd', 'reg_dump', 'Networ', 'wlan_l', 'mmc1'))][-150:]
                mon.observe(3, quiet=True)
                need(all(any('buffers_done' in x for x in sg['steps']) for sg in segs), 'a segment did not complete')
                report['amp_start'] = [l for l in report['play_log'] if 'start success' in l or 'start failed' in l]
                print('AMP_START', json.dumps([l.split('] ', 2)[-1][:80] for l in report['amp_start']][:8]), flush=True)
            if m['stage'] == 'audio-d3':
                import audiod3
                mon.phase = 'speaker BE open'; base = set(c.dmesg().splitlines())
                r = audiod3.run(mon, report); mon.observe(5, quiet=True)
                report['be_log'] = [l for l in c.dmesg().splitlines() if l not in base and not any(k in l for k in ('bht', 'ggc_reg', 'BHT', 'bh201', 'nl_srv', 'BATTERY_CHG', 'wlan_hdd'))][-120:]
                need(any(x.get('prepared') for x in r['steps']) and any(x.get('closed') for x in r['steps']), 'BE open/prepare/close incomplete: %r' % r['steps'])
            if m['stage'] == 'audio-d2':
                import audiod2
                mon.phase = 'shared memory map'; base = set(c.dmesg().splitlines())
                r = audiod2.run(report); mon.observe(5, quiet=True)
                report['shm_log'] = [l for l in c.dmesg().splitlines() if l not in base][-40:]
                need(r.get('handle') is not None and r.get('unmap_ok'), 'shared memory map/unmap incomplete: %r' % r.get('steps'))
            if m['stage'] in ('audio-c2', 'audio-c3', 'audio-c4', 'audio-c'):
                au = m['audio']; P = Path('/sys/bus/platform/devices')
                print('Observing codec/card probe for 60 s. No mixer writes, no PCM open.', flush=True); mon.observe(60, quiet=True)
                devs = {d.name: ((d/'driver').resolve().name if (d/'driver').is_symlink() else None) for d in P.iterdir()
                        if any(k in d.name for k in ('spf_core_platform', 'lpass', 'lpi', 'stub', 'swr', 'macro', 'wcd', 'audio_hw', 'swr_mclk', 'core_clk', 'tx_clk', 'mclk'))}
                i2c = {d.name: ((d/'driver').resolve().name if (d/'driver').is_symlink() else None) for d in Path('/sys/bus/i2c/devices').iterdir() if d.name in au['amps'] + ['3-000e']}
                ls = lambda p: sorted(x.name for x in Path(p).iterdir()) if Path(p).is_dir() else None
                report['audio'] = {'cards': c.read('/proc/asound/cards') if Path('/proc/asound/cards').exists() else None,
                                   'pcm': c.read('/proc/asound/pcm') if Path('/proc/asound/pcm').exists() else None,
                                   'platform': devs, 'i2c': i2c, 'sound_class': ls('/sys/class/sound'), 'swr': ls('/sys/bus/swr/devices'),
                                   'log': [l for l in c.dmesg().splitlines() if any(k in l.lower() for k in ('snd', 'asoc', 'wcd', 'swr', 'lpass', 'cdc', 'aw882', 'awinic', 'msm_', 'machine', 'stub', 'lpi', 'mbhc', 'fsa'))][-200:]}
                print('AUDIO', json.dumps({k: v for k, v in report['audio'].items() if k not in ('log',)}), flush=True)
                report['card_registered'] = bool(report['audio']['cards'] and 'no soundcards' not in report['audio']['cards'])
                print('CARD_REGISTERED' if report['card_registered'] else 'NO_CARD (see log/devices)', flush=True)
                if m['stage'] == 'audio-c': need(report['card_registered'], 'sound card not registered')
            if m['stage'] == 'audio-c1':
                import qrtrns
                au = m['audio']; P = Path('/sys/bus/platform/devices')
                print('Observing GPR/SPF/PRM bring-up for 60 s. No sound card, no codecs.', flush=True); mon.observe(60, quiet=True)
                gp = Path('/sys/bus/rpmsg/devices')/au['gpr_rpmsg']
                audio_devs = {d.name: ((d/'driver').resolve().name if (d/'driver').is_symlink() else None) for d in P.iterdir()
                              if any(k in d.name for k in ('spf_core', 'lpass', 'lpi', 'audio', 'gpr', 'prm', 'pinctrl@', 'stub', 'swr', 'macro', 'wcd', 'sound', 'ion'))}
                report['audio'] = {'gpr_rpmsg_driver': (gp/'driver').resolve().name if (gp/'driver').is_symlink() else None,
                                   'gpr_devices': sorted(x.name for x in Path('/sys/bus').glob('gprbus/devices/*')) if Path('/sys/bus/gprbus').exists() else sorted(str(x) for x in Path('/sys/bus').iterdir() if 'gpr' in x.name),
                                   'platform': audio_devs, 'services': qrtrns.lookup(), 'misc': sorted(x.name for x in Path('/sys/class/misc').iterdir() if any(k in x.name for k in ('aud', 'gpr', 'spf', 'ion', 'adsp'))),
                                   'log': [l for l in c.dmesg().splitlines() if any(k in l.lower() for k in ('gpr', 'spf', 'prm', 'audio', 'lpi', 'q6', 'apm', 'snd', 'pdr', 'servreg', 'adsp'))][-150:]}
                print('AUDIO', json.dumps({k: v for k, v in report['audio'].items() if k not in ('log', 'services')}), flush=True)
                need(report['audio']['gpr_rpmsg_driver'] is not None, 'adsp_apps not bound (gpr)')
            if m['stage'] == 'qrtr-smd':
                import qrtrns
                q = m['qrtr']; bat = lambda: {k: c.read('/sys/class/power_supply/battery/' + k).strip() for k in ('capacity', 'status', 'temp')}
                print('Observing IPCRTR bind / SoCCP services for 30 s.', flush=True); mon.observe(30, quiet=True)
                rp = Path('/sys/bus/rpmsg/devices')/q['soccp_rpmsg']
                report['qrtr'] = {'rpmsg_driver': (rp/'driver').resolve().name if (rp/'driver').is_symlink() else None,
                                  'services': qrtrns.lookup(), 'soccp_state': c.read(Path('/sys/class/remoteproc')/q['soccp_rproc']/'state').strip(),
                                  'battery': bat(), 'log': [l for l in c.dmesg().splitlines() if any(k in l.lower() for k in ('qrtr', 'soccp', 'pdr', 'pmic_glink', 'servreg', 'qmi'))][-80:]}
                print('QRTR', json.dumps({k: v for k, v in report['qrtr'].items() if k != 'log'}), flush=True)
                need(report['qrtr']['rpmsg_driver'] == 'qcom_smd_qrtr', 'IPCRTR not bound to qrtr_smd')
                need(report['qrtr']['soccp_state'] == 'attached', 'SoCCP state changed')
            if m['stage'] == 'blescan':
                import blescan
                breg, _ = rg.load(c, m['boot_id'], 'bt'); prev = mon.extra
                wl = lambda: c.read('/sys/class/net/wlp1s0/operstate').strip() if Path('/sys/class/net/wlp1s0').exists() else None
                w0 = wl()
                def bt_extra():
                    prev(); rg.process_ok(c, breg['keeper']); need(wl() == w0, 'Wi-Fi interface state changed')
                mon.extra = bt_extra; mon.phase = 'ble scan'
                r = blescan.run(mon, report, int(m['bt']['hci'][3:]), m['bt']['secs'])
                need(r.get('enable_status') == 0 and r.get('disable_status') == 0, 'scan enable/disable not clean: %r' % {k: r.get(k) for k in ('mode', 'enable_status', 'disable_status', 'legacy_status')})
                need(r.get('after_down', {}).get('up') is False, 'hci not brought down')
                report['bt_log'] = [l for l in c.dmesg().splitlines() if 'Bluetooth' in l or 'hci0' in l][-40:]
            if m['stage'] == 'bt-b':
                print('Observing BT stack load for 20 s. No HCI device is created.', flush=True); mon.observe(20, quiet=True)
                report['bt'] = {'class_bluetooth': sorted(p.name for p in Path('/sys/class/bluetooth').iterdir()) if Path('/sys/class/bluetooth').is_dir() else None,
                                'ldiscs': c.read('/proc/tty/ldiscs'),
                                'log': [l for l in c.dmesg().splitlines() if 'Bluetooth' in l or 'hci' in l.lower()][-60:]}
                print('BT', json.dumps({k: v for k, v in report['bt'].items() if k != 'log'}), flush=True)
                need(report['bt']['class_bluetooth'] == [], 'unexpected /sys/class/bluetooth content: %r' % report['bt']['class_bluetooth'])
                need(any(l.split()[:2] == ['n_hci', '15'] for l in report['bt']['ldiscs'].splitlines()), 'N_HCI ldisc not registered')
            if m['stage'] == 'bt-probe':
                import btprobe
                wl = lambda: {n: c.read(Path('/sys/class/net')/n/'operstate').strip() for n in ('wlp1s0',) if (Path('/sys/class/net')/n).exists()}
                report['wlan_before'] = wl(); mon.phase = 'bt power-on probe'
                pr = btprobe.run(c, need, mon, report); report['wlan_after'] = wl()
                print('BT_PROBE', json.dumps({'steps': pr['steps'], 'wlan': [report['wlan_before'], report['wlan_after']]}), flush=True)
                need(any(s.get('power_off_ret') == 0 for s in pr['steps']), 'power off not confirmed')
                need(report['wlan_after'] == report['wlan_before'], 'Wi-Fi interface state changed')
            if m['stage'] == 'bt-a':
                b = m['bt']; P = Path('/sys/bus/platform/devices')
                print('Observing UART/btpower probe for 60 s. Nothing is opened; the chip is not powered on.', flush=True); mon.observe(60, quiet=True)
                bound = {d: ((P/d/'driver').resolve().name if (P/d/'driver').is_symlink() else None) for d in (b['uart'], b['power'])}
                ttys = sorted(p.name for p in Path('/sys/class/tty').glob('ttyHS*'))
                rdevs = {c.read(Path('/sys/class/tty')/t/'dev').strip() for t in ttys}
                holders = []
                for fd in Path('/proc').glob('[0-9]*/fd/*'):
                    try:
                        st = os.stat(fd)
                        if (st.st_mode & 0o170000) == 0o020000 and '%d:%d' % (os.major(st.st_rdev), os.minor(st.st_rdev)) in rdevs: holders.append(str(fd))
                    except OSError: pass
                report['bt'] = {'bound': bound, 'tty': {t: c.read(Path('/sys/class/tty')/t/'dev').strip() for t in ttys},
                                'chrdevs': [l for l in c.read('/proc/devices').splitlines() if any(k in l.lower() for k in ('bt', 'uwb', 'ttyhs', 'geni'))],
                                'classes': sorted(p.name for p in Path('/sys/class').iterdir() if any(k in p.name.lower() for k in ('bt', 'uwb'))),
                                'clk_virt_synced': c.read(P/b['clk_virt']/'state_synced').strip(), 'tty_holders': holders,
                                'log': [l for l in c.dmesg().splitlines() if any(k in l.lower() for k in ('bt', 'uwb', 'geni', 'uart', 'ttyhs', 'pdc', 'aop'))][-150:]}
                print('BT', json.dumps({k: v for k, v in report['bt'].items() if k != 'log'}), flush=True)
                need(all(bound.values()), 'UART/btpower not bound: %r' % bound)
                need(ttys, 'no ttyHS device'); need(not holders, 'someone opened ttyHS: %r' % holders)
            if drm_before is not None: need(drm_state() == drm_before, 'DRM state changed')
            report['status'] = 'STAGE_%s_LOADED' % m['stage'].upper().replace('-', '_'); print(report['status'], flush=True)
        except BaseException as exc:
            report['error'] = str(exc); report['phase'] = mon.phase; raise
        finally:
            report.update(loaded=mon.loaded, waits=mon.waits.bound, audit=mon.audit + mon.review.audit)
            for key, fn in (('dmesg_after', c.dmesg), ('tasks_after', c.tasks), ('battery_after', c.battery)):
                try: report[key] = fn()
                except Exception as exc: report[key + '_error'] = str(exc)
            save('result.json', report); print('Saved:', B/'result.json', flush=True)

if __name__ == '__main__':
    signal.signal(signal.SIGALRM, c.timeout)
    def interrupted(*_): raise RuntimeError('interrupted; loaded modules retained, inspect before continuing')
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, interrupted)
    try: main()
    except Exception as exc: sys.exit('STOP: ' + str(exc))
