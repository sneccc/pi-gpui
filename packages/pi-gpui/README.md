# pi-gpui

`/gpui` for pi: continue the session you have open in the terminal in [Pi GPUI](https://github.com/bytesbay/pi-gpui), the native desktop app for pi on macOS, Linux and Windows, written in Rust with GPUI.

![Pi GPUI](https://raw.githubusercontent.com/bytesbay/pi-gpui/main/docs/banner.png)

## Install

```sh
pi install npm:pi-gpui
```

The app, if you don't have it yet:

```sh
brew install --cask bytesbay/tap/pi-gpui            # macOS
curl -fsSL https://pi-gpui.dev/install.sh | sh      # Linux
```

On Windows (the [sneccc/pi-gpui](https://github.com/sneccc/pi-gpui) fork), `/gpui` finds `pi-gpui.exe` through `PI_GPUI_EXE`, `PATH`, `%LOCALAPPDATA%\Programs\pi-gpui\`, or the exe registered for `pi-gpui://` links (`pi-gpui.exe --register-url-scheme`).

## Use

Type `/gpui` in pi. This pi exits, and the same session opens in Pi GPUI with its project in the sidebar. Without a saved session (`--no-session`), the project opens there with a new session.

The terminal pi exits first because pi does not protect a session file from two writers.

Needs Pi GPUI 0.1.10 or newer on macOS, 0.2.0 or newer on Linux. Any tool can do the same with a link:

```sh
open "pi-gpui://open?workspace=/path/to/project&session=/path/to/session.jsonl"      # macOS
pi-gpui "pi-gpui://open?workspace=/path/to/project&session=/path/to/session.jsonl"   # Linux
pi-gpui.exe "pi-gpui://open?workspace=C%3A%2Fproject&session=..."                    # Windows
```

## License

MIT
