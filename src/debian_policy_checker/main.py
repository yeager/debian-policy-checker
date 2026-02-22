"""Debian Policy Checker — Validate packages against Debian Policy Manual."""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk, Gio, GLib, Pango

import gettext
import locale
import os
import sys
import json
import datetime
import threading
import subprocess
import re

LOCALE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "po")
if not os.path.isdir(LOCALE_DIR):
    LOCALE_DIR = "/usr/share/locale"
locale.bindtextdomain("debian-policy-checker", LOCALE_DIR)
gettext.bindtextdomain("debian-policy-checker", LOCALE_DIR)
gettext.textdomain("debian-policy-checker")
_ = gettext.gettext

APP_ID = "se.danielnylander.debian.policy.checker"
SETTINGS_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "debian-policy-checker"
)
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")


def _load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {"welcome_shown": False}


def _save_settings(s):
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(s, f, indent=2)



def _run_lintian(path):
    """Run lintian on a .deb or source directory."""
    results = []
    try:
        r = subprocess.run(["lintian", "--display-info", "--display-experimental",
                           "--pedantic", path],
                          capture_output=True, text=True, timeout=120)
        for line in r.stdout.splitlines():
            m = re.match(r'(\w):\s+(\S+):\s+(\S+)\s+(.*)', line)
            if m:
                results.append({
                    "severity": m.group(1),
                    "package": m.group(2),
                    "tag": m.group(3),
                    "detail": m.group(4),
                })
            else:
                results.append({"severity": "I", "package": "", "tag": line, "detail": ""})
    except FileNotFoundError:
        results.append({"severity": "E", "tag": "lintian-not-installed",
                       "detail": _("Install with: sudo apt install lintian"), "package": ""})
    except Exception as e:
        results.append({"severity": "E", "tag": "error", "detail": str(e), "package": ""})
    return results



class DebianPolicyCheckerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=_("Debian Policy Checker"), default_width=1000, default_height=700)
        self.settings = _load_settings()
        self._results = []

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header
        headerbar = Adw.HeaderBar()
        title_widget = Adw.WindowTitle(title=_("Debian Policy Checker"), subtitle="")
        headerbar.set_title_widget(title_widget)
        self._title_widget = title_widget

        
        open_btn = Gtk.Button(icon_name="document-open-symbolic", tooltip_text=_("Open .deb or package directory"))
        open_btn.connect("clicked", self._on_open)
        headerbar.pack_start(open_btn)

        # Menu
        menu = Gio.Menu()
        menu.append(_("Settings"), "app.settings")
        menu.append(_("Copy Debug Info"), "app.copy-debug")
        menu.append(_("Keyboard Shortcuts"), "app.shortcuts")
        menu.append(_("About Debian Policy Checker"), "app.about")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        headerbar.pack_end(menu_btn)

        main_box.append(headerbar)

        
        scroll = Gtk.ScrolledWindow(vexpand=True)
        self._list = Gtk.ListBox()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list.add_css_class("boxed-list")
        self._list.set_margin_start(12)
        self._list.set_margin_end(12)
        self._list.set_margin_top(8)
        self._list.set_margin_bottom(8)
        scroll.set_child(self._list)
        
        self._empty = Adw.StatusPage()
        self._empty.set_icon_name("dialog-information-symbolic")
        self._empty.set_title(_("No package checked"))
        self._empty.set_description(_("Open a .deb file or package directory to check policy compliance."))
        self._empty.set_vexpand(True)
        
        self._stack = Gtk.Stack()
        self._stack.add_named(self._empty, "empty")
        self._stack.add_named(scroll, "list")
        self._stack.set_vexpand(True)
        main_box.append(self._stack)

        # Status bar
        self._status = Gtk.Label(label=_("Ready"), xalign=0)
        self._status.set_margin_start(12)
        self._status.set_margin_end(12)
        self._status.set_margin_top(4)
        self._status.set_margin_bottom(4)
        self._status.add_css_class("dim-label")
        main_box.append(self._status)

        self.set_content(main_box)

        if not self.settings.get("welcome_shown"):
            GLib.idle_add(self._show_welcome)

    def _show_welcome(self):
        dialog = Adw.Dialog()
        dialog.set_title(_("Welcome"))
        dialog.set_content_width(420)
        dialog.set_content_height(480)

        page = Adw.StatusPage()
        page.set_icon_name("dialog-information-symbolic")
        page.set_title(_("Welcome to Debian Policy Checker"))
        page.set_description(_("Check Debian Policy compliance.\n\n"
            "✓ Validate packages against Debian Policy\n"
            "✓ Clear error descriptions with fix suggestions\n"
            "✓ Integrates with lintian output\n"
            "✓ Policy section references\n"
            "✓ Export validation reports"))

        btn = Gtk.Button(label=_("Get Started"))
        btn.add_css_class("suggested-action")
        btn.add_css_class("pill")
        btn.set_halign(Gtk.Align.CENTER)
        btn.set_margin_top(12)
        btn.connect("clicked", self._on_welcome_close, dialog)
        page.set_child(btn)

        box = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        hb.set_show_title(False)
        box.add_top_bar(hb)
        box.set_content(page)
        dialog.set_child(box)
        dialog.present(self)

    def _on_welcome_close(self, btn, dialog):
        self.settings["welcome_shown"] = True
        _save_settings(self.settings)
        dialog.close()

    
    def _on_open(self, btn):
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Open package"))
        dialog.open(self, None, self._on_file_opened)

    def _on_file_opened(self, dialog, result):
        try:
            f = dialog.open_finish(result)
            path = f.get_path()
            self._status.set_text(_("Checking %s...") % path)
            self._title_widget.set_subtitle(os.path.basename(path))
            threading.Thread(target=self._do_check, args=(path,), daemon=True).start()
        except:
            pass

    def _do_check(self, path):
        results = _run_lintian(path)
        GLib.idle_add(self._show_results, results)

    def _show_results(self, results):
        self._results = results
        while True:
            row = self._list.get_row_at_index(0)
            if row is None:
                break
            self._list.remove(row)
        
        severity_icons = {"E": "❌", "W": "⚠️", "I": "ℹ️", "N": "📝", "P": "🔍"}
        severity_names = {"E": _("Error"), "W": _("Warning"), "I": _("Info"), "N": _("Note"), "P": _("Pedantic")}
        
        errors = sum(1 for r in results if r["severity"] == "E")
        warnings = sum(1 for r in results if r["severity"] == "W")
        
        for r in results:
            icon = severity_icons.get(r["severity"], "❓")
            sev = severity_names.get(r["severity"], r["severity"])
            row = Adw.ActionRow()
            row.set_title(f"{icon} {r['tag']}")
            row.set_subtitle(r.get("detail", ""))
            badge = Gtk.Label(label=sev)
            badge.add_css_class("caption")
            if r["severity"] == "E":
                badge.add_css_class("error")
            row.add_suffix(badge)
            self._list.append(row)
        
        self._stack.set_visible_child_name("list")
        self._status.set_text(_("%(total)d issues: %(errors)d errors, %(warnings)d warnings") %
                            {"total": len(results), "errors": errors, "warnings": warnings})


class DebianPolicyCheckerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window = None

        for name, callback in [
            ("settings", self._on_settings),
            ("copy-debug", self._on_copy_debug),
            ("shortcuts", self._on_shortcuts),
            ("about", self._on_about),
            ("quit", self._on_quit),
        ]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        self.set_accels_for_action("app.quit", ["<Ctrl>q"])
        self.set_accels_for_action("app.shortcuts", ["<Ctrl>slash"])

    def do_activate(self):
        if not self.window:
            self.window = DebianPolicyCheckerWindow(self)
        self.window.present()

    def _on_settings(self, *_):
        if not self.window:
            return
        dialog = Adw.PreferencesDialog()
        dialog.set_title(_("Settings"))
        page = Adw.PreferencesPage()
        
        group = Adw.PreferencesGroup(title=_("Lintian"))
        row = Adw.SwitchRow(title=_("Show pedantic warnings"))
        row.set_active(True)
        group.add(row)
        row2 = Adw.SwitchRow(title=_("Show experimental tags"))
        row2.set_active(True)
        group.add(row2)
        page.add(group)
        dialog.add(page)
        dialog.present(self.window)

    def _on_copy_debug(self, *_):
        if not self.window:
            return
        from . import __version__
        info = (
            f"Debian Policy Checker {__version__}\n"
            f"Python {sys.version}\n"
            f"GTK {Gtk.MAJOR_VERSION}.{Gtk.MINOR_VERSION}\n"
            f"Adw {Adw.MAJOR_VERSION}.{Adw.MINOR_VERSION}\n"
            f"OS: {os.uname().sysname} {os.uname().release}\n"
        )
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(info)
        self.window._status.set_text(_("Debug info copied"))

    def _on_shortcuts(self, *_):
        if self.window:
            dialog = Gtk.ShortcutsWindow(transient_for=self.window)
            section = Gtk.ShortcutsSection(visible=True)
            group = Gtk.ShortcutsGroup(title=_("General"), visible=True)
            for accel, title in [
                ("<Ctrl>q", _("Quit")),
                ("<Ctrl>slash", _("Keyboard shortcuts")),
            ]:
                group.append(Gtk.ShortcutsShortcut(accelerator=accel, title=title, visible=True))
            section.append(group)
            dialog.append(section)
            dialog.present()

    def _on_about(self, *_):
        from . import __version__
        dialog = Adw.AboutDialog(
            application_name=_("Debian Policy Checker"),
            application_icon="dialog-information-symbolic",
            version=__version__,
            developer_name="Daniel Nylander",
            website="https://github.com/yeager/debian-policy-checker",
            license_type=Gtk.License.GPL_3_0,
            issue_url="https://github.com/yeager/debian-policy-checker/issues",
            comments=_("Validate packages against the Debian Policy Manual with clear error reports and fix suggestions."),
        )
        dialog.present(self.window)

    def _on_quit(self, *_):
        self.quit()


def main():
    app = DebianPolicyCheckerApp()
    app.run(sys.argv)
