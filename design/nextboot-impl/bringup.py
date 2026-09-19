#!/usr/bin/env python3
"""Y700 staged bring-up orchestrator: runs the reviewed one-shot stages of this boot in order, exactly like the manual
runbook (build bundle as the user -> --check -> --apply as root), and stops at the first stage that does not end in its
reviewed success status. It never retries an attempted bundle, never deletes guards, never loads modules itself.
- Resumable: a stage whose bundle of THIS boot already has a success result is skipped; an attempted bundle without
  success stops the run (a human must review it). An unattempted bundle of this boot is reused only if its --check passes.
- Kill switch: /home/siwal/y700-agent/bringup.disable (exists -> exit 0 without doing anything).
- Log: /home/siwal/y700-agent/bringup-<boot8>.log and status JSON bringup-<boot8>.json (one per boot, appended).
Usage: sudo python3 -B bringup.py [--upto STAGE] [--wifi] [--dry-run]   (stages up to btkeeper)"""
import json, os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = Path('/home/siwal/y700-agent')
USER = 'siwal'
DISABLE = AGENT/'bringup.disable'
GOV = ['/sys/devices/system/cpu/cpufreq/policy0/scaling_governor', '/sys/devices/system/cpu/cpufreq/policy6/scaling_governor']
# (stage, runner, success statuses); 'governor' / 'wifi-connect' are built-in steps, not bundles
STAGES = [('pstore', 'load-stage.py', {'STAGE_PSTORE_LOADED'}),
          ('display', 'load-stage.py', {'STAGE_DISPLAY_LOADED'}),
          ('owner', 'run-owner.py', {'DISPLAY_OWNER_READY_AWAITING_VISUAL_CONFIRMATION'}),
          ('governor', None, None),
          ('gpu', 'load-stage.py', {'STAGE_GPU_LOADED'}),
          ('keeper', 'run-keeper.py', {'GPU_KEEPER_READY'}),
          ('touch', 'load-stage.py', {'STAGE_TOUCH_LOADED'}),
          ('adc', 'load-stage.py', {'STAGE_ADC_LOADED'}),
          ('wifi-a', 'load-stage.py', {'STAGE_WIFI_A_LOADED'}),
          ('wifi-b', 'load-stage.py', {'STAGE_WIFI_B_LOADED'}),
          ('wifi-connect', None, None),
          # Bluetooth last (needs cnss2 from the Wi-Fi stages; a BT failure never blocks display/GPU/desktop/Wi-Fi):
          # same reviewed bundles as the manual runbook of boot 8e18ad1f (STAGE_BT_A_LOADED, STAGE_BT_B_LOADED, BT_KEEPER_READY)
          ('bt-a', 'load-stage.py', {'STAGE_BT_A_LOADED'}),
          ('bt-b', 'load-stage.py', {'STAGE_BT_B_LOADED'}),
          ('btkeeper', 'run-btkeeper.py', {'BT_KEEPER_READY'})]

def boot_id(): return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

class Run:
    def __init__(self, dry):
        self.dry = dry; self.boot = boot_id(); b8 = self.boot[:8]
        self.logp = AGENT/('bringup-%s.log' % b8); self.statp = AGENT/('bringup-%s.json' % b8)
        self.status = json.loads(self.statp.read_text()) if self.statp.exists() else {'boot_id': self.boot, 'runs': []}
        self.cur = {'start': time.time(), 'dry_run': dry, 'steps': []}; self.status['runs'].append(self.cur)
    def log(self, *a):
        line = '%s %s' % (time.strftime('%H:%M:%S'), ' '.join(str(x) for x in a)); print(line, flush=True)
        with self.logp.open('a') as f: f.write(line + '\n')
    def save(self):
        tmp = self.statp.with_suffix('.tmp'); tmp.write_text(json.dumps(self.status, indent=1)); os.replace(tmp, self.statp)
        os.chown(self.statp, *self.ids())
    def ids(self):
        import pwd; p = pwd.getpwnam(USER); return p.pw_uid, p.pw_gid
    def step(self, **k): self.cur['steps'].append(dict(k, t=time.time())); self.save()

def bundles(stage, boot):
    out = []
    for d in sorted(AGENT.glob('boot-%s-*' % stage), key=lambda p: p.stat().st_mtime):
        if d.name.startswith('boot-%s-' % stage) and len(d.name) == len('boot-%s-' % stage) + 16 and (d/'manifest.json').exists():
            try: m = json.loads((d/'manifest.json').read_text())
            except ValueError: continue
            if m.get('boot_id') == boot and m.get('stage') == stage: out.append(d)
    return out

