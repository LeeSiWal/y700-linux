#!/usr/bin/env python3
"""Read-only charger logger: every INTERVAL s appends one JSON line with the battery/usb/ucsi power_supply attributes that
matter for charge control. Never writes sysfs. Usage: python3 -B chgwatch.py OUT.jsonl [interval_s]"""
import json, sys, time
from pathlib import Path

PS = Path('/sys/class/power_supply')
BATT = ['status', 'health', 'capacity', 'voltage_now', 'voltage_ocv', 'voltage_max', 'current_now', 'temp', 'charge_counter',
        'chg_fv', 'cv', 'charging_enabled', 'battery_maintenance', 'protection_setting', 'recharge_setting',
        'charge_control_start_threshold', 'charge_control_end_threshold', 'charge_control_limit']
USB = ['online', 'voltage_now', 'current_now', 'input_current_limit', 'usb_type']

def rd(p):
    try: return p.read_text().strip()
    except OSError as e: return 'ERR:%s' % e.errno

def sample():
    s = {'t': round(time.time(), 1), 'mono': round(time.monotonic(), 1)}
    s['battery'] = {a: rd(PS/'battery'/a) for a in BATT}
    s['usb'] = {a: rd(PS/'usb'/a) for a in USB}
    s['ucsi'] = {n.name: rd(n/'online') for n in sorted(PS.glob('ucsi-*'))}
    return s

if __name__ == '__main__':
    out = Path(sys.argv[1]); iv = float(sys.argv[2]) if len(sys.argv) > 2 else 10
    while True:
        with out.open('a') as f: f.write(json.dumps(sample()) + '\n')
        time.sleep(iv)
