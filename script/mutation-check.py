"""Checks that the Windows tests can fail: applies one deliberate bug at a time
and runs `cargo test`; a mutation the tests do not catch is reported.

    python -I script/mutation-check.py          # unit tests catch these
    python -I script/mutation-check.py --e2e    # windows-smoke.py catches these (headless)
    python -I script/mutation-check.py --node   # node --test catches these (seconds)
    ... --only TEXT                             # only the mutations whose name contains TEXT

Each source file is restored afterwards, even on Ctrl+C. Run it on a clean
working tree so a crash cannot lose changes: it works on copies in memory
and writes the original text back byte for byte.
"""
import os
import subprocess
import sys
import time

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
MUTATIONS += [
    (
        "the app refuses sessions its own pi holds",
        "src/sessions.rs",
        'if lease["appPid"].as_u64() == Some(own_pid) {',
        'if lease["appPid"].as_u64() == Some(own_pid) && false {',
    ),
    (
        "a reused pid keeps an old lease alive",
        "src/sessions.rs",
        "&& claimed.abs_diff(actual) > START_TOLERANCE_MS",
        "&& claimed.abs_diff(actual) > u64::MAX - 1",
    ),
]

MUTATIONS += [
    (
        "expanded forms keep the highlight inside one question",
        "src/chat/plugin_form.rs",
        "    if !expanded {\n        return (step, target.rem_euclid(count as isize) as usize);\n    }",
        "    if true {\n        return (step, target.rem_euclid(count as isize) as usize);\n    }",
    ),
]

