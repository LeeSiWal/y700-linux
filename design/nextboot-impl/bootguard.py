"""Boot-independent health guard for Y700 one-shot bundles.

Same semantics as the per-boot health_guard.Monitor used on boot b4e16b26, but reviewed faults and idle waits are
matched by *templates* instead of PIDs/timestamps of one boot:
  - SPMI boot warnings: the full 58-line block must equal the reviewed block (register/pstate/sp lines only
    shape-checked; CPU/PID/Comm/taint normalised), uptime <= 5 s, at most 3.
  - hung-task reports: only for PIDs bound to a reviewed wait template (comm + exact /proc stack), full report tail
    equal to the template, module build-ids equal to the live modules.
  - panel parse messages: exact text, emitted by the msm_drm insmod thread during that load, at most once each.
Everything else matching the fault pattern is unexpected. No name-wide whitelisting.
"""
import re, time
from pathlib import Path

FAULT = re.compile(r'WARNING:|BUG:|Oops:|Kernel panic|Call trace:|Runtime PM usage count underflow|Unbalanced IRQ|'
                   r'INFO: task .* blocked for more than|(?:SMMU|arm-smmu).*?[Ff]ault|synx:\s*warn:.*Subsystem restart|'
                   r'\[drm[^\n]*\*ERROR\*|\[msm-dsi-error\]')
PREFIX = re.compile(r'^\[\s*([0-9.]+)\]\s+\[\s*([TC])(\d+)\]\s?')
HUNG = re.compile(r'INFO: task (.{1,15}):(\d+) blocked for more than (\d+) seconds\.')
REG_LINE = re.compile(r'^(pstate: [0-9a-f]{8} \(.*\)|sp : [0-9a-f]{16}|(x\d+\s*: [0-9a-f]{16}\s*)+)$')
BUILDID = re.compile(r'\[(\w+) ([0-9a-f]{40})\]')

def payload(line):
    return PREFIX.sub('', line)

def uptime(line):
    m = PREFIX.match(line)
    return float(m.group(1)) if m else None

def thread(line):
    m = PREFIX.match(line)
    return int(m.group(3)) if m and m.group(2) == 'T' else None

# ---------------- SPMI boot warning blocks ----------------
def spmi_block_spans(lines):
    spans = []
    for i, l in enumerate(lines):
        if 'WARNING: CPU:' in l and 'spmi-pmic-arb.c:352 pmic_arb_wait_for_done' in l:
            j = i
            while j < len(lines) and j - i < 80 and '---[ end trace' not in lines[j]: j += 1
            spans.append((i, min(j, len(lines) - 1)))
    return spans

def spmi_blocks(text):
    lines = text.splitlines()
    return [[payload(x) for x in lines[a:b + 1]] for a, b in spmi_block_spans(lines)]

def normalize_block(block):
    out = []
    for x in block:
        x = re.sub(r'^WARNING: CPU: \d+ PID: \d+ at ', 'WARNING: CPU: # PID: # at ', x)
        x = re.sub(r'^CPU: \d+ UID: \d+ PID: \d+ Comm: \S+ Tainted: [A-Z ]+? +(\d+\.\d+\.\d+-)', r'CPU: # UID: # PID: # Comm: # Tainted: # \1', x)
        x = re.sub(r'^Tainted: \[.*\]$', 'Tainted: [#]', x)
        x = re.sub(r'---\[ end trace [0-9a-f]+ \]---', '---[ end trace # ]---', x)
        if REG_LINE.match(x.strip()): x = '{regs}'
        out.append(x)
    return out

# ---------------- dwc3_msm deferred-probe 'class hub' duplicate warnings ----------------
HUB_DUP = "sysfs: cannot create duplicate filename '/class/hub'"

def hub_dup_spans(lines):
    """(first, last) for each sysfs_warn_dup dump of the Lenovo dwc3_msm probe retry; ends at ret_from_fork (max 40 lines)."""
    spans = []
    for i, l in enumerate(lines):
        if payload(l) != HUB_DUP: continue
        j = i
        while j < len(lines) and j - i < 40 and not payload(lines[j]).strip().startswith('ret_from_fork'): j += 1
        spans.append((i, min(j, len(lines) - 1)))
    return spans

def normalize_hub_block(block):
    return [BUILDID.sub(lambda m: '[%s {buildid:%s}]' % (m.group(1), m.group(1)), x) for x in normalize_block(block)]

# ---------------- hung-task reports ----------------
def hung_spans(lines):
    """(header_idx, last_idx, comm15, pid) for each report; tail ends at the ret_from_fork frame (max 40 lines)."""
    spans = []
    for i, l in enumerate(lines):
        m = HUNG.search(payload(l))
        if not m: continue
        j = i + 1
        while j < len(lines) and j - i < 40 and not payload(lines[j]).strip().startswith('ret_from_fork'): j += 1
        spans.append((i, min(j, len(lines) - 1), m.group(1), int(m.group(2))))
    return spans

