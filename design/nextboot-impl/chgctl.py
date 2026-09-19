#!/usr/bin/env python3
"""Userspace charge limiter for the Y700 on Ubuntu (Android's Lenovo charger service is absent, so the 70/80 thresholds in
sysfs are not enforced). Foreground only, until Ctrl-C or reboot: no service, no persistence.
The ONLY sysfs write is /sys/class/power_supply/battery/charging_enabled <- '0' | '1'.
  capacity >= stop                         -> disable charging
  capacity <= start                        -> enable charging
  temp >= TEMP_OFF or V > voltage_max+50mV -> disable (safety latch; released when temp <= TEMP_ON and V back in range)
  unreadable attribute                     -> disable
Observed 3a1d6dac: without a charger (usb online=0) writes are ignored (value stays 1); with a charger, 0 takes effect
after up to ~1 s (readback is asynchronous): status "Not charging", battery current 0, the system runs from USB.
So: writes happen only while usb/online=1, and each write is confirmed by polling the readback (SETTLE_S).
If a confirmed value is later flipped back while the charger stays online 3 times, the controller gives up (exit 3).
On exit charging_enabled=1 (the firmware default, charge freely) is written and confirmed; the outcome is printed.
Usage: sudo python3 -B chgctl.py [--dry-run] [--start 70] [--stop 80] [--log FILE]"""
import fcntl, json, os, signal, sys, time
from pathlib import Path

B = Path('/sys/class/power_supply/battery')
EN = B/'charging_enabled'
TEMP_OFF, TEMP_ON, OV_MARGIN_UV, INTERVAL, MAX_OVERRIDES, SETTLE_S = 450, 400, 50000, 15, 3, 5.0
USB_ONLINE = Path('/sys/class/power_supply/usb/online')

def read_state(root=B):
    s = {}
    for a in ('capacity', 'temp', 'voltage_now', 'voltage_max', 'charging_enabled', 'status', 'current_now'):
        try: v = (root/a).read_text().strip(); s[a] = v if a == 'status' else int(v)
        except (OSError, ValueError): s[a] = None
    try: s['usb_online'] = int((root.parent/'usb'/'online').read_text())
    except (OSError, ValueError): s['usb_online'] = None
    return s

def settle(want, read=lambda: read_state()['charging_enabled'], secs=SETTLE_S, step=0.25):
    """poll the asynchronous readback until it equals want; -> (value, seconds)"""
    t0 = time.monotonic()
    while True:
        v = read()
        if v == want or time.monotonic() - t0 >= secs: return v, round(time.monotonic() - t0, 2)
        time.sleep(step)

def decide(s, cfg, latched):
    """-> (want: 0|1|None, latched, reason). None = leave as is."""
    need = ('capacity', 'temp', 'voltage_now', 'voltage_max', 'charging_enabled')
    if any(s.get(k) is None for k in need): return 0, latched, 'unreadable %s' % [k for k in need if s.get(k) is None]
    hot = s['temp'] >= TEMP_OFF; ov = s['voltage_now'] > s['voltage_max'] + OV_MARGIN_UV
    if hot or ov: return 0, True, 'safety: %s' % ('temp %.1fC' % (s['temp'] / 10) if hot else 'voltage %d uV' % s['voltage_now'])
    if latched:
        if s['temp'] > TEMP_ON: return 0, True, 'safety latch held (temp %.1fC > %.1fC)' % (s['temp'] / 10, TEMP_ON / 10)
        latched = False
    if s['capacity'] >= cfg['stop']: return 0, latched, 'capacity %d%% >= %d%%' % (s['capacity'], cfg['stop'])
    if s['capacity'] <= cfg['start']: return 1, latched, 'capacity %d%% <= %d%%' % (s['capacity'], cfg['start'])
    return None, latched, 'hysteresis band'

def main(argv):
    dry = '--dry-run' in argv; opt = lambda k, d: argv[argv.index(k) + 1] if k in argv else d
    cfg = {'start': int(opt('--start', (B/'charge_control_start_threshold').read_text())),
           'stop': int(opt('--stop', (B/'charge_control_end_threshold').read_text()))}
    if not 20 <= cfg['start'] < cfg['stop'] <= 100: sys.exit('bad thresholds %r' % cfg)
    if not dry and os.geteuid() != 0: sys.exit('needs root (or use --dry-run)')
    lock = open('/run/chgctl.lock' if not dry else '/tmp/chgctl-dry.lock', 'w')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: sys.exit('another chgctl is running')
    logf = Path(opt('--log', '/home/siwal/y700-design/power-20260919/chgctl.jsonl'))
    def log(**k):
        k['t'] = round(time.time(), 1); line = json.dumps(k); print(line, flush=True)
        try:
            with logf.open('a') as f: f.write(line + '\n')
        except OSError: pass
    latched = False; confirmed = None; overrides = 0
    def write(v, why):
        nonlocal confirmed
        if dry: log(event='would_write', value=v, reason=why); confirmed = v; return
        EN.write_text(str(v)); got, secs = settle(v)
        confirmed = v if got == v else None
        log(event='write', value=v, reason=why, readback=got, settle_s=secs, confirmed=got == v)
    def stop(code, why):
        signal.signal(signal.SIGINT, signal.SIG_IGN); signal.signal(signal.SIGTERM, signal.SIG_IGN)   # a 2nd Ctrl-C must not cut the restore
        got = None
        if not dry:
            try: EN.write_text('1'); got, _ = settle(1)
            except OSError as e: got = 'ERR %s' % e
        log(event='exit', code=code, reason=why, wrote=None if dry else 1, now=got,
            note='dry run, nothing written' if dry else ('charging_enabled=1 (charge freely)' if got == 1 else 'NOT confirmed: check charging_enabled')); sys.exit(code)
    signal.signal(signal.SIGTERM, lambda *a: stop(0, 'SIGTERM'))
    log(event='start', dry_run=dry, cfg=cfg, en=read_state()['charging_enabled'], temp_off=TEMP_OFF, temp_on=TEMP_ON)
    try:
        while True:
            s = read_state(); want, latched, why = decide(s, cfg, latched); online = s['usb_online'] == 1
            if not online: confirmed = None   # no charger: writes are ignored, nothing to enforce
            elif confirmed is not None and not dry and s['charging_enabled'] != confirmed:
                overrides += 1; log(event='overridden_by_firmware', expected=confirmed, found=s['charging_enabled'], n=overrides); confirmed = None
                if overrides >= MAX_OVERRIDES: stop(3, 'firmware keeps overriding charging_enabled')
            cur = confirmed if dry and confirmed is not None else s['charging_enabled']
            if online and want is not None and want != cur: write(want, why)
            log(event='tick', cap=s['capacity'], temp=s['temp'], v=s['voltage_now'], i=s['current_now'], st=s['status'],
                usb=s['usb_online'], en=s['charging_enabled'], want=want, why=why)
            time.sleep(INTERVAL)
    except KeyboardInterrupt: stop(0, 'Ctrl-C')

if __name__ == '__main__': main(sys.argv[1:])
