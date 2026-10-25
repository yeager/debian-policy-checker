# Debian Policy Checker

Validate packages against the Debian Policy Manual with fix suggestions.

Built with GTK4/Adwaita. Part of the [Danne L10n Suite](https://github.com/yeager/debian-repo).

## Installation

## Installation from PyPI

GTK4, libadwaita and PyGObject must be installed from your distribution (they
are native system libraries and are not installed by pip). On Debian/Ubuntu:

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pipx
pipx install --system-site-packages debian-policy-checker
```



### Debian/Ubuntu
```bash
sudo apt install debian-policy-checker
```

## License

GPL-3.0

## Author

Daniel Nylander — [danielnylander.se](https://danielnylander.se)