def hung_blocks(text, comm15):
    lines = text.splitlines()
    return [(a, pid, [payload(x) for x in lines[a + 1:b + 1]]) for a, b, c, pid in hung_spans(lines) if c == comm15]

def normalize_hung_tail(tail):
    out = []
    for x in tail:
        x = re.sub(r'pid:\d+\s+tgid:\d+\s+ppid:(\d+)\s+', r'pid:{pid} tgid:{pid} ppid:\1 ', x)
        x = BUILDID.sub(lambda m: '[%s {buildid:%s}]' % (m.group(1), m.group(1)), x)
        out.append(x)
    return out

def buildids_ok(tail, live):
    for x in tail:
        for mod, bid in BUILDID.findall(x):
            if live.get(mod) != bid: return False
    return True

# ---------------- review ----------------
class Review:
    """Stateful log review: bound wait PIDs, accepted panel lines and audit trail persist across ticks."""
    def __init__(self, templates, live_buildids):
        self.t = templates; self.live = live_buildids; self.bound = {}     # pid -> kind
        self.panel_ok = set(); self.audit = []
        self.exact_ok = set()   # exact dmesg lines reviewed after a recorded event (e.g. suspend test), never by pattern
        self.load_module = None; self.insmod_tid = None

    def unexpected(self, text):
        lines = text.splitlines(); ok = set()
        spmi = self.t['spmi']
        spans = spmi_block_spans(lines); shift = len(spmi['blocks']) - len(spans)
        for n, (a, b) in enumerate(spans):
            block = normalize_block([payload(x) for x in lines[a:b + 1]])
            up = uptime(lines[a])
            # approved rotation extension: when earlier blocks were dropped by the ring buffer, the survivors are the LAST ones
            cands = [spmi['blocks'][n]] if n < len(spmi['blocks']) else []
            if shift > 0 and n + shift < len(spmi['blocks']): cands.append(spmi['blocks'][n + shift])
            if n < spmi['max_count'] and up is not None and up <= spmi['max_uptime_s'] and block in cands:
                ok.update(range(a, b + 1))
        ok.update(self.rotated_template_head(lines))
        for a, b, comm15, pid in hung_spans(lines):
            kind = self.bound.get(pid)
            tmpl = self.t['waits'].get(kind) if kind else None
            tail = [payload(x) for x in lines[a + 1:b + 1]]
            if tmpl and comm15 == tmpl['comm15'] and normalize_hung_tail(tail) == tmpl['hung_tail'] and buildids_ok(tail, self.live):
                ok.update(range(a, b + 1))
        ok.update(self.rotated_head(lines))
        hub = self.t.get('dwc3_hub')
        if hub:
            for n, (a, b) in enumerate(hub_dup_spans(lines)):
                blk = [payload(x) for x in lines[a:b + 1]]; up = uptime(lines[a])
                if (n < hub['max_count'] and up is not None and up <= hub['max_uptime_s'] and len({thread(x) for x in lines[a:b + 1]}) == 1
                        and normalize_hub_block(blk) == hub['block'] and buildids_ok(blk, self.live)):
                    ok.update(range(a, b + 1))
        ok.update(i for i, l in enumerate(lines) if l in self.exact_ok)
        for i, l in enumerate(lines):
            if '[msm-dsi-error]' not in l: continue
            if l in self.panel_ok: ok.add(i); continue
            body = payload(l)
            if (self.load_module == 'msm_drm' and self.insmod_tid is not None and thread(l) == self.insmod_tid
                    and body in self.t['panel_messages'] and not any(payload(x) == body for x in self.panel_ok)):
                self.panel_ok.add(l); self.audit.append({'event': 'reviewed_panel_message', 'line': l}); ok.add(i)
        return [l for i, l in enumerate(lines) if FAULT.search(l) and i not in ok]

    def rotated_template_head(self, lines):
        """Approved extension of the rotated-head rule to the SPMI and dwc3_hub boot templates: a leading fragment at buffer
        index 0 (header dropped by the ring buffer), one reporting thread, within the template's uptime limit, whose
        normalized lines equal the exact SUFFIX of a template block (longest match); dwc3 fragments also need live build-ids."""
        if not lines or thread(lines[0]) is None: return set()
        tid = thread(lines[0]); n = 0
        while n < len(lines) and n < 80 and thread(lines[n]) == tid: n += 1
        frag = [payload(x) for x in lines[:n]]; up = uptime(lines[0])
        cands = [('spmi', b, self.t['spmi']['max_uptime_s'], normalize_block) for b in self.t['spmi']['blocks']]
        if self.t.get('dwc3_hub'): cands.append(('dwc3_hub', self.t['dwc3_hub']['block'], self.t['dwc3_hub']['max_uptime_s'], normalize_hub_block))
        best = None
        for kind, blk, max_up, norm in cands:
            if up is None or up > max_up: continue
            for L in range(min(len(blk) - 1, n), 0, -1):      # len(blk) itself is a complete block, handled above
                if norm(frag[:L]) == blk[-L:] and (kind != 'dwc3_hub' or buildids_ok(frag[:L], self.live)):
                    if not best or L > best[1]: best = (kind, L)
                    break
        if not best: return set()
        ev = {'event': 'rotated_template_head', 'kind': best[0], 'lines': best[1], 'first': lines[0]}
        if ev not in self.audit: self.audit.append(ev)
        return set(range(best[1]))

    def rotated_head(self, lines):
        """The ring buffer can drop the header of a report that was complete (and reviewed) earlier. Accept only a leading
        fragment (index 0, one reporting thread, no header, ending at ret_from_fork within 40 lines) that equals the
        *suffix* of a bound wait's reviewed tail; a surviving 'task:... pid:N' line must name a PID bound to that kind."""
        if not lines or HUNG.search(payload(lines[0])) or thread(lines[0]) is None: return set()
        tid = thread(lines[0]); end = None
        for j in range(min(40, len(lines))):
            if thread(lines[j]) != tid or HUNG.search(payload(lines[j])): return set()
            if payload(lines[j]).strip().startswith('ret_from_fork'): end = j; break
        if end is None: return set()
        seg = [payload(x) for x in lines[:end + 1]]; norm = normalize_hung_tail(seg)
        pids = [int(m.group(1)) for m in (re.search(r'^task:.{1,15}\s+state:D .*?pid:(\d+)\s', x) for x in seg) if m]
        for kind in sorted(set(self.bound.values())):
            tail = self.t['waits'][kind]['hung_tail']
            if len(norm) <= len(tail) and tail[-len(norm):] == norm and buildids_ok(seg, self.live) \
                    and all(self.bound.get(p) == kind for p in pids):
                self.audit.append({'event': 'rotated_head_fragment', 'kind': kind, 'lines': len(seg), 'first': lines[0]})
                return set(range(end + 1))
        return set()

