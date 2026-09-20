#!/usr/bin/env python3
"""Check what the boot automation actually achieved on the current boot (read-only).

Run it after a reboot: it reports the stages bringup.py reached, the state of the four units, and whether the pieces
they are supposed to produce are really there (display owner, GPU keeper, touch, Wi-Fi, audio, desktop, presenter).
Exit code 0 when everything expected is present, 1 otherwise, so it can be used as a gate."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

AGENT = Path('/home/siwal/y700-agent')
STATE = Path('/run/y700-desktop-state')
UNITS = ('y700-bringup', 'y700-desktop', 'y700-speakerd', 'y700-thermald')
WANT_STAGES = ('pstore', 'display', 'owner', 'governor', 'gpu', 'keeper', 'touch', 'adc',
               'wifi-a', 'wifi-b', 'bt-a', 'bt-b', 'btkeeper', 'qrtr-smd', 'adsp', 'audio-c1', 'audio-c')
ok_all = True


def say(good, label, detail=''):
    """good True/False, or None for a check this run cannot make (counts as neither pass nor fail)"""
    global ok_all
    if good is False:
        ok_all = False
    print('%-4s %-26s %s' % ({True: 'OK', False: 'FAIL', None: 'SKIP'}[good], label, detail))


def sh(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 'error: %r' % (e,)


boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()[:8]
up = time.time() - float(Path('/proc/uptime').read_text().split()[0])
print('boot %s, booted %s, checked %s\n' % (boot, time.strftime('%H:%M:%S', time.localtime(up)), time.strftime('%H:%M:%S')))

# 1. units
for u in UNITS:
    active = sh('systemctl', 'is-active', u)
    say(active in ('active', 'activating'), u, '%s (enabled=%s, since %s)'
        % (active, sh('systemctl', 'is-enabled', u), sh('systemctl', 'show', '-p', 'ActiveEnterTimestamp', '--value', u)[:30]))

# 2. bring-up stages of this boot
doc = None
p = AGENT / ('bringup-%s.json' % boot)
try:
    doc = json.loads(p.read_text())
except (OSError, ValueError) as e:
    say(False, 'bringup json', repr(e))
if doc:
    res = {}
    for r in doc.get('runs', []):
        for s in r.get('steps', []):
            if s.get('result') and s['result'] != 'skip':
                res[s['stage']] = s['result']
    missing = [s for s in WANT_STAGES if res.get(s) != 'ok']
    say(not missing, 'bring-up stages', 'all ok' if not missing else 'not ok: %s' % ', '.join('%s=%s' % (m, res.get(m)) for m in missing))

# 3. what the stages should have produced
say(Path('/dev/kgsl-3d0').exists(), '/dev/kgsl-3d0')
say(os.access('/dev/dma_heap/system', os.R_OK | os.W_OK), '/dev/dma_heap/system', 'readable+writable by this user')
say(any('y700-rotated-touch' in l for l in Path('/proc/bus/input/devices').read_text().splitlines()), 'touch proxy device')
ip = sh('sh', '-c', "ip -br addr show | awk '$2==\"UP\" && $1!=\"lo\" {print $1\" \"$3}'")
say(bool(ip), 'network up', ip.replace('\n', ' | '))
say('hci0' in sh('sh', '-c', 'ls /sys/class/bluetooth 2>/dev/null'), 'bluetooth hci0')
cards = sh('sh', '-c', 'cat /proc/asound/card*/id 2>/dev/null')
say('canoeqrd' in cards.replace('-', ''), 'audio card', cards.replace(chr(10), ' '))
say(Path('/run/y700-desktop/y700-speaker.fifo').exists(), 'speaker fifo', 'speakerd is listening')

# 4. desktop presenter
try:
    st = json.loads((STATE / 'status.json').read_text())
    age = time.time() - st.get('t', 0)
    say(age < 180, 'presenter status', 'fps %s, zerocopy %s, copy_ms %s, underruns %s, %.0f s old'
        % (st.get('fps'), st.get('zerocopy'), st.get('copy_ms_avg'), st.get('underruns_total'), age))
except PermissionError:
    say(None if os.geteuid() else False, 'presenter status', 'root-only file: run this script with sudo to see it')
except (OSError, ValueError) as e:
    say(False, 'presenter status', repr(e))
say(Path('/run/y700-desktop/wayland-0').exists(), 'wayland socket')
say(bool(sh('pgrep', '-x', 'phoc')), 'compositor (phoc)')
say(bool(sh('pgrep', '-f', 'xguard.py')), 'x input guard')

print('\n%s' % ('ALL EXPECTED PIECES PRESENT' if ok_all else 'SOMETHING IS MISSING - see FAIL lines above'))
sys.exit(0 if ok_all else 1)
