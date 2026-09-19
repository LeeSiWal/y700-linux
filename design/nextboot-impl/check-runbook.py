#!/usr/bin/env python3
"""Static check of the next-boot runbook (no device access): simulates the live module set from the first stage of boot
3a1d6dac (pstore manifest = modules present right after boot) through every stage in RUNBOOK and verifies
  - each stage's requires_live is satisfied by the boot set + earlier stages,
  - every module file's modinfo `depends` is live or loaded earlier in the same stage (order inside the stage),
  - every module file exists and matches its pinned SHA256 (MODS / EXTRA),
  - every code file of the stage exists.
Usage: python3 -B check-runbook.py"""
import hashlib, json, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNBOOK = [
    # (stage, runner, note)
    ('pstore', 'load-stage.py', 'ramoops backend'),
    ('display', 'load-stage.py', '15 display modules; ~5 min (HFI/fence waits + 130 s)'),
    ('owner', 'run-owner.py', 'DRM owner: small pattern -> native colour bars (visual check)'),
    ('gpu', 'load-stage.py', 'after: echo schedutil > policy0/6 scaling_governor (user sudo)'),
    ('keeper', 'run-keeper.py', 'first KGSL open, fd held'),
    ('touch', 'load-stage.py', 'spi-msm-geni -> NVT-ts'),
    ('adc', 'load-stage.py', 'PMIC ADC5 gen4 -> 15 NTC thermal zones incl. usb1/usb2-conn-therm (charger connector guard)'),
    ('wifi-a', 'load-stage.py', 'PCIe/MHI/cnss2'),
    ('wifi-b', 'load-stage.py', 'fs_ready=1 -> cold-boot calibration -> cld3 -> wlp1s0'),
    ('bt-a', 'load-stage.py', 'msm_geni_serial + btpower'),
    ('bt-b', 'load-stage.py', 'bluetooth/hci_uart stack'),
    ('btkeeper', 'run-btkeeper.py', 'vote UART clock, rampatch+NVM, H4 attach -> hci0'),
    ('qrtr-smd', 'load-stage.py', 'IPCRTR transport'),
    ('adsp', 'run-adsp.py', 'ADSP firmware (already installed) + pdmapper + remoteproc start'),
    ('audio-c1', 'load-stage.py', 'GPR/SPF/PRM/audio-pkt/ion/lpi'),
    ('audio-c', 'load-stage.py', 'codecs, amps, BT-audio codec, hdmi, machine -> card0'),
    ('audio-d5', 'load-stage.py', 'speaker check: L/R beeps + WAV (listen)'),
    ('frpc', 'load-stage.py', 'FastRPC (fastrpc-adsp-secure, fastrpc-lpass2000)'),
    ('sensors', 'run-sensors.py', 'hexrpcd on sensorspd with a WRITABLE copy of the persist registry from the first attach -> SSC'),
    ('susp-freezer', 'run-suspend.py', 'S-1 pm_test=freezer'),
    ('susp-devices', 'run-suspend-dev.py', 'S-2a pm_test=devices (LAN plugged); S-2b last, LAN unplugged, session over Wi-Fi'),
]

def tables():
    src = (HERE/'make-boot-bundle.py').read_text()
    head = src[:src.index("args = sys.argv[1:]")]
    ns = {'__file__': str(HERE/'make-boot-bundle.py')}; exec(compile(head, 'make-boot-bundle.py(head)', 'exec'), ns)
    return ns

def depends(path):
    out = subprocess.run(['modinfo', '-F', 'depends', str(path)], capture_output=True, text=True).stdout.strip()
    return {x.replace('-', '_') for x in out.split(',') if x}

def main():
    ns = tables(); ORDER, REQ, CODE, EXTRA, MODS = ns['ORDER'], ns['REQUIRES'], ns['CODE_BY_STAGE'], ns['EXTRA'], ns['MODS']
    base = json.loads(Path('/home/siwal/y700-agent/boot-pstore-b11e2aca2539c6b1/manifest.json').read_text())
    live = {n.replace('-', '_') for n in base['module_names']}; problems = []; rows = []
    for stage, runner, note in RUNBOOK:
        if stage not in ORDER: problems.append('%s: unknown stage' % stage); continue
        miss = [r for r in REQ[stage] if r.replace('-', '_') not in live]
        if miss: problems.append('%s: requires not live: %s' % (stage, miss))
        for code in CODE.get(stage, []) + [runner]:
            if not (HERE/code).exists(): problems.append('%s: code missing %s' % (stage, code))
        loaded = []
        for f in ORDER[stage]:
            if f in EXTRA: src, want = Path(EXTRA[f]['path']), EXTRA[f]['sha256']
            elif f in MODS: src, want = Path('/home/siwal/y700-agent')/MODS[f]['bundle']/f, MODS[f]['sha256']
            else: problems.append('%s: no source for %s' % (stage, f)); continue
            if not src.exists() or hashlib.sha256(src.read_bytes()).hexdigest() != want: problems.append('%s: %s missing/changed' % (stage, f)); continue
            d = depends(src) - live
            if d: problems.append('%s: %s depends on not-yet-loaded %s' % (stage, f, sorted(d)))
            m = f[:-3].replace('-', '_'); live.add(m); loaded.append(m)
        rows.append((stage, len(loaded), note))
    for stage, n, note in rows: print('%-10s %2d modules  %s' % (stage, n, note))
    print('final live modules: %d' % len(live))
    print('RUNBOOK', 'OK' if not problems else 'PROBLEMS'); [print('  -', p) for p in problems]
    return 1 if problems else 0

if __name__ == '__main__': sys.exit(main())
