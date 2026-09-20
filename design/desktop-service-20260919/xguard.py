"""X input guard for the phosh session.

phoc never restacks Xwayland windows: the X stack keeps creation order, and it ignores XRaiseWindow/XLowerWindow
(ConfigureRequest) and zwlr_foreign_toplevel activate from other clients (measured 2026-09-21). X routes pointer and
touch input by ITS stacking, so the most recently created window of a multi-window X app keeps every click even when
phosh is showing a different one of its windows - the Steam client's Friends/Settings windows swallowed all touch
while the store window was on screen, and closing the extra window was the only thing that restored input.

This guard watches _NET_ACTIVE_WINDOW (phoc sets it to the window phosh shows) against the topmost mapped toplevel.
While they disagree it first asks for a raise (harmless, normally ignored), and after `grace` seconds it closes the
window on top with WM_DELETE_WINDOW - a polite close, the same as tapping its X button; the app keeps running and can
open the window again. Only windows of the configured WM_CLASSes (default: steam) are ever closed.

desktop.json: {"x_guard": "close" | "warn" | "off", "x_guard_classes": ["steam"], "x_guard_grace": 2.0}
"""
import ctypes as C
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOGF = sys.stderr

x11 = C.CDLL('libX11.so.6')
x11.XOpenDisplay.restype = C.c_void_p
x11.XDefaultRootWindow.restype = C.c_ulong
x11.XInternAtom.restype = C.c_ulong
x11.XFetchName.argtypes = [C.c_void_p, C.c_ulong, C.POINTER(C.c_char_p)]

IsViewable = 2


class Attrs(C.Structure):
    _fields_ = [('x', C.c_int), ('y', C.c_int), ('width', C.c_int), ('height', C.c_int), ('border_width', C.c_int),
                ('depth', C.c_int), ('visual', C.c_void_p), ('root', C.c_ulong), ('class_', C.c_int),
                ('bit_gravity', C.c_int), ('win_gravity', C.c_int), ('backing_store', C.c_int),
                ('backing_planes', C.c_ulong), ('backing_pixel', C.c_ulong), ('save_under', C.c_int),
                ('colormap', C.c_ulong), ('map_installed', C.c_int), ('map_state', C.c_int),
                ('all_event_masks', C.c_long), ('your_event_mask', C.c_long), ('do_not_propagate_mask', C.c_long),
                ('override_redirect', C.c_int), ('screen', C.c_void_p)]


class ClassHint(C.Structure):
    _fields_ = [('res_name', C.c_char_p), ('res_class', C.c_char_p)]


class ClientMessage(C.Structure):
    _fields_ = [('type', C.c_int), ('serial', C.c_ulong), ('send_event', C.c_int), ('display', C.c_void_p),
                ('window', C.c_ulong), ('message_type', C.c_ulong), ('format', C.c_int), ('l', C.c_long * 5)]


def log(*a):
    print('%s XGUARD' % time.strftime('%Y-%m-%d %H:%M:%S'), *a, file=LOGF, flush=True)


def conf():
    """re-read desktop.json every tick: the settings app rewrites it live"""
    try:
        with open(os.path.join(HERE, 'desktop.json')) as f:
            c = json.load(f)
    except (OSError, ValueError):
        c = {}
    return (str(c.get('x_guard', 'close')),
            [s.lower() for s in c.get('x_guard_classes', ['steam'])],
            float(c.get('x_guard_grace', 2.0)))


def decide(active, tops, waited, grace):
    """active: the window phosh shows (0 when none). tops: mapped, non-override toplevels, bottom -> TOP.
    Returns (action, window): 'raise' while inside the grace period, then 'close' for the window on top."""
    if not active or active not in tops or len(tops) < 2:
        return None, 0
    if tops[-1] == active:
        return None, 0
    return ('close' if waited >= grace else 'raise'), tops[-1]


