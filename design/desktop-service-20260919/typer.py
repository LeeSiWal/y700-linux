#!/usr/bin/env python3
"""Type text into whatever window has keyboard focus, through a temporary uinput keyboard.
Used for UI-only inputs that cannot be scripted otherwise (e.g. Steam's console tab, which has no command line).
Needs write access to /dev/uinput. Usage: typer.py "text to type" [--enter] [--delay 0.02] | typer.py --test"""
import fcntl, os, stat, struct, sys, time
from pathlib import Path

EV_SYN, EV_KEY, SYN_REPORT = 0, 1, 0
EVENT = struct.Struct('<qqHHi')
def _ioc(d, t, nr, size): return (d << 30) | (size << 16) | (ord(t) << 8) | nr
UI_SET_EVBIT, UI_SET_KEYBIT = _ioc(1, 'U', 100, 4), _ioc(1, 'U', 101, 4)
UI_DEV_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = _ioc(1, 'U', 3, 92), _ioc(0, 'U', 1, 0), _ioc(0, 'U', 2, 0)
KEY_LEFTSHIFT, KEY_ENTER, KEY_SPACE, KEY_BACKSPACE = 42, 28, 57, 14

ROWS = [('1234567890-=', 2), ('qwertyuiop[]', 16), ("asdfghjkl;'", 30), ('zxcvbnm,./', 44)]
KEYMAP = {}
for chars, base in ROWS:
    for i, c in enumerate(chars): KEYMAP[c] = (base + i, False)
KEYMAP.update({' ': (KEY_SPACE, False), '\t': (15, False), '`': (41, False), '\\': (43, False)})
SHIFTED = {'!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8', '(': '9', ')': '0',
           '_': '-', '+': '=', '{': '[', '}': ']', ':': ';', '"': "'", '<': ',', '>': '.', '?': '/', '~': '`', '|': '\\'}
for up, low in SHIFTED.items(): KEYMAP[up] = (KEYMAP[low][0], True)
for c in 'abcdefghijklmnopqrstuvwxyz': KEYMAP[c.upper()] = (KEYMAP[c][0], True)

def encode(text):
    """text -> [(keycode, shift), ...]; raises ValueError on a character this map cannot type."""
    out = []
    for ch in text:
        if ch not in KEYMAP: raise ValueError('cannot type %r' % ch)
        out.append(KEYMAP[ch])
    return out

class Keyboard:
    def __init__(self, name='y700-typer'):
        ui = Path('/dev/uinput')
        if not ui.exists():
            ma, mi = map(int, Path('/sys/class/misc/uinput/dev').read_text().split(':'))
            os.mknod(ui, 0o600 | stat.S_IFCHR, os.makedev(ma, mi))
        self.fd = os.open(ui, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for code in sorted({c for c, _ in KEYMAP.values()} | {KEY_LEFTSHIFT, KEY_ENTER, KEY_BACKSPACE}):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack('<HHHH80sI', 0x06, 0x1d6b, 0x0701, 1, name.encode(), 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        time.sleep(1.0)                                   # let the compositor pick the device up before the first key
    def _ev(self, t, code, value): os.write(self.fd, EVENT.pack(0, 0, t, code, value))
    def tap(self, code, shift=False, delay=0.02):
        if shift: self._ev(EV_KEY, KEY_LEFTSHIFT, 1)
        self._ev(EV_KEY, code, 1); self._ev(EV_SYN, SYN_REPORT, 0); time.sleep(delay)
        self._ev(EV_KEY, code, 0)
        if shift: self._ev(EV_KEY, KEY_LEFTSHIFT, 0)
        self._ev(EV_SYN, SYN_REPORT, 0); time.sleep(delay)
    def type(self, text, delay=0.02):
        for code, shift in encode(text): self.tap(code, shift, delay)
    def close(self):
        try: fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError: pass
        os.close(self.fd)

def main(argv):
    if '--test' in argv:
        assert encode('abc') == [(30, False), (48, False), (46, False)]
        assert encode('A') == [(30, True)]
        assert encode('download_depot 4628740')[:3] == [(32, False), (24, False), (17, False)]
        assert encode('_')[0][1] is True and encode('-')[0][1] is False
        try: encode('한'); raise AssertionError('should refuse')
        except ValueError: pass
        print('typer selftest OK'); return 0
    text = argv[0] if argv else sys.exit('usage: typer.py "text" [--enter]')
    delay = float(argv[argv.index('--delay') + 1]) if '--delay' in argv else 0.02
    encode(text)                                          # fail before creating the device
    kb = Keyboard()
    try:
        kb.type(text, delay)
        if '--enter' in argv: kb.tap(KEY_ENTER, delay=delay)
    finally: kb.close()
    print('typed %d characters%s' % (len(text), ' + Enter' if '--enter' in argv else ''))
    return 0

if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
