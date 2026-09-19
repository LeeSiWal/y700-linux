#!/usr/bin/env python3
"""Y700 display settings (GTK4/libadwaita): render resolution, rotation, brightness. Talks to y700-desktop.service over
/run/y700-desktop/ctl.sock (the same requests as y700-ctl); the service validates every value."""
import socket, sys
import gi
gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk

SOCK = '/run/y700-desktop/ctl.sock'
RESOLUTIONS = [(100, '100% · 1904×3040', '가장 선명 · 발열/전력 가장 큼'), (90, '90% · 1712×2736', ''),
               (80, '80% · 1524×2432', '권장 균형'), (67, '67% · 1276×2036', ''), (50, '50% · 952×1520', '발열/전력 가장 적음')]
ROTATIONS = [(270, '가로'), (90, '가로 (반대)'), (0, '세로')]

def ctl(req):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(8)
    try:
        s.connect(SOCK); s.sendall(req.encode()); return s.recv(256).decode().strip()
    except OSError as e: return 'error %s' % e
    finally: s.close()

def value(reply):
    try: return int(reply.split()[1]) if reply.split()[0] in ('ok', 'get') else None
    except (IndexError, ValueError): return None

class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title='Y700 디스플레이', default_width=520, default_height=760)
        self.toast = Adw.ToastOverlay(); tv = Adw.ToolbarView(); tv.add_top_bar(Adw.HeaderBar())
        page = Adw.PreferencesPage(); tv.set_content(page); self.toast.set_child(tv); self.set_content(self.toast)
        self.busy = False

        g = Adw.PreferencesGroup(title='렌더링 해상도',
                                 description='낮출수록 앱과 화면 합성이 적은 픽셀을 그려 발열과 전력이 줄어듭니다. '
                                             '화면 크기(UI 크기)는 그대로이고 선명도만 달라집니다.')
        page.add(g); cur = value(ctl('resolution get')); first = None; self.res_buttons = {}
        for pct, title, sub in RESOLUTIONS:
            row = Adw.ActionRow(title=title, subtitle=sub); b = Gtk.CheckButton(); b.set_valign(Gtk.Align.CENTER)
            if first: b.set_group(first)
            else: first = b
            b.set_active(pct == cur); b.connect('toggled', self.on_res, pct)
            row.add_prefix(b); row.set_activatable_widget(b); g.add(row); self.res_buttons[pct] = b

        g2 = Adw.PreferencesGroup(title='화면 방향'); page.add(g2)
        cur_r = value(ctl('rotate get')); first = None
        for deg, title in ROTATIONS:
            row = Adw.ActionRow(title=title); b = Gtk.CheckButton(); b.set_valign(Gtk.Align.CENTER)
            if first: b.set_group(first)
            else: first = b
            b.set_active(deg == cur_r); b.connect('toggled', self.on_rot, deg)
            row.add_prefix(b); row.set_activatable_widget(b); g2.add(row)

        g3 = Adw.PreferencesGroup(title='밝기'); page.add(g3)
        cur_b = value(ctl('brightness get')) or 50
        sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 5, 100, 5); sc.set_value(cur_b); sc.set_hexpand(True)
        sc.set_draw_value(True); sc.set_format_value_func(lambda s, v: '%d%%' % v)
        self.bl_pending = None; sc.connect('value-changed', self.on_bl)
        row = Adw.ActionRow(title='화면 밝기'); row.add_suffix(sc); g3.add(row)

    def say(self, text): self.toast.add_toast(Adw.Toast(title=text, timeout=3))

    def on_res(self, b, pct):
        if not b.get_active() or self.busy: return
        self.busy = True
        r = ctl('resolution %d' % pct)
        if r.startswith('ok'):
            self.say('해상도 %d%% 적용 · 6초 동안 화면 안정성을 확인합니다' % pct)
            GLib.timeout_add(7000, self.check_res, pct)
        else:
            self.say('변경 실패: ' + r); self.busy = False; self.sync_res()
    def check_res(self, pct):
        now = value(ctl('resolution get')); self.busy = False
        if now != pct: self.say('화면 불안정으로 %s%%로 되돌렸습니다' % now)
        self.sync_res(); return False
    def sync_res(self):
        now = value(ctl('resolution get'))
        if now in self.res_buttons and not self.res_buttons[now].get_active():
            self.busy = True; self.res_buttons[now].set_active(True); self.busy = False

    def on_rot(self, b, deg):
        if b.get_active():
            r = ctl('rotate %d' % deg)
            if not r.startswith('ok'): self.say('회전 실패: ' + r)

    def on_bl(self, sc):                                   # debounce: one request after the finger stops
        if self.bl_pending: GLib.source_remove(self.bl_pending)
        self.bl_pending = GLib.timeout_add(150, self.apply_bl, int(sc.get_value()))
    def apply_bl(self, v):
        self.bl_pending = None; r = ctl('brightness %d' % v)
        if not r.startswith('ok'): self.say('밝기 실패: ' + r)
        return False

class App(Adw.Application):
    def __init__(self): super().__init__(application_id='dev.y700.Display')
    def do_activate(self):
        w = self.props.active_window or Window(self); w.present()

if __name__ == '__main__': sys.exit(App().run(sys.argv))