class Display:
    def __init__(self, name=b':0'):
        self.d = x11.XOpenDisplay(name)
        if not self.d:
            raise OSError('cannot open X display %s' % name.decode())
        self.root = x11.XDefaultRootWindow(C.c_void_p(self.d))
        self.a_active = x11.XInternAtom(C.c_void_p(self.d), b'_NET_ACTIVE_WINDOW', 0)
        self.a_protocols = x11.XInternAtom(C.c_void_p(self.d), b'WM_PROTOCOLS', 0)
        self.a_delete = x11.XInternAtom(C.c_void_p(self.d), b'WM_DELETE_WINDOW', 0)

    def active_window(self):
        at = C.c_ulong(); fmt = C.c_int(); n = C.c_ulong(); rem = C.c_ulong(); prop = C.POINTER(C.c_ulong)()
        x11.XGetWindowProperty(C.c_void_p(self.d), C.c_ulong(self.root), C.c_ulong(self.a_active), 0, 1, 0, 0,
                               C.byref(at), C.byref(fmt), C.byref(n), C.byref(rem), C.byref(prop))
        return int(prop[0]) if n.value else 0

    def toplevels(self):
        """mapped, non-override-redirect children of the root, bottom -> top"""
        r = C.c_ulong(); p = C.c_ulong(); kids = C.POINTER(C.c_ulong)(); n = C.c_uint()
        if not x11.XQueryTree(C.c_void_p(self.d), C.c_ulong(self.root), C.byref(r), C.byref(p),
                              C.byref(kids), C.byref(n)):
            return []
        out = []
        for i in range(n.value):
            w = kids[i]; a = Attrs()
            if not x11.XGetWindowAttributes(C.c_void_p(self.d), C.c_ulong(w), C.byref(a)):
                continue
            if a.map_state == IsViewable and not a.override_redirect and a.width > 64 and a.height > 64:
                out.append(int(w))
        return out

    def wm_class(self, w):
        h = ClassHint()
        if not x11.XGetClassHint(C.c_void_p(self.d), C.c_ulong(w), C.byref(h)):
            return ''
        return (h.res_class or b'').decode('utf-8', 'replace').lower()

    def title(self, w):
        nm = C.c_char_p()
        if x11.XFetchName(C.c_void_p(self.d), C.c_ulong(w), C.byref(nm)) and nm.value:
            return nm.value.decode('utf-8', 'replace')
        return ''

    def raise_window(self, w):
        x11.XRaiseWindow(C.c_void_p(self.d), C.c_ulong(w))
        x11.XFlush(C.c_void_p(self.d))

    def close_window(self, w):
        ev = ClientMessage()
        ev.type = 33                                        # ClientMessage
        ev.window = w
        ev.message_type = self.a_protocols
        ev.format = 32
        ev.l[0] = self.a_delete
        ev.l[1] = 0
        x11.XSendEvent(C.c_void_p(self.d), C.c_ulong(w), 0, C.c_long(0), C.byref(ev))
        x11.XFlush(C.c_void_p(self.d))


def run(dpy, sleep=0.5, now=time.monotonic):
    since = {}                                              # offending window -> first time seen on top
    closed = {}
    while True:
        mode, classes, grace = conf()
        if mode == 'off':
            time.sleep(sleep)
            continue
        active = dpy.active_window()
        tops = dpy.toplevels()
        waited = now() - since.get(tops[-1], now()) if tops else 0.0
        action, win = decide(active, tops, waited, grace)
        if not action:
            since.clear()
            time.sleep(sleep)
            continue
        since.setdefault(win, now())
        cls = dpy.wm_class(win)
        if action == 'raise':
            dpy.raise_window(active)                        # phoc ignores this today; harmless and free if it changes
        elif cls in classes and mode == 'close':
            if now() - closed.get(win, 0) > 5:              # never hammer a window that refuses to close
                log('closing 0x%x %r (class %s) - it is on top of the X stack but phosh shows 0x%x %r'
                    % (win, dpy.title(win), cls, active, dpy.title(active)))
                dpy.close_window(win)
                closed[win] = now()
        else:
            if now() - closed.get(win, 0) > 30:
                log('0x%x %r (class %s) holds X input while phosh shows 0x%x %r - left alone (mode=%s)'
                    % (win, dpy.title(win), cls, active, dpy.title(active), mode))
                closed[win] = now()
        time.sleep(sleep)


def main():
    name = os.environ.get('DISPLAY', ':0').encode()
    for i in range(600):                                    # Xwayland starts on demand, after the session
        try:
            dpy = Display(name)
            break
        except OSError:
            time.sleep(1)
    else:
        log('no X display after 600 s, giving up')
        return 1
    log('start on', name.decode(), 'conf', conf())
    run(dpy)


if __name__ == '__main__':
    sys.exit(main())
