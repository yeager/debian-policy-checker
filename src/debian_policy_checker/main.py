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
import shutil
from debian_policy_checker.accessibility import AccessibilityManager

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

# Lintian tag categories for grouping
TAG_CATEGORIES = {
    "debian/control": ["depends", "build-depends", "conflicts", "replaces", "provides",
                       "description", "maintainer", "uploaders", "homepage", "vcs-",
                       "section", "priority", "essential", "standards-version"],
    "debian/copyright": ["copyright", "license", "dep5"],
    "debian/changelog": ["changelog", "version", "urgency", "distribution"],
    "debian/rules": ["rules", "dh-", "debhelper", "override_dh", "clean-should"],
    "file-permissions": ["permission", "executable", "setuid", "setgid", "world-writable"],
    "shared-libraries": ["shlib", "symbols", "soname", "library", "ldconfig"],
    "init-scripts": ["init.d", "systemd", "service-file", "daemon"],
    "packaging": ["package-", "binary-", "source-", "arch-", "multi-arch"],
    "spelling": ["spelling"],
    "manpages": ["manpage", "manual"],
}

# Fix recommendations per common lintian tags
TAG_RECOMMENDATIONS = {
    "no-copyright-file": _("Add a debian/copyright file following DEP-5 format."),
    "changelog-file-missing-in-native-package": _("Create debian/changelog using 'dch --create'."),
    "wrong-bug-number-in-closes": _("Use the correct Debian BTS bug number in changelog closes."),
    "description-synopsis-is-duplicated": _("Make the long description different from the synopsis."),
    "depends-on-essential-package-without-using-version": _("Remove dependency on essential package or add a versioned dependency."),
    "binary-without-manpage": _("Add a manpage for each binary in your package."),
    "copyright-without-copyright-notice": _("Add proper copyright notices with years and holder names."),
    "maintainer-script-without-set-e": _("Add 'set -e' at the top of maintainer scripts."),
    "description-starts-with-leading-spaces": _("Remove leading spaces from the package description."),
    "latest-debian-changelog-entry-changed-to-native": _("Check if the package should be native or add an upstream version."),
    "package-must-activate-ldconfig-trigger": _("Add a trigger for ldconfig or use dh_makeshlibs."),
    "shlib-without-versioned-soname": _("Ensure shared libraries have a versioned SONAME."),
    "hardening-no-relro": _("Enable RELRO hardening: add 'export DEB_BUILD_MAINT_OPTIONS=hardening=+all' to debian/rules."),
    "hardening-no-fortify-functions": _("Enable fortify functions: add hardening=+all to DEB_BUILD_MAINT_OPTIONS."),
    "hardening-no-pie": _("Enable PIE: add hardening=+all to DEB_BUILD_MAINT_OPTIONS."),
    "no-symbols-control-file": _("Create a symbols file using dpkg-gensymbols or pkg-kde-tools."),
    "init.d-script-missing-lsb-description": _("Add LSB headers to the init.d script."),
    "obsolete-relation-form-in-source": _("Replace '<' with '<<' and '>' with '>>' in dependency relations."),
    "spelling-error-in-description": _("Fix the spelling error in the package description."),
    "executable-not-elf-or-script": _("Ensure executable files are proper ELF binaries or scripts with shebang."),
}


def _load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {"welcome_shown": False, "pedantic": True, "experimental": True}


def _save_settings(s):
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w") as f:
        json.dump(s, f, indent=2)


def _get_recommendation(tag):
    """Get a fix recommendation for a lintian tag."""
    if tag in TAG_RECOMMENDATIONS:
        return TAG_RECOMMENDATIONS[tag]
    # Try partial matches
    for key, rec in TAG_RECOMMENDATIONS.items():
        if key in tag:
            return rec
    return _("Consult 'lintian-explain-tags %s' for details.") % tag


def _categorize_tag(tag):
    """Assign a lintian tag to a category."""
    tag_lower = tag.lower()
    for category, keywords in TAG_CATEGORIES.items():
        for kw in keywords:
            if kw in tag_lower:
                return category
    return _("other")


