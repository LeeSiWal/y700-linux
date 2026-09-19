#!/usr/bin/env python3
"""One boot's read-only pre-display evidence. No module load or clock writes."""
from pathlib import Path
import datetime, fcntl, hashlib, json, os, re, signal, stat, subprocess, sys, time

B = Path(__file__).resolve().parent
CLK = Path('/sys/kernel/debug/clk')
CLOCKS = ('disp_cc_mdss_mdp_clk_src', 'disp_cc_mdss_mdp_clk',
          'disp_cc_mdss_mdp1_clk', 'disp_cc_mdss_mdp_lut_clk',
          'disp_cc_mdss_mdp_lut1_clk', 'disp_cc_mdss_mdp_ss_ip_clk')
FIELDS = ('clk_prepare_count', 'clk_enable_count', 'clk_flags', 'clk_min_rate', 'clk_max_rate')

def need(ok, reason):
    if not ok: raise RuntimeError(reason)

def timeout(*_): raise TimeoutError('read exceeded 5 seconds')

def read(path, binary=False):
    signal.alarm(5)
    try: return Path(path).read_bytes() if binary else Path(path).read_text()
    finally: signal.alarm(0)

def optional(path):
    try: return {'text': read(path)}
    except FileNotFoundError: return {'unavailable': 'absent'}
    except PermissionError: return {'unavailable': 'permission denied'}

def validate(m, s):
    need(m['slot'] == s['slot'] == '_a', 'slot A required')
    for key in ('boot_id', 'kernel', 'root', 'slot'):
        need(s[key] == m[key], key + ' changed')
    need(s['root'] == s['sd_device'], 'root is not expected SD partition')
    need(s['root_fstype'] == 'ext4', 'unexpected root filesystem')
    need(not s['display_loaded'], 'display modules already loaded; baseline invalid')
    need(s['module_names'] == m['module_names'], 'module set changed')
    need(s['module_notes'] == m['module_notes'], 'loaded module identity changed')

def identity(m):
    roots = [line.split() for line in read('/proc/self/mountinfo').splitlines() if line.split()[4] == '/']
    need(len(roots) == 1, 'ambiguous root mount')
    bootconfig = read('/proc/bootconfig')
    slots = re.findall(r'^androidboot\.slot_suffix\s*=\s*"([^"]+)"\s*$', bootconfig, re.M)
    need(len(slots) == 1, 'ambiguous slot')
    modules = sorted(line.split()[0] for line in read('/proc/modules').splitlines())
    s = {'boot_id': read('/proc/sys/kernel/random/boot_id').strip(),
         'kernel': os.uname().release, 'root': roots[0][2],
         'root_fstype': roots[0][roots[0].index('-') + 1],
         'sd_device': read('/sys/class/block/mmcblk1p3/dev').strip(), 'slot': slots[0],
         'module_names': modules,
         'module_notes': {n: read(Path('/sys/module')/n/'notes/.note.gnu.build-id', True).hex() for n in m['module_notes']},
         'display_loaded': any(n in modules for n in ('msm_drm', 'msm_hfi_core', 'msm_hw_fence'))}
    validate(m, s)
    return s

def task_stat(text):
    end = text.rfind(')')
    values = text[end+2:].split()
    need(end > 0 and len(values) >= 20, 'invalid task stat')
    return {'comm': text[text.index('(')+1:end], 'state': values[0], 'starttime': values[19]}

def tasks():
    result = {}
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal(): continue
        try: threads = list((process/'task').iterdir())
        except (FileNotFoundError, ProcessLookupError): continue
        for p in threads:
            try:
                before = task_stat(read(p/'stat'))
                if before['state'] != 'D' and before['comm'] != 'adci_thread': continue
                record = dict(before, tgid=process.name, tid=p.name,
                              stack=optional(p/'stack'), wchan=optional(p/'wchan'))
                after = task_stat(read(p/'stat'))
                record['identity_stable'] = (before['comm'], before['starttime']) == (after['comm'], after['starttime'])
                record['state_after'] = after['state']
                result[p.name] = record
            except (FileNotFoundError, ProcessLookupError): continue
    return result

