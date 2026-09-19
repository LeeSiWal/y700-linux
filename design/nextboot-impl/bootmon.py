"""Monitor for boot-independent bundles: boot identity + module identity + battery/ramoops + template-bound waits + template
log review. Mirrors health_guard.Monitor (b4e16b26) without PIDs or timestamps from another boot."""
import importlib.util, json, os, re, time
from pathlib import Path
import bootguard as bg

B = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('readers', B/'runtime-readers.py')
c = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(c)
need = c.need

def live_buildid(mod):
    return c.read(Path('/sys/module')/mod/'notes/.note.gnu.build-id', True).hex()[-40:]

def live_modules():
    return {x.split()[0] for x in c.read('/proc/modules').splitlines()}

class BootMonitor:
    def __init__(self, m, templates):
        self.m = m; self.t = templates
        self.waits = bg.Waits(templates)
        self.review = bg.Review(templates, {})
        self.review.panel_ok = set(m.get('reviewed_lines', []))   # exact lines accepted by this boot's display stage
        self.review.exact_ok = set(m.get('reviewed_extra_lines', []))   # exact lines from reviewed rebase records
        self.loaded = {}; self.phase = 'preflight'; self.insmod_mod = None; self.pending = {}   # kind -> not_before (ticks)
        self.extra = None   # optional per-tick check (e.g. display registry) installed by a stage
        self.audit = []; self.last_log = ''; self.ramoops_required = m.get('ramoops_required', True)
        self.refresh_buildids()

    def refresh_buildids(self):
        mods = {'qcom_scm', 'si_core_module', 'msm_hfi_core', 'msm_hw_fence', 'spmi_pmic_arb', 'dwc3_msm'}
        self.review.live = {x: live_buildid(x) for x in mods if (Path('/sys/module')/x).is_dir()}

    def context(self):
        need(c.read('/proc/sys/kernel/random/boot_id').strip() == self.m['boot_id'], 'boot changed')
        need(os.uname().release == self.m['kernel'], 'kernel changed')
        need(re.search(r'^androidboot\.slot_suffix\s*=\s*"_a"\s*$', c.read('/proc/bootconfig'), re.M), 'slot A required')
        root = [x.split() for x in c.read('/proc/self/mountinfo').splitlines() if x.split()[4] == '/']
        need(len(root) == 1 and root[0][2] == self.m['root'] == c.read('/sys/class/block/mmcblk1p3/dev').strip(), 'SD root changed')
        for name, bid in {**self.m['module_notes'], **self.loaded}.items():
            need(live_buildid(name) == bid, 'module changed: ' + name)
        names = live_modules(); expected = set(self.m['module_names']) | set(self.loaded)
        if self.insmod_mod: expected.add(self.insmod_mod)
        need(names <= expected, 'unexpected module loaded: ' + str(sorted(names - expected)))

    def bind(self, kinds, not_before=0):
        for kind, pid in self.waits.try_bind(c.tasks(), kinds, not_before):
            self.audit.append({'event': 'wait_bound', 'kind': kind, 'pid': pid, 'time': time.time()})
            print('REVIEWED_IDLE_WAIT', kind, 'pid=' + pid, flush=True)
        self.review.bound = self.waits.pid_kinds()

    def tick(self):
        self.context()
        if self.ramoops_required: need(Path('/sys/bus/platform/drivers/ramoops/ramoops').is_symlink(), 'ramoops backend lost')
        bat = Path('/sys/class/power_supply/battery')
        need(c.read(bat/'health').strip() == 'Good', 'battery health changed')
        need(int(c.read(bat/'capacity')) >= 20 and int(c.read(bat/'temp')) < 450, 'battery outside reviewed range')
        tasks = c.tasks()
        for kind, nb in list(self.pending.items()):
            for k, pid in self.waits.try_bind(tasks, [kind], nb):
                self.audit.append({'event': 'wait_bound', 'kind': k, 'pid': pid, 'time': time.time()})
                print('REVIEWED_IDLE_WAIT', k, 'pid=' + pid, flush=True); del self.pending[kind]
        self.review.bound = self.waits.pid_kinds()
        unknown = self.waits.check(tasks)
        self.last_log = c.dmesg(); bad = self.review.unexpected(self.last_log)
        need(not bad, 'unreviewed kernel fault: ' + str(bad[:3]))
        if self.extra: self.extra()
        return tasks, unknown

    def observe(self, seconds, quiet=False):
        start = time.monotonic(); stable = None
        while True:
            _, unknown = self.tick(); now = time.monotonic()
            if unknown: stable = None
            elif stable is None: stable = now
            if now - start >= seconds and (not quiet or (stable is not None and now - stable >= 2)): return
            need(not quiet or now - start < max(20, seconds + 20), 'no quiet interval')
            time.sleep(.5)