def _run_lintian(path, pedantic=True, experimental=True):
    """Run lintian on a .deb or source directory."""
    if not shutil.which("lintian"):
        return [{"severity": "E", "tag": "lintian-not-installed",
                 "detail": _("lintian is required. Install with: sudo apt install lintian"),
                 "package": "", "category": "packaging"}]

    results = []
    cmd = ["lintian", "--display-info"]
    if experimental:
        cmd.append("--display-experimental")
    if pedantic:
        cmd.append("--pedantic")
    cmd.append(path)

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r'(\w):\s+(\S+):\s+(\S+)\s*(.*)', line)
            if m:
                tag = m.group(3)
                results.append({
                    "severity": m.group(1),
                    "package": m.group(2),
                    "tag": tag,
                    "detail": m.group(4),
                    "category": _categorize_tag(tag),
                    "recommendation": _get_recommendation(tag),
                })
            elif line:
                results.append({"severity": "I", "package": "", "tag": line, "detail": "",
                               "category": _("other"), "recommendation": ""})
    except subprocess.TimeoutExpired:
        results.append({"severity": "E", "tag": "timeout",
                       "detail": _("lintian timed out after 120 seconds"),
                       "package": "", "category": "packaging", "recommendation": ""})
    except Exception as e:
        results.append({"severity": "E", "tag": "error", "detail": str(e),
                       "package": "", "category": "packaging", "recommendation": ""})
    return results


class DebianPolicyCheckerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=_("Debian Policy Checker"),
                         default_width=1000, default_height=700)
        self.settings = _load_settings()
        self._results = []

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header
        headerbar = Adw.HeaderBar()
        title_widget = Adw.WindowTitle(title=_("Debian Policy Checker"), subtitle="")
        headerbar.set_title_widget(title_widget)
        self._title_widget = title_widget

        open_btn = Gtk.Button(icon_name="document-open-symbolic",
                              tooltip_text=_("Open .deb or package directory"))
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

        # Content area with stack
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_vexpand(True)

        # Empty state
        self._empty = Adw.StatusPage()
        self._empty.set_icon_name("dialog-information-symbolic")
        self._empty.set_title(_("No package checked"))
        self._empty.set_description(_("Open or drag a .deb file to check policy compliance."))
        self._empty.set_vexpand(True)
        self._stack.add_named(self._empty, "empty")

        # Success state (no errors)
        self._success = Adw.StatusPage()
        self._success.set_icon_name("emblem-ok-symbolic")
        self._success.set_title(_("Everything looks good! 👍"))
        self._success.set_description(_("No policy issues found. Your package is compliant."))
        self._success.set_vexpand(True)
        self._stack.add_named(self._success, "success")

        # Loading state
        self._loading = Adw.StatusPage()
        self._loading.set_icon_name("content-loading-symbolic")
        self._loading.set_title(_("Checking package..."))
        self._loading.set_description(_("Running lintian analysis. This may take a moment."))
        spinner = Gtk.Spinner(spinning=True)
        spinner.set_halign(Gtk.Align.CENTER)
        spinner.set_size_request(32, 32)
        self._loading.set_child(spinner)
        self._loading.set_vexpand(True)
        self._stack.add_named(self._loading, "loading")

        # Results view
        results_scroll = Gtk.ScrolledWindow(vexpand=True)
        self._results_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._results_box.set_margin_start(12)
        self._results_box.set_margin_end(12)
        self._results_box.set_margin_top(8)
        self._results_box.set_margin_bottom(8)
        results_scroll.set_child(self._results_box)
        self._stack.add_named(results_scroll, "results")

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

        # Drag and drop support
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect("drop", self._on_drop)
        self.add_controller(drop_target)

        if not self.settings.get("welcome_shown"):
            GLib.idle_add(self._show_welcome)

    def _on_drop(self, drop_target, value, x, y):
        """Handle drag-and-drop of .deb files."""
        if isinstance(value, Gio.File):
            path = value.get_path()
            if path and path.endswith(".deb"):
                self._check_package(path)
                return True
            else:
                self._status.set_text(_("Only .deb files are supported for drag and drop"))
        return False

    def _show_welcome(self):
        dialog = Adw.Dialog()
        dialog.set_title(_("Welcome"))
        dialog.set_content_width(420)
        dialog.set_content_height(480)

        page = Adw.StatusPage()
        page.set_icon_name("dialog-information-symbolic")
        page.set_title(_("Welcome to Debian Policy Checker"))
        page.set_description(_("Check Debian Policy compliance.\n\n"
            "✓ Drag & drop .deb files to check\n"
            "✓ Powered by lintian\n"
            "✓ Errors grouped by category\n"
            "✓ Fix recommendations for each issue\n"
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
        ff = Gtk.FileFilter()
        ff.set_name(_("Debian packages (*.deb)"))
        ff.add_pattern("*.deb")
        ff.add_mime_type("application/vnd.debian.binary-package")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(ff)
        all_ff = Gtk.FileFilter()
        all_ff.set_name(_("All files"))
        all_ff.add_pattern("*")
        filters.append(all_ff)
        dialog.set_filters(filters)
        dialog.open(self, None, self._on_file_opened)

    def _on_file_opened(self, dialog, result):
        try:
            f = dialog.open_finish(result)
            path = f.get_path()
            self._check_package(path)
        except Exception:
            pass

    def _check_package(self, path):
        """Start checking a package."""
        self._status.set_text(_("Checking %s...") % os.path.basename(path))
        self._title_widget.set_subtitle(os.path.basename(path))
        self._stack.set_visible_child_name("loading")
        threading.Thread(target=self._do_check, args=(path,), daemon=True).start()

    def _do_check(self, path):
        results = _run_lintian(path,
                               pedantic=self.settings.get("pedantic", True),
                               experimental=self.settings.get("experimental", True))
        GLib.idle_add(self._show_results, results)

    def _show_results(self, results):
        self._results = results

        # Clear previous results
        child = self._results_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self._results_box.remove(child)
            child = next_child

        # No issues found — show success
        if not results:
            self._stack.set_visible_child_name("success")
            self._status.set_text(_("No issues found — package is policy compliant!"))
            return

        severity_icons = {"E": "❌", "W": "⚠️", "I": "ℹ️", "N": "📝", "P": "🔍"}
        severity_names = {
            "E": _("Error"), "W": _("Warning"), "I": _("Info"),
            "N": _("Note"), "P": _("Pedantic")
        }

        # Count by severity
        counts = {}
        for r in results:
            s = r["severity"]
            counts[s] = counts.get(s, 0) + 1

        # Summary banner
        summary_frame = Gtk.Frame()
        summary_frame.add_css_class("card")
        summary_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        summary_box.set_margin_start(16)
        summary_box.set_margin_end(16)
        summary_box.set_margin_top(12)
        summary_box.set_margin_bottom(12)

        summary_title = Gtk.Label(label=_("Summary"))
        summary_title.add_css_class("title-3")
        summary_title.set_halign(Gtk.Align.START)
        summary_box.append(summary_title)

        summary_text_parts = []
        for sev_code, label in [("E", _("Errors")), ("W", _("Warnings")),
                                 ("I", _("Info")), ("N", _("Notes")), ("P", _("Pedantic"))]:
            if sev_code in counts:
                icon = severity_icons.get(sev_code, "")
                summary_text_parts.append(f"{icon} {counts[sev_code]} {label}")

        summary_label = Gtk.Label(label="   ".join(summary_text_parts))
        summary_label.set_halign(Gtk.Align.START)
        summary_label.add_css_class("body")
        summary_box.append(summary_label)

        total = len(results)
        total_label = Gtk.Label(label=_("Total: %d issues") % total)
        total_label.set_halign(Gtk.Align.START)
        total_label.add_css_class("dim-label")
        summary_box.append(total_label)

        summary_frame.set_child(summary_box)
        self._results_box.append(summary_frame)

        # Group by category
        categories = {}
        for r in results:
            cat = r.get("category", _("other"))
            categories.setdefault(cat, []).append(r)

        # Sort: errors-heavy categories first
        def cat_sort_key(item):
            cat, items = item
            errors = sum(1 for i in items if i["severity"] == "E")
            warnings = sum(1 for i in items if i["severity"] == "W")
            return (-errors, -warnings, cat)

        for cat, items in sorted(categories.items(), key=cat_sort_key):
            cat_errors = sum(1 for i in items if i["severity"] == "E")
            cat_warnings = sum(1 for i in items if i["severity"] == "W")

            group = Adw.PreferencesGroup()
            badge_parts = []
            if cat_errors:
                badge_parts.append(f"❌ {cat_errors}")
            if cat_warnings:
                badge_parts.append(f"⚠️ {cat_warnings}")
            if len(items) - cat_errors - cat_warnings > 0:
                badge_parts.append(f"ℹ️ {len(items) - cat_errors - cat_warnings}")

            group.set_title(f"{cat}  ({', '.join(badge_parts)})")

            for r in items:
                icon = severity_icons.get(r["severity"], "❓")
                sev = severity_names.get(r["severity"], r["severity"])

                row = Adw.ExpanderRow()
                row.set_title(f"{icon} {GLib.markup_escape_text(r['tag'])}")
                if r.get("detail"):
                    row.set_subtitle(GLib.markup_escape_text(r["detail"]))

                # Severity badge
                badge = Gtk.Label(label=sev)
                badge.add_css_class("caption")
                if r["severity"] == "E":
                    badge.add_css_class("error")
                elif r["severity"] == "W":
                    badge.add_css_class("warning")
                row.add_suffix(badge)

                # Recommendation row inside expander
                rec = r.get("recommendation", "")
                if rec:
                    rec_row = Adw.ActionRow()
                    rec_row.set_title(_("💡 Recommendation"))
                    rec_row.set_subtitle(GLib.markup_escape_text(rec))
                    rec_row.set_subtitle_lines(5)
                    row.add_row(rec_row)

                # Package info row
                if r.get("package"):
                    pkg_row = Adw.ActionRow()
                    pkg_row.set_title(_("Package"))
                    pkg_row.set_subtitle(GLib.markup_escape_text(r["package"]))
                    row.add_row(pkg_row)

                group.add(row)

            self._results_box.append(group)

        self._stack.set_visible_child_name("results")
        errors = counts.get("E", 0)
        warnings = counts.get("W", 0)
        self._status.set_text(
            _("%(total)d issues: %(errors)d errors, %(warnings)d warnings") %
            {"total": total, "errors": errors, "warnings": warnings}
        )


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

    def _on_settings(self, *_args):
        if not self.window:
            return
        dialog = Adw.PreferencesDialog()
        dialog.set_title(_("Settings"))
        page = Adw.PreferencesPage()

        group = Adw.PreferencesGroup(title=_("Lintian"))
        row = Adw.SwitchRow(title=_("Show pedantic warnings"))
        row.set_active(self.window.settings.get("pedantic", True))
        row.connect("notify::active", self._on_pedantic_changed)
        group.add(row)
        row2 = Adw.SwitchRow(title=_("Show experimental tags"))
        row2.set_active(self.window.settings.get("experimental", True))
        row2.connect("notify::active", self._on_experimental_changed)
        group.add(row2)
        page.add(group)
        dialog.add(page)
        dialog.present(self.window)

    def _on_pedantic_changed(self, row, *_):
        self.window.settings["pedantic"] = row.get_active()
        _save_settings(self.window.settings)

    def _on_experimental_changed(self, row, *_):
        self.window.settings["experimental"] = row.get_active()
        _save_settings(self.window.settings)

    def _on_copy_debug(self, *_args):
        if not self.window:
            return
        from . import __version__
        lintian_ver = "not installed"
        try:
            r = subprocess.run(["lintian", "--version"], capture_output=True, text=True, timeout=5)
            lintian_ver = r.stdout.strip()
        except Exception:
            pass
        info = (
            f"Debian Policy Checker {__version__}\n"
            f"Lintian: {lintian_ver}\n"
            f"Python {sys.version}\n"
            f"GTK {Gtk.MAJOR_VERSION}.{Gtk.MINOR_VERSION}\n"
            f"Adw {Adw.MAJOR_VERSION}.{Adw.MINOR_VERSION}\n"
            f"OS: {os.uname().sysname} {os.uname().release}\n"
        )
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(info)
        self.window._status.set_text(_("Debug info copied"))

    def _on_shortcuts(self, *_args):
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

    def _on_about(self, *_args):
        from . import __version__
        dialog = Adw.AboutDialog(
            application_name=_("Debian Policy Checker"),
            application_icon="dialog-information-symbolic",
            version=__version__,
            developer_name="Daniel Nylander",
            website="https://github.com/yeager/debian-policy-checker",
            license_type=Gtk.License.GPL_3_0,
            issue_url="https://github.com/yeager/debian-policy-checker/issues",
            comments=_("Validate packages against the Debian Policy Manual using lintian, with grouped results and fix recommendations."),
        )
        dialog.add_credit_section(_("Thanks to"), [
            "GTK https://gtk.org",
            "libadwaita https://gnome.pages.gitlab.gnome.org/libadwaita/",
            "Python https://python.org",
            "lintian https://lintian.debian.org",
            "Transifex https://transifex.com",
        ])
        dialog.present(self.window)

    def _on_quit(self, *_args):
        self.quit()


def main():
    app = DebianPolicyCheckerApp()
    app.run(sys.argv)


# --- Session restore ---
import json as _json
import os as _os

def _save_session(window, app_name):
    config_dir = _os.path.join(_os.path.expanduser('~'), '.config', app_name)
    _os.makedirs(config_dir, exist_ok=True)
    state = {'width': window.get_width(), 'height': window.get_height(),
             'maximized': window.is_maximized()}
    try:
        with open(_os.path.join(config_dir, 'session.json'), 'w') as f:
            _json.dump(state, f)
    except OSError:
        pass

def _restore_session(window, app_name):
    path = _os.path.join(_os.path.expanduser('~'), '.config', app_name, 'session.json')
    try:
        with open(path) as f:
            state = _json.load(f)
        window.set_default_size(state.get('width', 800), state.get('height', 600))
        if state.get('maximized'):
            window.maximize()
    except (FileNotFoundError, _json.JSONDecodeError, OSError):
        pass


# --- Fullscreen toggle (F11) ---
def _setup_fullscreen(window, app):
    """Add F11 fullscreen toggle."""
    from gi.repository import Gio
    if not app.lookup_action('toggle-fullscreen'):
        action = Gio.SimpleAction.new('toggle-fullscreen', None)
        action.connect('activate', lambda a, p: (
            window.unfullscreen() if window.is_fullscreen() else window.fullscreen()
        ))
        app.add_action(action)
        app.set_accels_for_action('app.toggle-fullscreen', ['F11'])


# --- Plugin system ---
import importlib.util
import os as _pos

def _load_plugins(app_name):
    """Load plugins from ~/.config/<app>/plugins/."""
    plugin_dir = _pos.path.join(_pos.path.expanduser('~'), '.config', app_name, 'plugins')
    plugins = []
    if not _pos.path.isdir(plugin_dir):
        return plugins
    for fname in sorted(_pos.listdir(plugin_dir)):
        if fname.endswith('.py') and not fname.startswith('_'):
            path = _pos.path.join(plugin_dir, fname)
            try:
                spec = importlib.util.spec_from_file_location(fname[:-3], path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                plugins.append(mod)
            except Exception as e:
                print(f"Plugin {fname}: {e}")
    return plugins
