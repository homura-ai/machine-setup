# GNOME preferences

This example dconf file contains keyboard shortcuts, input sources,
mouse and touchpad settings, interface themes, favorite apps, and extension
preferences. Install the apps and GNOME-compatible extensions you use before
loading it. The input sources include Anthy, libpinyin and m17n math-latex.
The theme settings use Ina, Flat-Remix-Blue-Dark and
Flat-Remix-Red-Darkest-fullPanel. Export and import any terminal profiles
separately on the machines that use them.

Run in the GUI session, from the directory containing this INI; first back up successfully (noclobber preserves an existing backup):
`mkdir -p ~/.local/state && (umask 077; set -C; dconf dump / > ~/.local/state/dconf-before.ini)`
Then apply: `dconf load / < dconf-filtered.ini`