def clocks():
    result = {}
    for name in CLOCKS:
        p = CLK/name
        if not p.is_dir():
            result[name] = {'unavailable': 'no clock directory'}
            continue
        fields = list(FIELDS)
        # Only this source's OEM recalc function was verified to return a cached request.
        # Branch/PLL rate reads and full clk_summary/measurement muxes are excluded.
        if name == 'disp_cc_mdss_mdp_clk_src': fields.append('clk_rate')
        result[name] = {}
        for field in fields:
            f = p/field
            value = optional(f)
            if 'text' in value: value['mode'] = oct(stat.S_IMODE(f.stat().st_mode))
            result[name][field] = value
    return result

def battery():
    return {p.name: {f: optional(p/f) for f in ('type','status','capacity','temp','health','online','voltage_now','current_now')}
            for p in Path('/sys/class/power_supply').iterdir()}

def dmesg():
    r = subprocess.run(['dmesg'], text=True, capture_output=True, timeout=8)
    need(r.returncode == 0, 'dmesg unavailable: ' + r.stderr.strip())
    return r.stdout

def main():
    need(sys.argv[1:] in (['--check'], ['--collect']), 'use --check or --collect')
    m = json.loads(read(B/'manifest.json'))
    for name, digest in m['files'].items():
        need(Path(name).name == name and hashlib.sha256(read(B/name, True)).hexdigest() == digest, 'bundle changed: ' + name)
    initial = identity(m)
    need(not (B/'attempt.json').exists(), 'already attempted; inspect saved evidence')
    if sys.argv[1] == '--check':
        print('CHECK PASS: new boot, slot A, SD root,', len(m['module_notes']), 'module identities; display not loaded.')
        return
    need(os.geteuid() == 0, 'sudo required for task stacks and selected clock debugfs files')
    with (B/'collection.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        initial = identity(m)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        with (B/'attempt.json').open('x') as f: json.dump({'time':stamp, 'boot_id':m['boot_id']}, f)
        report = {'boot_id':m['boot_id'], 'identity_before':initial, 'samples':[],
                  'note':'Pre-display evidence only; existing waits/warnings are recorded, not approved. Source rate is a software request, not measured hardware frequency.'}
        try:
            report['dmesg_before'] = dmesg()
            report['battery_before'] = battery()
            report['pstore'] = {str(p): {x.name: optional(x) for x in p.iterdir() if x.is_file()}
                                for p in (Path('/sys/fs/pstore'), Path('/var/lib/systemd/pstore')) if p.is_dir()}
            for index in range(3):
                identity(m)
                sample = {'uptime':read('/proc/uptime').strip(), 'tasks':tasks(), 'clocks':clocks()}
                report['samples'].append(sample)
                print('SAMPLE', index+1, 'D tasks:', [v['comm'] for v in sample['tasks'].values() if v['state']=='D'], flush=True)
                print('MDP source software request:', sample['clocks']['disp_cc_mdss_mdp_clk_src'].get('clk_rate'), flush=True)
                if index != 2: time.sleep(2)
            report['battery_after'] = battery()
        except Exception as exc: report['error'] = str(exc)
        finally:
            signal.alarm(0)
            for key, fn in (('identity_after', lambda:identity(m)), ('dmesg_after', dmesg)):
                try: report[key] = fn()
                except Exception as exc: report[key+'_error'] = str(exc); report.setdefault('error', 'final verification failed')
            report['module_identities_unchanged'] = report.get('identity_after') == initial
            output = B/('reboot-baseline-'+stamp+'.json')
            with output.open('x') as f:
                json.dump(report, f, indent=2); f.flush(); os.fsync(f.fileno())
            os.chmod(output, 0o644)
            print('Saved:', output, flush=True)
        need('error' not in report, report.get('error', 'collection failed'))
        print('Read-only evidence collected. No modules, clocks, display or boot settings changed.')

if __name__ == '__main__':
    signal.signal(signal.SIGALRM, timeout)
    try: main()
    except Exception as exc: sys.exit('STOP: ' + str(exc))