# ---------------- task / wait binding ----------------
def frames(stack_text):
    return [re.sub(r'^\[<[^>]+>\]\s*', '', x).strip() for x in stack_text.splitlines() if x.strip()]

class Waits:
    """Reviewed idle waits bound by template (comm + exact stack), never by a PID from another boot."""
    def __init__(self, templates):
        self.t = templates['waits']; self.bound = {}   # pid(str) -> {'kind','starttime'}
        self.unknown = {}

    def try_bind(self, tasks, kinds, not_before=0):
        bound = []
        for kind in kinds:
            if any(v['kind'] == kind for v in self.bound.values()): continue
            tm = self.t[kind]
            cands = [(pid, t) for pid, t in tasks.items() if t['state'] == 'D' and t['comm'] == tm['comm'] and t['identity_stable']
                     and frames(t.get('stack', {}).get('text', '')) == tm['frames'] and int(t['starttime']) >= not_before]
            if len(cands) == 1:
                pid, t = cands[0]; self.bound[pid] = {'kind': kind, 'starttime': t['starttime']}; bound.append((kind, pid))
        return bound

    def check(self, tasks, now=None):
        """Raise on a changed bound wait; return unknown D tasks (caller enforces the 10 s rule)."""
        now = time.monotonic() if now is None else now
        for pid, b in self.bound.items():
            t = tasks.get(pid)
            if b['kind'] == 'adci':
                if not t or t['starttime'] != b['starttime']: raise RuntimeError('ADCI identity changed')
            if t and t['state'] == 'D':
                if not (t['starttime'] == b['starttime'] and t['identity_stable'] and frames(t.get('stack', {}).get('text', '')) == self.t[b['kind']]['frames']):
                    raise RuntimeError('reviewed wait stack/identity changed: %s %s' % (pid, b['kind']))
        unknown = {(pid, t['starttime']): t for pid, t in tasks.items() if t['state'] == 'D' and pid not in self.bound}
        for k in list(self.unknown):
            if k not in unknown: del self.unknown[k]
        for k, t in unknown.items():
            self.unknown.setdefault(k, now)
            if now - self.unknown[k] >= 10: raise RuntimeError('new task blocked for 10 seconds: ' + str(t))
        return unknown

    def pid_kinds(self):
        return {int(p): b['kind'] for p, b in self.bound.items()}
