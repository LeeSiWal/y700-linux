#!/usr/bin/env python3
"""Guarded one-shot reconstruction of previously reviewed display modules. No KMS."""
from pathlib import Path
import fcntl, hashlib, importlib.util, json, os, re, signal, subprocess, sys, time
from y700lib import new_kernel_faults

B = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('baseline', B/'runtime-readers.py')
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
need = c.need

def save(name, data):
    with (B/name).open('x') as f:
        json.dump(data, f, indent=2); f.flush(); os.fsync(f.fileno())

def frames(text):
    return [re.sub(r'^\[<[^>]+>\]\s*', '', x).strip() for x in text.splitlines() if x.strip()]

def payload(line):
    return re.sub(r'^\[\s*[0-9.]+\]\s+\[\s*T\d+\]\s?', '', line)

def recurring_adci_records(text, m):
    """Only a complete, previously reviewed hung-task report, including all frames."""
    lines = text.splitlines(); accepted = set(); tail = m['adci_log_tail']
    for i, line in enumerate(lines):
        if not re.fullmatch(r'INFO: task adci_thread:119 blocked for more than [0-9]+ seconds\.', payload(line)): continue
        block = lines[i+1:i+1+len(tail)]
        if [payload(x) for x in block] == tail:
            accepted.add(line); accepted.update(block)
    return accepted

def same_wait(t, expected):
    return (t['comm'] == expected['comm'] and t['starttime'] == expected['starttime']
            and t['identity_stable'] and frames(t.get('stack', {}).get('text','')) == expected['frames'])

class Monitor:
    def __init__(self, m):
        self.m=m; self.waits=dict(m['reviewed_waits']); self.loaded={}; self.unknown={}
        self.load_start={}; self.bound=set(); self.audit=[]; self.phase='preflight'
        self.accepted_panel=set(); self.insmod_pid=None; self.last_log=''

    def context(self):
        need(c.read('/proc/sys/kernel/random/boot_id').strip()==self.m['boot_id'], 'boot changed')
        need(os.uname().release==self.m['kernel'], 'kernel changed')
        need(re.search(r'^androidboot\.slot_suffix\s*=\s*"_a"\s*$',c.read('/proc/bootconfig'),re.M), 'slot A required')
        root=[x.split() for x in c.read('/proc/self/mountinfo').splitlines() if x.split()[4]=='/']
        need(len(root)==1 and root[0][2]==self.m['root']==c.read('/sys/class/block/mmcblk1p3/dev').strip(), 'SD root changed')
        for name, note in {**self.m['module_notes'], **self.loaded}.items():
            need(c.read(Path('/sys/module')/name/'notes/.note.gnu.build-id',True).hex()==note, 'module changed: '+name)
        names={x.split()[0] for x in c.read('/proc/modules').splitlines()}
        expected=set(self.m['module_names'])|set(self.loaded)
        if self.insmod_pid: expected.add(self.phase[:-3].replace('-','_'))
        need(names<=expected, 'unexpected module loaded')

    def check_tasks(self):
        tasks=c.tasks(); now=time.monotonic()
        # ADCI's actual identity is checked even if it temporarily leaves D state.
        need('119' in tasks and tasks['119']['starttime']==self.waits['119']['starttime'], 'ADCI identity changed')
        for pid,t in tasks.items():
            if t['state']!='D': continue
            if pid in self.waits:
                need(same_wait(t,self.waits[pid]), 'reviewed task stack/identity changed: '+pid)
                continue
            for module, template in self.m['listener_templates'].items():
                if module not in self.loaded or module in self.bound: continue
                if (t['comm']==template['comm'] and t['identity_stable']
                    and frames(t.get('stack',{}).get('text',''))==template['frames']
                    and int(t['starttime'])>=self.load_start[module]):
                    self.waits[pid]={'comm':t['comm'],'starttime':t['starttime'],'frames':template['frames']}
                    self.bound.add(module)
                    self.audit.append({'event':'reviewed_listener_bound','boot_id':self.m['boot_id'],'pid':pid,'task':t,'module':module})
                    print('REVIEWED_IDLE_WAIT',module,'pid='+pid,flush=True)
                    break
        unknown={(pid,t['starttime']):t for pid,t in tasks.items() if t['state']=='D' and pid not in self.waits}
        for key in list(self.unknown):
            if key not in unknown: del self.unknown[key]
        for key,t in unknown.items():
            self.unknown.setdefault(key,now)
            need(now-self.unknown[key]<10, 'new task blocked for 10 seconds: '+str(t))
        return tasks, unknown

    def check_log(self, text):
        accepted=set(self.m['reviewed_fault_records'])|recurring_adci_records(text,self.m)|self.accepted_panel
        unexpected=[]
        for line in new_kernel_faults('',text):
            if line in accepted: continue
            # Two exact, previously source-reviewed nonfatal parser messages; no general DRM exemption.
            body=payload(line)
            if (self.phase=='msm_drm.ko' and self.insmod_pid is not None
                and re.search(r'\[\s*T'+str(self.insmod_pid)+r'\]',line)
                and body in self.m['panel_messages']
                and not any(payload(x)==body for x in self.accepted_panel)):
                self.accepted_panel.add(line)
                self.audit.append({'event':'existing_panel_parse_error','record':line})
                continue
            unexpected.append(line)
        need(not unexpected, 'unreviewed kernel fault: '+str(unexpected[:3]))

    def tick(self):
        self.context()
        need(Path('/sys/bus/platform/drivers/ramoops/ramoops').is_symlink(), 'ramoops backend lost')
        battery=Path('/sys/class/power_supply/battery')
        need(c.read(battery/'health').strip()=='Good', 'battery health changed')
        need(int(c.read(battery/'capacity'))>=20 and int(c.read(battery/'temp'))<450, 'battery outside reviewed trial range')
        tasks,unknown=self.check_tasks()
        self.last_log=c.dmesg(); self.check_log(self.last_log)
        return tasks,unknown

    def observe(self, seconds, quiet=False):
        start=time.monotonic(); stable=None
        while True:
            tasks,unknown=self.tick(); now=time.monotonic()
            if unknown: stable=None
            elif stable is None: stable=now
            if now-start>=seconds and (not quiet or stable is not None and now-stable>=2): return
            need(not quiet or now-start<max(20,seconds+20),'no quiet interval')
            time.sleep(.5)
