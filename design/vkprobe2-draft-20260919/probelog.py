"""Strict parser for vkprobe.py output. Mesa/turnip log lines are allowed at info/warning level only."""
import json, re

MESA_LINE = re.compile(r'^(MESA|TU|turnip)\b.*?:\s*(info|warning|debug)\b', re.I)
ORDER = ['READY_FOR_PROBE', 'ICD_LOADED', 'NEGOTIATED', 'INSTANCE_CREATED', 'MESSENGER_CREATED', 'ENUMERATE_ENTER', 'PHYSICAL_DEVICES', 'PROPERTIES', 'HELD']

class ProbeLog:
    def __init__(self):
        self.stage = 0; self.go_sent = False; self.props = None; self.interface = None; self.mesa = []; self.errors = []; self.held = None
    def expect(self, tag):
        if ORDER[self.stage] != tag: raise RuntimeError('out of order: got %s expected %s' % (tag, ORDER[self.stage]))
        if tag != 'READY_FOR_PROBE' and not self.go_sent: raise RuntimeError('probe progressed without gate')
        self.stage += 1
    def feed(self, line):
        if self.held is not None: raise RuntimeError('output after HELD: ' + line)
        if line.startswith('STOP:'): raise RuntimeError('worker error: ' + line)
        if line == 'READY_FOR_PROBE': self.expect(line); return 'ready'
        if line == 'ICD_LOADED': self.expect(line); return 'loaded'
        m = re.fullmatch(r'NEGOTIATED interface=(\d+)', line)
        if m:
            self.expect('NEGOTIATED'); self.interface = int(m.group(1))
            if not 2 <= self.interface <= 7: raise RuntimeError('unexpected ICD interface version')
            return 'negotiated'
        if line == 'INSTANCE_CREATED': self.expect(line); return 'instance'
        if line == 'MESSENGER_CREATED': self.expect(line); return 'messenger'
        if line.startswith('VKMSG '):
            if not self.go_sent: raise RuntimeError('driver message before gate: ' + line)
            self.mesa.append(line)
            if re.match(r'VKMSG sev=0x1000\b', line): self.errors.append(line)
            return 'vkmsg'
        if line == 'ENUMERATE_ENTER': self.expect(line); return 'enumerate'
        m = re.fullmatch(r'PHYSICAL_DEVICES count=(\d+)', line)
        if m:
            self.expect('PHYSICAL_DEVICES')
            if m.group(1) != '1': raise RuntimeError('device count ' + m.group(1))
            return 'count'
        if line.startswith('PROPERTIES '):
            self.expect('PROPERTIES'); p = json.loads(line[11:])
            if p.get('vendorID') != 0x5143 or p.get('deviceName') != 'Adreno (TM) 840':
                raise RuntimeError('unexpected device: ' + line)
            self.props = p; return 'props'
        if line.startswith('HELD '):
            self.held = line
            if not (self.props is not None and line.endswith('reason=VK_INSTANCE_RETAINED')): raise RuntimeError('worker stopped: ' + line)
            if self.errors: raise RuntimeError('driver reported errors: ' + self.errors[0])
            self.expect('HELD'); return 'held'
        if MESA_LINE.search(line):
            if not self.go_sent: raise RuntimeError('driver output before gate: ' + line)
            self.mesa.append(line); return 'mesa'
        raise RuntimeError('unexpected worker line: ' + line)