E2E_MUTATIONS = [
    (
        "options a plugin marks selected are not picked when the form opens",
        "src/chat/plugin_form.rs",
        "            .map(|question| question.preselected.clone())",
        "            .map(|_| Vec::new())",
        "grill",
    ),
    (
        "Right does not move to the next question",
        "src/chat/plugin_form.rs",
        "            \"right\" => self.form_go(id, 1, window, cx),",
        "            \"right\" => {}",
        "grill",
    ),
    (
        "pictures a tool returns show only after opening the row",
        "src/chat.rs",
        "                    .when(!images.is_empty(), |this| {\n                        this.child(\n                            div()\n                                .px(px(8.))",
        "                    .when(open && !images.is_empty(), |this| {\n                        this.child(\n                            div()\n                                .px(px(8.))",
        "images",
    ),
    (
        "Up in the empty message box does nothing",
        "src/text_input.rs",
        "        if self.recall_older(cx) {\n            return;\n        }",
        "",
        "edit",
    ),
    (
        "the pencil forks a new session instead of editing in place",
        "src/chat.rs",
        "        self.resolve_row(row, true, cx);",
        "        self.resolve_row(row, false, cx);",
        "edit",
    ),
    (
        "a session a terminal pi holds looks like any other in the sidebar",
        "src/hub.rs",
        ".and_then(|file| self.held_sessions.get(file))",
        ".and_then(|file| self.held_sessions.get(file).filter(|_| false))",
        "held",
    ),
    (
        "the project's reload button does not look for new sessions",
        "src/hub.rs",
        "            self.save(cx);\n        }\n        self.load_sessions(workspace, cx);\n        cx.notify();",
        "            self.save(cx);\n        }\n        cx.notify();",
        "refresh",  # optional: the windows-smoke.py --only check that covers it
    ),
    (
        "child processes get a console window",
        "src/process.rs",
        "const CREATE_NO_WINDOW: u32 = 0x0800_0000;",
        "const CREATE_NO_WINDOW: u32 = 0;",
        "main",
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
        "crash",
    ),
    (
        "pasting copied files does nothing (clipboard list ignored)",
        "src/platform.rs",
        "        if IsClipboardFormatAvailable(CF_HDROP) == 0 {",
        "        if IsClipboardFormatAvailable(CF_HDROP) != 0 {",
        "main",
    ),
    (
        "a second launch never finds the running app (no handover)",
        "src/single_instance.rs",
        "Ok(mut pipe) => return pipe.write_all(line.as_bytes()).is_ok(),",
        "Ok(_) => return false,",
        "handover",
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


NODE_MUTATIONS = [
    # (name, file, original, mutated): `node --test` must catch these. The lease
    # code is in two files; the test also checks the copies are identical, so
    # most mutations here change one copy and must still be caught.
    (
        "a pi takes over a session another live pi holds",
        "packages/pi-gpui/index.ts",
        "if (!fs.existsSync(session) || (!takeOver && leaseHolder(session))) return false;",
        "if (!fs.existsSync(session)) return false;",
    ),
    (
        "releasing removes another process's lease",
        "extensions/pi-gpui-bridge.ts",
        "\t\tif (readLease(session)?.pid !== process.pid) return;\n",
        "",
    ),
    (
        "a dead process's lease still counts",
        "packages/pi-gpui/index.ts",
        "lease.pid === process.pid || !processAlive(lease.pid)",
        "lease.pid === process.pid",
    ),
    (
        "the package leases sessions inside Pi GPUI too",
        "packages/pi-gpui/index.ts",
        '\t\tif (ctx.mode === "rpc") return;\n\t\tconst session',
        "\t\tconst session",
    ),
    (
        "/resume into a session Pi GPUI holds is not stopped",
        "packages/pi-gpui/index.ts",
        "if (!open) return { cancel: true };",
        "",
    ),
    (
        "the terminal pi keeps its lease after quitting",
        "packages/pi-gpui/index.ts",
        '\tpi.on("session_shutdown", () => {\n\t\tlease?.release();',
        '\tpi.on("session_shutdown", () => {',
    ),
    (
        "/gpui opens the app while pi is still running",
        "packages/pi-gpui/index.ts",
        "if (!gone()) return setTimeout(poll, 100);",
        "if (false) return setTimeout(poll, 100);",
    ),
]

NODE_MUTATIONS += [
    (
        "a new session's lease waits for an event instead of the file",
        "extensions/pi-gpui-bridge.ts",
        "\t\tif (!fs.existsSync(session)) return;\n\t\tclearInterval(appear);\n\t\trefresh();",
        "\t\treturn;",
    ),
    (
        "/gpui says it continues now while a run is still going",
        "packages/pi-gpui/index.ts",
        "return ctx.isIdle && !ctx.isIdle()",
        "return false",
    ),
]

NODE_MUTATIONS += [
    (
        "the app's pi respects a lease whose pid Windows already reused",
        "extensions/pi-gpui-bridge.ts",
        "holdSession(session, \"Pi GPUI\", { appPid }, true)",
        "holdSession(session, \"Pi GPUI\", { appPid })",
    ),
]

NODE_MUTATIONS += [
    (
        "the grill form does not put the recommended choice first",
        "examples/plugins/grill/format.mjs",
        "...choices.filter((_, position) => position !== best).map((label) => ({ label })),",
        "...choices.map((label) => ({ label })),",
    ),
    (
        "the agent cannot tell an accepted recommendation from another choice",
        "examples/plugins/grill/format.mjs",
        "if (picked === 0) return",
        "if (picked === -1) return",
    ),
]

NODE_TESTS = ["extensions/lease.test.ts", "packages/pi-gpui/index.test.ts", "examples/plugins/grill/grill.test.ts"]


def read(path):
    with open(path, encoding="utf-8", newline="") as file:
        return file.read()


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as file:
        file.write(text)


def move_running_exe_aside():
    """cargo cannot replace a running exe, but Windows lets it be renamed: so a
    Pi GPUI the user has open keeps running (nothing is killed)."""
    exe = os.path.join(ROOT, "target", "release", "pi-gpui.exe")
    folder = os.path.dirname(exe)
    # Old copies that are no longer running can go (a running one refuses).
    for name in os.listdir(folder) if os.path.isdir(folder) else []:
        if name.startswith("pi-gpui.exe.running-old"):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
    if not os.path.exists(exe):
        return
    # A running exe can be renamed but not replaced, and renaming always works,
    # so always move it aside: cargo then writes a fresh one.
    n = 1
    while os.path.exists(f"{exe}.running-old{n}"):
        n += 1
    os.rename(exe, f"{exe}.running-old{n}")


def tests_pass(e2e=False, node=False, smoke_only=None):
    if node:
        command = ["node", "--no-warnings", "--test", *NODE_TESTS]
        try:
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return False, "timed out"
        return result.returncode == 0, result.stdout + result.stderr
    if e2e:
        move_running_exe_aside()
        built = subprocess.run(
            ["cargo", "build", "--release"], cwd=ROOT, capture_output=True, text=True
        )
        if built.returncode != 0:
            return False, "could not compile " + built.stdout + built.stderr
        command = [sys.executable, "-I", os.path.join(ROOT, "script", "windows-smoke.py")]
        if smoke_only:
            command += ["--only", smoke_only]
    else:
        command = ["cargo", "test", "--release"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr


def main():
    e2e = "--e2e" in sys.argv[1:]
    node = "--node" in sys.argv[1:]
    mutations = NODE_MUTATIONS if node else E2E_MUTATIONS if e2e else MUTATIONS
    if "--only" in sys.argv[1:]:
        # --only TEXT: just the mutations whose name contains TEXT
        wanted = sys.argv[sys.argv.index("--only") + 1].lower()
        mutations = [m for m in mutations if wanted in m[0].lower()]
    survived = []
    started_all = time.time()
    for number, (name, file, original, mutated, *smoke_only) in enumerate(mutations, 1):
        started = time.time()
        print(f"[{number}/{len(mutations)}] {name} ...", end=" ", flush=True)
        path = os.path.join(ROOT, file)
        text = read(path)
        crlf = "\r\n" in text
        needle = original.replace("\n", "\r\n") if crlf else original
        if text.count(needle) != 1:
            print(f"SKIP: pattern not found exactly once in {file}")
            survived.append(name + " (pattern missing)")
            continue
        replacement = mutated.replace("\n", "\r\n") if crlf else mutated
        write(path, text.replace(needle, replacement))
        try:
            passed, output = tests_pass(e2e, node, smoke_only[0] if smoke_only else None)
        finally:
            write(path, text)
            if e2e:
                # leave the real binary built from the unmutated source
                move_running_exe_aside()
                subprocess.run(["cargo", "build", "--release"], cwd=ROOT, capture_output=True)
        compiled = "could not compile" not in output
        if passed:
            print(f"SURVIVED ({time.time() - started:.0f} s)  <-- the tests did not notice")
            survived.append(name)
        elif not compiled:
            print(f"BROKEN ({time.time() - started:.0f} s): the mutation does not compile (fix the script)")
            survived.append(name + " (does not compile)")
        else:
            print(f"caught ({time.time() - started:.0f} s)")
    print()
    if survived:
        print(f"{len(survived)} problem(s):")
        for name in survived:
            print("  -", name)
        sys.exit(1)
    print(f"all {len(mutations)} mutations were caught in {time.time() - started_all:.0f} s")


if __name__ == "__main__":
    main()
