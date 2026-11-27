# Platform differences

| | Windows | macOS | Linux |
|---|---|---|---|
| Plugins folder | `%HOME%\.config\pi-gpui\plugins\<id>` (`%USERPROFILE%` if `HOME` is unset, `XDG_CONFIG_HOME` wins) | `~/Library/Application Support/pi-gpui/plugins/<id>` | `~/.config/pi-gpui/plugins/<id>` |
| `cmd-` in keybindings | Ctrl | ⌘ | Ctrl |
| `run` lookup | PATH with `.exe`, `.cmd`, `.bat` | PATH and the login shell (nvm) | PATH and the login shell |
| Shell scripts in `run` | **not started** (`./run.sh`) | yes | yes |
| Node's home folder | `USERPROFILE` | `HOME` | `HOME` |
| Temp folder | `%TEMP%` | `$TMPDIR` | `/tmp` |

Built-in plugins (none ship today) come from the app bundle, or `plugins/` in
the repository when the app runs from a build.

## Windows notes

- Prefer `["node", "plugin.mjs"]` or `["python", "plugin.py"]` in `run`. If a
  launcher is needed, ship a `.cmd` next to the `.sh`; there is no per-OS `run`
  in protocol 1 (yet).
- Python packages may lack wheels for the newest Python (ViZDoom has none for
  3.14): pick the version in the launcher (`py -3.12`).
- Paths written into tool results use backslashes; show them in backticks.