def result(d):
    try: return json.loads((d/'result.json').read_text())
    except (OSError, ValueError): return None

def sh(cmd, user=False, log=None):
    if user: cmd = ['runuser', '-u', USER, '--'] + cmd
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()

def main(argv):
    dry = '--dry-run' in argv; upto = argv[argv.index('--upto') + 1] if '--upto' in argv else 'wifi-b'
    names = [s for s, _, _ in STAGES]
    if upto not in names: sys.exit('unknown stage ' + upto)
    if DISABLE.exists(): print('bringup disabled by', DISABLE); return 0
    if os.geteuid() != 0 and not dry: sys.exit('run as root (systemd service or sudo)')
    r = Run(dry); r.log('BRINGUP start boot=%s upto=%s dry=%s' % (r.boot[:8], upto, dry))
    todo = names[:names.index(upto) + 1]
    # wifi-connect runs only with --wifi (it now sits before the BT stages in STAGES)
    if 'wifi-connect' in todo and '--wifi' not in argv and upto != 'wifi-connect': todo.remove('wifi-connect')
    if '--wifi' in argv and names.index(upto) >= names.index('wifi-b') and 'wifi-connect' not in todo: todo.append('wifi-connect')
    for stage, runner, ok in STAGES:
        if stage not in todo: continue
        if stage == 'governor':
            cur = [Path(g).read_text().strip() for g in GOV]
            if cur != ['schedutil'] * 2 and not dry:
                for g in GOV: Path(g).write_text('schedutil')
            r.log('GOVERNOR', cur, '-> schedutil'); r.step(stage=stage, result='ok'); continue
        if stage == 'wifi-connect':
            rc, out = (0, 'dry') if dry else sh(['nmcli', 'con', 'up', 'y700-wifi'])
            r.log('WIFI_CONNECT rc=%d %s' % (rc, out.splitlines()[-1] if out else '')); r.step(stage=stage, rc=rc); continue
        have = bundles(stage, r.boot)
        done = [d for d in have if (result(d) or {}).get('status') in ok]
        if done: r.log('SKIP %s (done: %s)' % (stage, done[-1].name)); r.step(stage=stage, result='skip', bundle=done[-1].name); continue
        tried = [d for d in have if (d/'attempt.json').exists()]
        if tried:
            res = result(tried[-1]) or {}
            r.log('STOP %s: attempted bundle %s ended %r (review by hand, never retried)' % (stage, tried[-1].name, res.get('error') or res.get('status')))
            r.step(stage=stage, result='stop-attempted', bundle=tried[-1].name); return 3
        fresh = [d for d in have if not (d/'attempt.json').exists()]
        bundle = None
        for d in reversed(fresh):
            rc, out = sh(["python3", "-B", str(d/runner), "--check"], user=(os.geteuid() == 0))
            if rc == 0: bundle = d; break
        if bundle is None:
            if dry: r.log('DRY %s: would build + check + apply' % stage); r.step(stage=stage, result='dry'); continue
            rc, out = sh(['python3', '-B', str(HERE/'make-boot-bundle.py'), stage], user=(os.geteuid() == 0))
            if rc != 0: r.log('STOP %s: build failed: %s' % (stage, out[-300:])); r.step(stage=stage, result='build-failed', out=out[-500:]); return 4
            bundle = Path(out.split()[-2])
            rc, out = sh(['python3', '-B', str(bundle/runner), '--check'], user=(os.geteuid() == 0))
            if rc != 0: r.log('STOP %s: check failed: %s' % (stage, out[-300:])); r.step(stage=stage, result='check-failed', bundle=bundle.name, out=out[-500:]); return 5
        r.log('APPLY %s %s' % (stage, bundle.name))
        if dry: r.step(stage=stage, result='dry', bundle=bundle.name); continue
        with (bundle/'bringup-apply.log').open('w') as f:
            p = subprocess.run(['python3', '-B', str(bundle/runner), '--apply'], cwd=str(bundle), stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        os.chown(bundle/'bringup-apply.log', *r.ids())
        res = result(bundle) or {}
        if res.get('status') not in ok:
            r.log('STOP %s: rc=%d status=%r error=%r' % (stage, p.returncode, res.get('status'), res.get('error'))); r.step(stage=stage, result='failed', bundle=bundle.name, error=res.get('error')); return 6
        r.log('OK %s %s' % (stage, res.get('status'))); r.step(stage=stage, result='ok', bundle=bundle.name)
    r.log('BRINGUP done'); return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
