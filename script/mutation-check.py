"""Checks that the Windows tests can fail: applies one deliberate bug at a time
and runs `cargo test`; a mutation the tests do not catch is reported.

    python -I script/mutation-check.py          # unit tests catch these
    python -I script/mutation-check.py --e2e    # windows-smoke.py catches these (headless)
    ... --only TEXT                             # only the mutations whose name contains TEXT

Each source file is restored afterwards, even on Ctrl+C. Run it on a clean
working tree so a crash cannot lose changes: it works on copies in memory
and writes the original text back byte for byte.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BS = chr(92)

# (name, file, original text, mutated text)
MUTATIONS = [
    (
        "mouse x and y swapped",
        "src/automation/windows.rs",
        "((y as u16 as isize) << 16) | (x as u16 as isize)",
        "((x as u16 as isize) << 16) | (y as u16 as isize)",
    ),
    (
        "mouse position ignores dpi scale",
        "src/automation/windows.rs",
        "let (x, y) = ((x * scale).round() as i32, (y * scale).round() as i32);",
        "let (x, y) = (x.round() as i32, y.round() as i32);",
    ),
    (
        "capture keeps the source alpha",
        "src/automation/windows.rs",
        "rgba.extend_from_slice(&[bgra[2], bgra[1], bgra[0], 255]);",
        "rgba.extend_from_slice(&[bgra[2], bgra[1], bgra[0], bgra[3]]);",
    ),
    (
        "capture does not swap red and blue",
        "src/automation/windows.rs",
        "rgba.extend_from_slice(&[bgra[2], bgra[1], bgra[0], 255]);",
        "rgba.extend_from_slice(&[bgra[0], bgra[1], bgra[2], 255]);",
    ),
    (
        "capture does not clamp to the window",
        "src/automation/windows.rs",
        "let width = (width.max(1) as usize).min(window_width - left);",
        "let width = width.max(1) as usize;",
    ),
    (
        "points ignore the dpi",
        "src/automation/windows.rs",
        "let scale = 96.0 / f64::from(dpi.max(96));",
        "let scale = 1.0;",
    ),
    (
        "drive letters are not path starts",
        "src/attachments.rs",
        "|| (cfg!(windows)\n                && bytes[index].is_ascii_alphabetic()",
        "|| (false\n                && bytes[index].is_ascii_alphabetic()",
    ),
    (
        "verbatim prefix stripped from UNC paths too",
        "src/sessions.rs",
        "Some(plain) if plain.as_bytes().get(1) == Some(&b':') => plain,",
        "Some(plain) => plain,",
    ),
    (
        "session folder keeps the verbatim prefix",
        "src/sessions.rs",
        "    let path = strip_verbatim(&path);\n    let trimmed",
        "    let trimmed",
    ),
    (
        "backslashes are not turned into slashes in links",
        "src/open_url.rs",
        "let byte = if cfg!(windows) && byte == b'" + BS + BS + "' { b'/' } else { byte };",
        "let byte = byte;",
    ),
    (
        "pi lookup does not try .cmd shims",
        "src/pi.rs",
        '["exe", "cmd", "bat", "com"]\n        .iter()\n        .map(|extension| format!("{name}.{extension}"))',
        '["exe"]\n        .iter()\n        .map(|extension| format!("{name}.{extension}"))',
    ),
    (
        "dead processes count as alive",
        "src/sessions.rs",
        "GetExitCodeProcess(process, &mut code) != 0 && code == STILL_ACTIVE",
        "GetExitCodeProcess(process, &mut code) != 0",
    ),
    (
        "trash deletes without checking the result",
        "src/platform.rs",
        "if code == 0 && operation.aborted == 0 {",
        "if true {",
    ),
]


# Bugs only the end-to-end test can see: (name, file, original, mutated).
# Not here: "automation takes the focus". Windows' foreground lock stops a
# background-launched app from stealing it anyway, so that check is best effort.
E2E_MUTATIONS = [
    (
        "child processes get a console window",
        "src/process.rs",
        "const CREATE_NO_WINDOW: u32 = 0x0800_0000;",
        "const CREATE_NO_WINDOW: u32 = 0;",
    ),
    (
        "the project keeps the verbatim prefix",
        "src/sessions.rs",
        "    if canonical.to_string_lossy() == plain {\n        Ok(canonical)\n    } else {\n        Ok(PathBuf::from(plain))\n    }",
        "    Ok(canonical)",
    ),
    (
        "children are not tied to the app (no job object)",
        "src/main.rs",
        "    process::contain_children();\n",
        "",
    ),
    (
        "pasting copied files does nothing (clipboard list ignored)",
        "src/platform.rs",
        "        if IsClipboardFormatAvailable(CF_HDROP) == 0 {",
        "        if IsClipboardFormatAvailable(CF_HDROP) != 0 {",
    ),
    (
        "a second launch never finds the running app (no handover)",
        "src/single_instance.rs",
        "Ok(mut pipe) => return pipe.write_all(line.as_bytes()).is_ok(),",
        "Ok(_) => return false,",
    ),
    (
        "caption buttons do nothing when clicked",
        "src/hub.rs",
        "            .on_click(move |_, window, _| act(window))\n",
        "",
    ),
    (
        "maximize never restores",
        "src/platform.rs",
        "let command = if IsZoomed(hwnd) != 0 {",
        "let command = if false {",
    ),
    (
        "the whole title bar is one caption area again (shadows the buttons)",
        "src/hub.rs",
        "            .border_color(colors.border_variant)\n            .on_mouse_down(",
        "            .border_color(colors.border_variant)\n            .window_control_area(WindowControlArea::Drag)\n            .on_mouse_down(",
    ),
    (
        "closing a session leaves it (and its pi) running",
        "src/hub.rs",
        "        let position = entry.chats.iter().position(|chat| &chat.view == view);\n        entry.chats.retain(|chat| &chat.view != view);\n        let workspace = entry.path.clone();",
        "        let position = entry.chats.iter().position(|chat| &chat.view == view);\n        let workspace = entry.path.clone();",
    ),
]


def read(path):
    with open(path, encoding="utf-8", newline="") as file:
        return file.read()


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as file:
        file.write(text)


def tests_pass(e2e=False):
    if e2e:
        built = subprocess.run(
            ["cargo", "build", "--release"], cwd=ROOT, capture_output=True, text=True
        )
        if built.returncode != 0:
            return False, "could not compile " + built.stdout + built.stderr
        subprocess.run(["taskkill", "/F", "/IM", "pi-gpui.exe"], capture_output=True)
        command = [sys.executable, "-I", os.path.join(ROOT, "script", "windows-smoke.py")]
    else:
        command = ["cargo", "test", "--release"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr


def main():
    e2e = "--e2e" in sys.argv[1:]
    mutations = E2E_MUTATIONS if e2e else MUTATIONS
    if "--only" in sys.argv[1:]:
        # --only TEXT: just the mutations whose name contains TEXT
        wanted = sys.argv[sys.argv.index("--only") + 1].lower()
        mutations = [m for m in mutations if wanted in m[0].lower()]
    survived = []
    for name, file, original, mutated in mutations:
        path = os.path.join(ROOT, file)
        text = read(path)
        crlf = "\r\n" in text
        needle = original.replace("\n", "\r\n") if crlf else original
        if text.count(needle) != 1:
            print(f"SKIP   {name}: pattern not found exactly once in {file}")
            survived.append(name + " (pattern missing)")
            continue
        replacement = mutated.replace("\n", "\r\n") if crlf else mutated
        write(path, text.replace(needle, replacement))
        try:
            passed, output = tests_pass(e2e)
        finally:
            write(path, text)
            if e2e:
                # leave the real binary built from the unmutated source
                subprocess.run(["cargo", "build", "--release"], cwd=ROOT, capture_output=True)
        compiled = "could not compile" not in output
        if passed:
            print(f"SURVIVED {name}  <-- the tests did not notice")
            survived.append(name)
        elif not compiled:
            print(f"BROKEN {name}: the mutation does not compile (fix the script)")
            survived.append(name + " (does not compile)")
        else:
            print(f"caught {name}")
    print()
    if survived:
        print(f"{len(survived)} problem(s):")
        for name in survived:
            print("  -", name)
        sys.exit(1)
    print(f"all {len(mutations)} mutations were caught")


if __name__ == "__main__":
    main()
