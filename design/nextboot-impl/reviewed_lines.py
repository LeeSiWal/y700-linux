"""Collect log lines that an earlier stage of THIS boot reviewed and accepted (exact text), for later bundles' manifests."""
import json
from pathlib import Path

def collect(agent, boot_id):
    lines = []
    for d in sorted(Path(agent).glob('boot-display-*')):
        try:
            m = json.loads((d/'manifest.json').read_text()); r = json.loads((d/'result.json').read_text())
        except Exception:
            continue
        if m.get('boot_id') != boot_id or r.get('status') != 'STAGE_DISPLAY_LOADED': continue
        lines += [a['line'] for a in r.get('audit', []) if a.get('event') == 'reviewed_panel_message']
    return lines

def collect_extra(agent, boot_id):
    """Exact lines accepted by reviewed rebase records of this boot (registry-rebase-<boot8>-*.json)."""
    lines = []
    for p in sorted(Path(agent).glob('registry-rebase-%s-*.json' % boot_id[:8])):
        r = json.loads(p.read_text())
        if r.get('boot_id') == boot_id: lines += r.get('reviewed_extra_lines', [])
    return lines
