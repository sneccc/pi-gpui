"""End-to-end smoke test of the Windows build. Starts the real exe in a
sandbox (own HOME, own project) and checks what a user would notice:

  - pi is found on PATH (no PI_EXECUTABLE) and a session can start
  - the automation protocol answers, and reports errors for bad commands
  - a screenshot really contains the UI (not a blank or black picture)
  - automation does not take the focus, and no console window pops up
  - the project is saved without Windows' `\\\\?\\` prefix
  - closing a session, and quitting the app, leave no pi process behind
  - the caption buttons (minimize, maximize, close) and a caption double click work
  - killing the app without warning (a crash) stops pi too
  - pasting a file copied in Explorer does something (this overwrites the clipboard)
  - a second launch (with a folder, a pi-gpui:// link, or nothing) hands over and exits

    python -I script/windows-smoke.py [--show] [--serial] [--verbose] [--only main,controls,crash,handover,lease,refresh,gpui,held,ask,template,edit,images,grill,subagents] [path\\to\\pi-gpui.exe]

The app runs headless (PI_GPUI_HEADLESS=1: its windows sit on a hidden
desktop, nothing pops up); --show puts them on your screen to watch.
The check groups run side by side (each has its own HOME and app); --serial runs
them one after another. --only runs just the named groups. Each group prints one
line when it is done (its time, passed and failed checks); --verbose lists every check.

Needs pi on PATH. Sends no prompt, so it costs no tokens. Exit code 0 = all
checks passed. See docs/WINDOWS_UI_CHECKS.md.
"""
import ctypes
import ctypes.wintypes as wt
import json
import re
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONLY = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv[1:] else None
SERIAL = "--serial" in sys.argv[1:]
VERBOSE = "--verbose" in sys.argv[1:]
ARGS = [arg for index, arg in enumerate(sys.argv[1:], 1)
        if arg not in ("--show", "--only", "--serial", "--verbose") and sys.argv[index - 1] != "--only"]
HEADLESS = "--show" not in sys.argv[1:]
EXE = ARGS[0] if ARGS else os.path.join(ROOT, "target", "release", "pi-gpui.exe")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def join_headless_desktop():
    """Moves this thread to the app's hidden desktop (src/platform.rs), so the
    window checks below (EnumWindows, messages, focus) see the app's windows."""
    user32.CreateDesktopW.restype = wt.HANDLE
    user32.CreateDesktopW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p]
    desktop = user32.CreateDesktopW("pi-gpui-headless", None, None, 0, 0x10000000, None)  # GENERIC_ALL
    if not desktop or not user32.SetThreadDesktop(desktop):
        sys.exit(f"could not join the hidden desktop (error {ctypes.get_last_error()}); try --show")
    os.environ["PI_GPUI_HEADLESS"] = "1"

failures = []
# Failure details can hold pi's screen (box drawing, spinners): never let
# printing them crash on a pipe with Windows' default code page.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# Checks run side by side, one thread each: a thread collects its own lines,
# printed together when it is done.
_output = threading.local()


def say(line):
    lines = getattr(_output, "lines", None)
    if lines is None:
        print(line, flush=True)
    else:
        lines.append(line)


def check(name, ok, detail=""):
    say(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(name)
    return ok


CREDENTIAL = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|^(AWS|AZURE|GOOGLE_APPLICATION)_", re.I)


def offline_environ():
    """os.environ without credentials (API keys, tokens). The pi processes the
    tests start must never reach a real model: if the fake model fails to
    answer, pi would fall back to any provider whose key it finds, and spend
    money. Without keys it fails loudly instead."""
    return {name: value for name, value in os.environ.items() if not CREDENTIAL.search(name)}


def sandbox_env(home, auto):
    """The environment for an app under test: its own HOME and automation
    folder, no credentials, and an empty pi agent folder, so its pi loads none
    of the user's extensions or settings (and writes nothing into theirs)."""
    return dict(offline_environ(), HOME=home, PI_GPUI_AUTOMATION=auto,
                PI_CODING_AGENT_DIR=os.path.join(home, "pi-agent"))


def wait_for(predicate, seconds, interval=0.2):
    end = time.time() + seconds
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


# ---------------------------------------------------------------- processes
class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("size", wt.DWORD), ("usage", wt.DWORD), ("pid", wt.DWORD),
        ("heap", ctypes.c_size_t), ("module", wt.DWORD), ("threads", wt.DWORD),
        ("parent", wt.DWORD), ("priority", wt.LONG), ("flags", wt.DWORD),
        ("exe", wt.WCHAR * 260),
    ]


def process_table():
    """{pid: (parent pid, exe name)} of every process."""
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    table = {}
    entry = ProcessEntry()
    entry.size = ctypes.sizeof(ProcessEntry)
    more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
    while more:
        table[entry.pid] = (entry.parent, entry.exe)
        more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    kernel32.CloseHandle(snapshot)
    return table


kernel32.OpenProcess.restype = wt.HANDLE


def start_time(pid):
    """When the process started (100 ns ticks), or None if it cannot be opened."""
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    times = (ctypes.c_ulonglong * 4)()
    ok = kernel32.GetProcessTimes(handle, *(ctypes.byref(times, 8 * i) for i in range(4)))
    kernel32.CloseHandle(handle)
    return times[0] if ok else None


ntdll = ctypes.WinDLL("ntdll")
ntdll.NtQueryInformationProcess.restype = ctypes.c_long
ntdll.NtQueryInformationProcess.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.ULONG,
                                            ctypes.POINTER(wt.ULONG)]


class UnicodeString(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ushort), ("maximum", ctypes.c_ushort), ("buffer", ctypes.c_void_p)]


def command_line(pid):
    """The process's command line (ProcessCommandLineInformation), or None."""
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        size = wt.ULONG(0)
        buffer = ctypes.create_string_buffer(8192)
        if ntdll.NtQueryInformationProcess(handle, 60, buffer, len(buffer), ctypes.byref(size)) != 0:
            return None
        text = UnicodeString.from_buffer(buffer)
        return ctypes.wstring_at(text.buffer, text.length // 2)
    finally:
        kernel32.CloseHandle(handle)


def session_pis(app_pid):
    """{pid: exe} of the pi processes running sessions under the app: node.exe
    with `--mode rpc`. Not the short `pi --version` the app runs at start-up."""
    return {pid: exe for pid, exe in descendants(app_pid).items()
            if exe.lower() == "node.exe" and "--mode rpc" in (command_line(pid) or "")}


def descendants(pid):
    """{pid: exe name} of the processes `pid` started, and theirs. A parent pid
    can be stale (that parent died and its pid went to a new process), so a
    child only counts if it started after its parent."""
    table = process_table()
    found, queue = {}, [pid]
    while queue:
        parent = queue.pop()
        born = start_time(parent)
        for child, (parent_of_child, exe) in table.items():
            if parent_of_child != parent or child in found or born is None:
                continue
            child_born = start_time(child)
            if child_born is not None and child_born >= born:
                found[child] = exe
                queue.append(child)
    return found


def alive(pid):
    return pid in process_table()


# ------------------------------------------------------------------ windows
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


user32.OpenDesktopW.restype = wt.HANDLE
user32.OpenDesktopW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.BOOL, wt.DWORD]
user32.EnumDesktopWindows.argtypes = [wt.HANDLE, WNDENUMPROC, wt.LPARAM]


def visible_windows(pids):
    """[(class name, title)] of visible top-level windows owned by `pids`, on
    this thread's desktop and on the user's normal one: the app's own window
    is on the hidden desktop, but processes it starts open theirs (a console,
    say) on the normal desktop."""
    found, seen = [], set()

    def visit(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd) and hwnd not in seen:
            seen.add(hwnd)
            cls, title = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            user32.GetWindowTextW(hwnd, title, 256)
            found.append((cls.value, title.value))
        return True

    callback = WNDENUMPROC(visit)
    user32.EnumWindows(callback, 0)
    normal = user32.OpenDesktopW("Default", 0, False, 0x0041)  # DESKTOP_READOBJECTS | DESKTOP_ENUMERATE
    if normal:
        user32.EnumDesktopWindows(normal, callback, 0)
        user32.CloseDesktop(normal)
    return found


def foreground_pid():
    owner = wt.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(owner))
    return owner.value


def app_window(pid):
    """HWND of the app's main window (class Zed::Window), or None."""
    found = []

    def visit(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if owner.value == pid and cls.value == "Zed::Window" and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(visit), 0)
    return found[0] if found else None


HIT = {1: "client", 2: "caption", 8: "minimize", 9: "maximize", 20: "close"}
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetClassLongPtrW.restype = ctypes.c_size_t
user32.GetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int]


def window_icon(hwnd):
    """The icon the taskbar shows: the window's own (WM_GETICON) or its class's."""
    for kind in (1, 0, 2):  # ICON_BIG, ICON_SMALL, ICON_SMALL2
        icon = user32.SendMessageW(hwnd, 0x007F, kind, 0)  # WM_GETICON
        if icon:
            return icon
    return user32.GetClassLongPtrW(hwnd, -14) or user32.GetClassLongPtrW(hwnd, -34)  # GCLP_HICON(SM)


def client_width(hwnd):
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    return rect.right


def screen_position(hwnd, x, y):
    point = wt.POINT(x, y)
    user32.ClientToScreen(hwnd, ctypes.byref(point))
    return (point.y & 0xFFFF) << 16 | (point.x & 0xFFFF)


def hit_test(hwnd, x, y=18):
    """What Windows would make of the point (x, y) of the client area."""
    # gpui follows the pointer itself: move it there first, as real input does.
    # SendMessage returns once the app has handled the move: no sleep needed.
    user32.SendMessageW(hwnd, 0x0200, 0, (y << 16) | x)  # WM_MOUSEMOVE
    return user32.SendMessageW(hwnd, 0x0084, 0, screen_position(hwnd, x, y))  # WM_NCHITTEST


_last_click = threading.local()


def caption_click(hwnd, x, y=18, double=False):
    """The messages a real left click (or double click) on the title bar sends.
    Clicks closer together than Windows' double-click time would count as one
    multi-click, so it waits that long after this thread's previous click."""
    gap = user32.GetDoubleClickTime() / 1000 + 0.05
    elapsed = time.time() - getattr(_last_click, "at", 0)
    if elapsed < gap:
        time.sleep(gap - elapsed)
    hit = hit_test(hwnd, x, y)
    where = screen_position(hwnd, x, y)
    messages = [0x00A0, 0x00A1, 0x00A2]  # WM_NCMOUSEMOVE, WM_NCLBUTTONDOWN, WM_NCLBUTTONUP
    if double:
        messages += [0x00A3, 0x00A2]  # WM_NCLBUTTONDBLCLK, WM_NCLBUTTONUP
    for message in messages:
        user32.PostMessageW(hwnd, message, hit, where)
        time.sleep(0.05)
    _last_click.at = time.time()
    # The caller waits for the effect (maximized, minimized, closed).
    return hit


def window_controls_check(sandbox):
    """A second launch, without a project: the caption buttons."""
    home, auto = os.path.join(sandbox, "home2"), os.path.join(sandbox, "auto2")
    os.makedirs(home)
    os.makedirs(auto)
    env = sandbox_env(home, auto)
    env.pop("PI_EXECUTABLE", None)
    app = subprocess.Popen([EXE], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        hwnd = wait_for(lambda: app_window(app.pid), 20)
        check("controls: the window opens without a project", bool(hwnd))
        if not hwnd:
            return
        Automation(auto).send("wait 1")  # answers once the app's event loop runs
        width = client_width(hwnd)
        # Walk the title bar from the right: close, maximize, minimize, then free caption space.
        seen = []
        for x in range(width - 4, 0, -6):
            area = HIT.get(hit_test(hwnd, x), "other")
            if not seen or seen[-1] != area:
                seen.append(area)
        check("controls: title bar has close, maximize, minimize and caption regions in order",
              [a for a in seen if a != "client"][:4] == ["close", "maximize", "minimize", "caption"], seen)

        zoomed = lambda: bool(user32.IsZoomed(hwnd))
        # The buttons sit 125, 79 and 33 points from the right edge (46 wide each);
        # the width changes when the window is maximized, so ask again each time.
        restored = lambda: not user32.IsZoomed(hwnd) and not user32.IsIconic(hwnd)
        check("controls: maximize button maximizes",
              caption_click(hwnd, client_width(hwnd) - 79) == 9 and bool(wait_for(zoomed, 5, 0.05)))
        check("controls: the same button restores",
              caption_click(hwnd, client_width(hwnd) - 79) == 9 and bool(wait_for(restored, 5, 0.05)))
        caption_click(hwnd, 600, double=True)
        check("controls: double click on the caption maximizes", bool(wait_for(zoomed, 5, 0.05)))
        caption_click(hwnd, 600, double=True)
        check("controls: double click again restores", bool(wait_for(restored, 5, 0.05)))
        check("controls: minimize button minimizes",
              caption_click(hwnd, client_width(hwnd) - 125) == 8
              and bool(wait_for(lambda: user32.IsIconic(hwnd), 5, 0.05)))
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        wait_for(restored, 5, 0.05)
        check("controls: close button closes the app",
              caption_click(hwnd, client_width(hwnd) - 33) == 20
              and bool(wait_for(lambda: app.poll() is not None, 10)))
    finally:
        if app.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)


def crash_check(sandbox):
    """A third launch: the app is killed without warning (as a crash would)
    while its pi is busy, and pi must not be left running. An idle pi exits by
    itself when its input closes; a busy one finishes its run first (here: a
    minute), so only the job object stops it in time."""
    home, auto = os.path.join(sandbox, "home3"), os.path.join(sandbox, "auto3")
    project = os.path.join(sandbox, "crash project")
    for folder in (home, auto, project):
        os.makedirs(folder)
    agent = fake_pi_agent(sandbox, "agent3")
    env = dict(offline_environ(), HOME=home, PI_GPUI_AUTOMATION=auto, PI_CODING_AGENT_DIR=agent,
               PI_SLOW_MODEL_MS="60000")
    env.pop("PI_EXECUTABLE", None)
    app = subprocess.Popen([EXE, project], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    driver = Automation(auto)
    pis = {}
    try:
        pis = wait_for(lambda: session_pis(app.pid) and descendants(app.pid), 25) or {}
        check("crash: the app has a session running pi", bool(pis), pis)
        driver.settle(sandbox)
        driver.send("type crash test", "key enter")
        asked = lambda: any("crash test" in open(os.path.join(folder, name), encoding="utf-8").read()
                            for folder, _, names in os.walk(os.path.join(agent, "sessions"))
                            for name in names if name.endswith(".jsonl"))
        check("crash: pi is busy with a prompt (the slow model takes a minute)", bool(wait_for(asked, 15)))
        # Only the app: no /T, so nothing but the job object can stop pi.
        subprocess.run(["taskkill", "/F", "/PID", str(app.pid)], capture_output=True)
        check("crash: the app is gone", bool(wait_for(lambda: app.poll() is not None, 10)))
        check("crash: killing the app also stops pi (no orphans)",
              wait_for(lambda: not any(alive(pid) for pid in pis), 15),
              [pid for pid in pis if alive(pid)])
    finally:
        if app.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)
        for pid in pis:
            if alive(pid):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)


def fake_pi_agent(sandbox, name, model="slow-model.ts"):
    """A pi agent folder whose default model is a free fake model from
    script/debug: slow-model.ts (delay from PI_SLOW_MODEL_MS) or
    scripted-model.ts (steps from PI_SCRIPTED_MODEL)."""
    agent = os.path.join(sandbox, name)
    os.makedirs(agent)
    with open(os.path.join(agent, "settings.json"), "w", encoding="utf-8") as file:
        json.dump({"extensions": [os.path.join(ROOT, "script", "debug", model)],
                   "defaultProvider": "faux", "defaultModel": "faux-1", "defaultThinkingLevel": "off"}, file)
    return agent


def ask_check(sandbox):
    """The Ask example plugin, end to end: pi calls its tool, the plugin shows
    a form in the chat, keys answer it, and pi gets the answers back."""
    home, auto, project = (os.path.join(sandbox, name) for name in ("home9", "auto9", "ask project"))
    for folder in (home, auto, project):
        os.makedirs(folder)
    shutil.copytree(os.path.join(ROOT, "examples", "plugins", "ask"),
                    os.path.join(home, ".config", "pi-gpui", "plugins", "ask"))
    agent = fake_pi_agent(sandbox, "agent9", "scripted-model.ts")
    steps = os.path.join(sandbox, "ask-steps.json")
    with open(steps, "w", encoding="utf-8") as file:
        json.dump([{"tool": "ask_user_question", "args": {"questions": [
            {"question": "Which colour?", "options": [{"label": "Red"}, {"label": "Blue"}]},
            {"question": "Any comment?"}]}},
            {"echo": "GOT: "}], file)
    env = dict(offline_environ(), HOME=home, PI_GPUI_AUTOMATION=auto, PI_CODING_AGENT_DIR=agent, PI_SCRIPTED_MODEL=steps)
    env.pop("PI_EXECUTABLE", None)
    log_path = os.path.join(sandbox, "ask-app.log")
    log = open(log_path, "w")
    app = subprocess.Popen([EXE, project], env=env, stdout=log, stderr=subprocess.STDOUT)
    driver = Automation(auto)

    def transcript():
        return "".join(open(os.path.join(folder, name), encoding="utf-8").read()
                       for folder, _, names in os.walk(os.path.join(agent, "sessions"))
                       for name in names if name.endswith(".jsonl"))

    try:
        check("ask: the app opens its window", bool(wait_for(lambda: app_window(app.pid), 20)))
        check("ask: the plugin is loaded", bool(wait_for(
            lambda: "loaded 1 plugin(s): ask" in open(log_path, errors="replace").read(), 15)))
        driver.settle(sandbox)
        baseline = chat_accent(driver, sandbox)
        driver.send("type please ask me", "key enter")
        check("ask: pi calls the plugin's tool", bool(wait_for(lambda: "ask_user_question" in transcript(), 15)))
        check("ask: the plugin's form appears in the chat", wait_for_form(driver, sandbox, baseline))
        # As a user would: 2 picks the second option, Enter goes on, a typed
        # answer for the free question, Enter sends.
        answer = driver.send("key 2", "wait 200", "key enter", "wait 300", "type looks good", "key enter")
        check("ask: the keys reach the form", answer is not None and all(l.endswith(": ok") for l in answer), answer)
        got = wait_for(lambda: "GOT: The user answered" in transcript(), 15)
        text = transcript()
        check("ask: pi gets the answers from the form",
              bool(got) and "Blue" in text and "looks good" in text, text[-400:])
        check("ask: only the fake model answered", only_fake_model(text), models_used(text))
    finally:
        if app.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)
        log.close()


def models_used(transcript):
    """{provider/model} of the assistant messages in session JSON lines."""
    used = set()
    for line in transcript.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") or {}
        if entry.get("type") == "message" and message.get("role") == "assistant":
            used.add(f"{message.get('provider')}/{message.get('model')}")
    return used


def only_fake_model(transcript):
    return models_used(transcript) <= {"faux/faux-1"}


def accent_pixels(path, left=300, top=80, right=1150, bottom=780):
    """Orange (the theme's accent) pixels in the chat column: forms, chips and
    focused fields use it, the plain transcript hardly at all."""
    _, _, bpp, rows = decode_png(path)
    count = 0
    for row in rows[top:bottom]:
        for x in range(left, min(right, len(row) // bpp)):
            r, g, b = row[x * bpp], row[x * bpp + 1], row[x * bpp + 2]
            if r > 90 and r > g > b and r - b > 40:  # warm: the accent and its dim borders
                count += 1
    return count


def chat_accent(driver, folder):
    """accent_pixels of the window right now."""
    path = os.path.join(folder, f"accent-{id(driver)}-{threading.get_ident()}.png")
    driver.send(f"shot {path}")
    return accent_pixels(path) if os.path.isfile(path) else 0


def wait_for_form(driver, folder, baseline, more=500, timeout=20):
    """Waits until a plugin's form (or field) is drawn in the chat: it adds
    accent-coloured borders and buttons, at least `more` pixels over
    `baseline` (chat_accent before the prompt). A spinner keeps the window
    changing, so `settle` cannot tell this."""
    return bool(wait_for(lambda: chat_accent(driver, folder) >= baseline + more, timeout, 0.1))


def chip_pixels(path):
    """Orange (accent) pixels on the message field's text line: an attachment
    chip has many, plain pasted text none (only the caret, a few)."""
    _, _, bpp, rows = decode_png(path)
    count = 0
    for row in rows[712:748]:
        for x in range(300, 1050):
            r, g, b = row[x * bpp], row[x * bpp + 1], row[x * bpp + 2]
            if r > 150 and g > 90 and b < 110 and r - b > 70:
                count += 1
    return count


def template_check(sandbox):
    """The plugin template of the project skill (.pi/skills/pi-gpui-plugins/
    assets/template) still works: it loads, its shortcut inserts text, and a
    pi call of its tool shows a field whose answer reaches pi."""
    home, auto, project = (os.path.join(sandbox, name) for name in ("home10", "auto10", "template project"))
    for folder in (home, auto, project):
        os.makedirs(folder)
    template = os.path.join(ROOT, ".pi", "skills", "pi-gpui-plugins", "assets", "template")
    manifest = json.load(open(os.path.join(template, "plugin.json"), encoding="utf-8"))
    shutil.copytree(template, os.path.join(home, ".config", "pi-gpui", "plugins", manifest["id"]))
    tool = manifest["contributes"]["toolEvents"][0]
    agent = fake_pi_agent(sandbox, "agent10", "scripted-model.ts")
    steps = os.path.join(sandbox, "template-steps.json")
    json.dump([{"tool": tool, "args": {"question": "What should I call you?"}}, {"echo": "GOT: "}],
              open(steps, "w", encoding="utf-8"))
    env = dict(offline_environ(), HOME=home, PI_GPUI_AUTOMATION=auto, PI_CODING_AGENT_DIR=agent, PI_SCRIPTED_MODEL=steps)
    env.pop("PI_EXECUTABLE", None)
    log_path = os.path.join(sandbox, "template-app.log")
    log = open(log_path, "w")
    app = subprocess.Popen([EXE, project], env=env, stdout=log, stderr=subprocess.STDOUT)
    driver = Automation(auto)

    def transcript():
        return "".join(open(os.path.join(folder, name), encoding="utf-8").read()
                       for folder, _, names in os.walk(os.path.join(agent, "sessions"))
                       for name in names if name.endswith(".jsonl"))

    try:
        check("template: the app opens its window", bool(wait_for(lambda: app_window(app.pid), 20)))
        check("template: the plugin loads", bool(wait_for(
            lambda: f"plugin(s): {manifest['id']}" in open(log_path, errors="replace").read(), 15)))
        driver.settle(sandbox)
        empty = chat_accent(driver, sandbox)
        # The manifest's shortcut (cmd- is ctrl- on Windows) inserts text: the
        # message field gets more text pixels. Checked through the transcript
        # below instead of pixels: send the inserted text as the prompt.
        keys = manifest["contributes"]["commands"][0]["keybinding"].replace("cmd-", "ctrl-")
        driver.send(f"key {keys}", "wait 500", "key enter")
        check("template: the shortcut inserts its text (sent as the prompt)",
              bool(wait_for(lambda: "Hello from a plugin" in transcript(), 15)), transcript()[-300:])
        check("template: pi calls the template's tool", bool(wait_for(lambda: tool in transcript(), 15)))
        check("template: its field appears in the tool call", wait_for_form(driver, sandbox, empty, more=150))
        driver.send("type Daniel", "key enter")
        check("template: the answer reaches pi",
              bool(wait_for(lambda: "GOT: The user's name is Daniel." in transcript(), 15)), transcript()[-300:])
        check("template: only the fake model answered", only_fake_model(transcript()), models_used(transcript()))
    finally:
        if app.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)
        log.close()


def edit_check(sandbox):
    """Up in the empty message box brings back sent messages; the pencil on a
    message edits it in the same session as a new branch."""
    home, auto, project = (os.path.join(sandbox, name) for name in ("home11", "auto11", "edit project"))
    for folder in (home, auto, project):
        os.makedirs(folder)
    agent = fake_pi_agent(sandbox, "agent11", "scripted-model.ts")
    steps = os.path.join(sandbox, "edit-steps.json")
    json.dump([{"text": "OK"}], open(steps, "w"))
    env = dict(offline_environ(), HOME=home, PI_GPUI_AUTOMATION=auto, PI_CODING_AGENT_DIR=agent,
               PI_SCRIPTED_MODEL=steps)
    app = subprocess.Popen([EXE, project], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    driver = Automation(auto)

    def entries():
        """[(id, parent, role, text)] of the session's messages."""
        found = []
        for folder, _, names in os.walk(os.path.join(agent, "sessions")):
            for name in names:
                if not name.endswith(".jsonl"):
                    continue
                for line in open(os.path.join(folder, name), encoding="utf-8"):
                    entry = json.loads(line)
                    message = entry.get("message") or {}
                    if entry.get("type") == "message" and message.get("role") in ("user", "assistant"):
                        content = message["content"]
                        text = content if isinstance(content, str) else "".join(
                            part.get("text", "") for part in content if part.get("type") == "text")
                        found.append((entry["id"], entry.get("parentId"), message["role"], text))
        return found

    def users():
        return [entry for entry in entries() if entry[2] == "user"]

    def answered(count):
        return wait_for(lambda: sum(entry[2] == "assistant" for entry in entries()) >= count, 15, 0.1)

    try:
        check("edit: the app opens its window", bool(wait_for(lambda: app_window(app.pid), 20)))
        driver.settle(sandbox)
        driver.send("type first message", "key enter")
        answered(1)
        driver.send("type second message", "key enter")
        answered(2)
        # Up twice in the empty box: the first message again; Enter sends it.
        driver.send("key up", "key up", "key enter")
        check("edit: Up in the empty box brings back earlier messages",
              bool(answered(3)) and [entry[3] for entry in users()][-1:] == ["first message"],
              [entry[3] for entry in users()])
        driver.send("type draft", "key up", "key ctrl-a", "key backspace")
        check("edit: Up in typed text does not replace it", len(users()) == 3)

        # The pencil sits in the left gutter of a user message; with three
        # turns the second message's row is at y 538 (rows are bottom-aligned).
        before = {entry[0] for entry in users()}
        original = next(entry for entry in users() if entry[3] == "second message")
        driver.settle(sandbox, quiet=0.5)
        driver.send("move 500 538", "wait 300", "move 298 546", "wait 200", "click 298 546", "wait 1500")
        driver.send("key ctrl-a", "type edited message", "key enter")
        answered(4)
        edited = [entry for entry in users() if entry[0] not in before]
        check("edit: the pencil puts the message back and sending makes a new message",
              [entry[3] for entry in edited] == ["edited message"], [entry[3] for entry in users()])
        check("edit: ... branching where the original was (same parent, same session)",
              bool(edited) and edited[0][1] == original[1], (edited, original))
        check("edit: the original branch is kept", original[0] in {entry[0] for entry in users()})
        files = [name for _, _, names in os.walk(os.path.join(agent, "sessions")) for name in names
                 if name.endswith(".jsonl")]
        check("edit: still one session (an edit is not a fork)", len(files) == 1, files)
    finally:
        if app.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)


def images_check(sandbox):
    """A picture pi reads (its `read` tool on an image) shows in the chat
    right away, without opening the tool row."""
    home, auto, project = (os.path.join(sandbox, name) for name in ("home12", "auto12", "images project"))
    for folder in (home, auto, project):
        os.makedirs(folder)
    # A picture of one distinctive colour, to find on screen: a 600x300 PNG.
    color = (43, 108, 176)
    width, height = 600, 300
    row = b"\x00" + bytes(color) * width
    raw = zlib.compress(row * height)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    with open(os.path.join(project, "chart.png"), "wb") as file:
        file.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                   + chunk(b"IDAT", raw) + chunk(b"IEND", b""))
    agent = fake_pi_agent(sandbox, "agent12", "scripted-model.ts")
    steps = os.path.join(sandbox, "images-steps.json")
    json.dump([{"tool": "read", "args": {"path": "chart.png"}}, {"text": "Seen."}], open(steps, "w"))
    env = dict(offline_environ(), HOME=home, PI_GPUI_AUTOMATION=auto, PI_CODING_AGENT_DIR=agent,
               PI_SCRIPTED_MODEL=steps)
    app = subprocess.Popen([EXE, project], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    driver = Automation(auto)
    shot = os.path.join(sandbox, "images.png")

    def picture_pixels():
        driver.send("move 700 300", f"shot {shot}")
        if not os.path.isfile(shot):
            return 0
        _, _, bpp, rows = decode_png(shot)
        return sum(1 for line in rows[80:780] for x in range(300, 1150, 2)
                   if abs(line[x * bpp] - color[0]) < 8 and abs(line[x * bpp + 1] - color[1]) < 8
                   and abs(line[x * bpp + 2] - color[2]) < 8)

    try:
        check("images: the app opens its window", bool(wait_for(lambda: app_window(app.pid), 20)))
        driver.settle(sandbox)
        check("images: nothing blue before", picture_pixels() < 50)
        driver.send("type look at the chart", "key enter")
        # A 600x300 picture shown at most 320 wide: thousands of its pixels.
        shown = wait_for(lambda: picture_pixels() > 5000, 15, 0.2)
        check("images: the picture pi read shows in the chat without opening the row", bool(shown),
              picture_pixels())
    finally:
        if app.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)


def grill_check(sandbox):
    """The Grill plugin: a grilling round as a form; accepting the
    recommendation, picking another choice and answering in your own words
    all reach the agent as decisions."""
    home, auto, project = (os.path.join(sandbox, name) for name in ("home13", "auto13", "grill project"))
    for folder in (home, auto, project):
        os.makedirs(folder)
    shutil.copytree(os.path.join(ROOT, "examples", "plugins", "grill"),
                    os.path.join(home, ".config", "pi-gpui", "plugins", "grill"))
    agent = fake_pi_agent(sandbox, "agent13", "scripted-model.ts")
    steps = os.path.join(sandbox, "grill-steps.json")
    json.dump([{"tool": "grill_round", "args": {"round": 1, "progress": "0 settled, 3 open", "questions": [
        {"title": "Ship on Friday?", "recommended": "Yes, with billing behind a flag"},
        {"title": "Which database?", "choices": ["SQLite", "PostgreSQL", "DuckDB"],
         "recommended": "PostgreSQL", "recommendedChoice": 1},
        {"title": "Who owns on-call?", "recommended": "The platform team"}]}},
        {"echo": "GOT: "}], open(steps, "w"))
    env = dict(offline_environ(), HOME=home, PI_GPUI_AUTOMATION=auto, PI_CODING_AGENT_DIR=agent,
               PI_SCRIPTED_MODEL=steps)
    app = subprocess.Popen([EXE, project], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    driver = Automation(auto)

    def transcript():
        return "".join(open(os.path.join(folder, name), encoding="utf-8").read()
                       for folder, _, names in os.walk(os.path.join(agent, "sessions"))
                       for name in names if name.endswith(".jsonl"))

    try:
        check("grill: the app opens its window", bool(wait_for(lambda: app_window(app.pid), 20)))
        driver.settle(sandbox)
        baseline = chat_accent(driver, sandbox)
        driver.send("type grill me", "key enter")
        check("grill: pi calls grill_round", bool(wait_for(lambda: "grill_round" in transcript(), 15)))
        check("grill: the round appears as a form", wait_for_form(driver, sandbox, baseline))
        # Q1 is left alone: its recommendation is picked from the start. Right
        # goes to Q2 without answering anything; 3 = its third option (the
        # recommended one comes first). Right to Q3, Down to the text field
        # (Space on an option would pick it), own words, Enter sends.
        driver.send("key right", "wait 200", "key 3", "key right", "wait 200",
                    "key down", "type Maya does", "key enter")
        got = wait_for(lambda: "GOT: Round 1: the user answered." in transcript(), 15)
        text = json.dumps(transcript())
        check("grill: the agent learns the accepted recommendation",
              bool(got) and "accepted your recommendation (Yes, with billing behind a flag)" in text, text[-500:])
        check("grill: ... the other choice", "chose: DuckDB" in text)
        check("grill: ... and the answer in the user's own words", "answered in their own words: Maya does" in text)
        check("grill: only the fake model answered", only_fake_model(transcript()), models_used(transcript()))
    finally:
        if app.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)


DURABLE = os.environ.get("PI_DURABLE_SUBAGENTS") or os.path.join(os.path.expanduser("~"), "source", "pi-durable-subagents")


def subagents_check(sandbox):
    """The Subagents panel with the real pi-durable-subagents extension: pi
    delegates to a subagent, the panel lists it, a click shows its
    conversation, and Back returns to the session. Needs a checkout of the
    extension with the Pi GPUI publisher (src/gpui.ts) at PI_DURABLE_SUBAGENTS
    (default ~/source/pi-durable-subagents); skipped without one."""
    if not os.path.isfile(os.path.join(DURABLE, "src", "gpui.ts")):
        say(f"skip  subagents: no pi-durable-subagents with src/gpui.ts at {DURABLE}")
        return
    home, auto, project = (os.path.join(sandbox, name) for name in ("home14", "auto14", "subagents project"))
    for folder in (home, auto, project):
        os.makedirs(folder)
    agent = fake_pi_agent(sandbox, "agent14", "scripted-model.ts")
    settings_path = os.path.join(agent, "settings.json")
    settings = json.load(open(settings_path))
    settings["extensions"].append(DURABLE)
    json.dump(settings, open(settings_path, "w"))
    steps = os.path.join(sandbox, "subagents-steps.json")
    json.dump([
        {"tool": "Agent", "args": {"prompt": "Find the auth code.", "description": "find auth",
                                  "subagent_type": "Explore", "name": "auth-scout", "run_in_background": False}},
        {"text": "Found **src/auth.rs**.", "delayMs": 1500},
        {"text": "Done."}], open(steps, "w"))
    env = dict(sandbox_env(home, auto), PI_CODING_AGENT_DIR=agent, PI_SCRIPTED_MODEL=steps)
    app = subprocess.Popen([EXE, project], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    driver = Automation(auto)
    published = os.path.join(home, ".cache", "pi-gpui", "subagents")

    def fleet():
        for folder, _, names in os.walk(published):
            if "fleet.json" in names:
                try:
                    return json.load(open(os.path.join(folder, "fleet.json"), encoding="utf-8"))
                except (OSError, ValueError):
                    return None
        return None

    def main_area(name):
        path = os.path.join(sandbox, name + ".png")
        driver.send("move 640 400", "wait 200", f"shot {path}")
        if not os.path.isfile(path):
            return None
        _, _, bpp, rows = decode_png(path)
        return [bytes(row[300 * bpp:780 * bpp]) for row in rows[80:700]]

    try:
        check("subagents: the app opens its window", bool(wait_for(lambda: app_window(app.pid), 20)))
        driver.settle(sandbox)
        driver.send("key ctrl-alt-a", "wait 300", "type find the auth code", "key enter")
        done = wait_for(lambda: any(a.get("status") == "completed" for a in (fleet() or {}).get("agents", [])), 30)
        check("subagents: the extension publishes the subagent for the app", bool(done), fleet())
        names = [a.get("name") for a in (fleet() or {}).get("agents", [])]
        check("subagents: ... named and done", names == ["auth-scout"], names)
        driver.settle(sandbox, quiet=0.6)
        chat = main_area("subagents-chat")
        # The panel is the right dock: Main is the first row, the subagent the second.
        driver.send("move 1000 150", "wait 200", "click 1000 150", "wait 800")
        shown = main_area("subagents-shown")
        check("subagents: clicking it shows its conversation instead of the chat", chat and shown and shown != chat)
        driver.send("move 1000 96", "wait 200", "click 1000 96", "wait 800")
        driver.settle(sandbox, quiet=0.5)
        back = main_area("subagents-back")
        check("subagents: Main returns to the session", back == chat)

        # Right-click the session in the sidebar: Copy session trace (the 4th
        # menu item). It overwrites the clipboard: keep the user's text.
        saved = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                               capture_output=True, text=True).stdout
        traces = os.path.join(home, ".cache", "pi-gpui", "traces")
        try:
            driver.send("move 150 121", "rclick 150 121", "wait 400", "move 230 225", "click 230 225")
            written = wait_for(lambda: os.path.isdir(traces) and os.listdir(traces), 10)
            trace = open(os.path.join(traces, written[0]), encoding="utf-8").read() if written else ""
            check("subagents: right-click > Copy session trace writes the trace", bool(written))
            check("subagents: ... with the session file, the fleet and the subagent's transcript",
                  all(part in trace for part in ("## Session file (JSONL)", "### Fleet", "auth-scout",
                                                 "Found **src/auth.rs**.", "- subagents published: 1")),
                  trace[:600])
        finally:
            subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $input"],
                           input=saved, text=True, capture_output=True)
    finally:
        if app.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)


def handover_check(sandbox):
    """A fourth launch, with a HOME of its own: later launches hand their
    folder or pi-gpui:// link to the running app and exit."""
    home, auto = os.path.join(sandbox, "home4"), os.path.join(sandbox, "auto4")
    second_project = os.path.join(sandbox, "second project")
    third_project = os.path.join(sandbox, "third project")
    for folder in (home, auto, second_project, third_project):
        os.makedirs(folder)
    env = sandbox_env(home, auto)
    env.pop("PI_EXECUTABLE", None)
    app = subprocess.Popen([EXE], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    strays = []

    def launch(*args):
        """Runs a later launch; returns its exit code, or None if it kept running."""
        later = subprocess.Popen([EXE, *args], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            return later.wait(timeout=15)
        except subprocess.TimeoutExpired:
            strays.append(later)
            return None

    def projects():
        try:
            state = json.load(open(os.path.join(home, ".config", "pi-gpui", "state.json")))
        except (OSError, ValueError):
            return []
        return [os.path.normcase(os.path.realpath(w["path"])) for w in state["workspaces"]]

    try:
        check("handover: the first app opens its window", bool(wait_for(lambda: app_window(app.pid), 20)))
        started = time.time()
        code = launch(second_project)
        check("handover: a second launch with a folder exits by itself", code == 0, code)
        check("handover: ... quickly (it handed over, it did not start an app)", time.time() - started < 8,
              f"{time.time() - started:.1f} s")
        link = "pi-gpui://open?workspace=" + third_project.replace(os.sep, "/").replace(" ", "%20").replace(":", "%3A")
        code = launch(link)
        check("handover: a second launch with a pi-gpui:// link exits by itself", code == 0, code)
        code = launch()
        check("handover: a second launch with no argument exits by itself", code == 0, code)
        for label, folder in (("the folder argument", second_project), ("the pi-gpui:// link", third_project)):
            wanted = os.path.normcase(os.path.realpath(folder))
            check(f"handover: the running app opened {label}", bool(wait_for(lambda: wanted in projects(), 10)),
                  projects())
        check("handover: the first app is still running", app.poll() is None)
    finally:
        for process in (app, *strays):
            if process.poll() is None:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)


def node_lease(script, *args, wait=False):
    """Runs `script` in node with the pi-gpui package's lease code imported as `m`."""
    url = "file:///" + os.path.join(ROOT, "packages", "pi-gpui", "index.ts").replace(os.sep, "/")
    code = f"import({json.dumps(url)}).then(async m => {{ const a = process.argv.slice(1); {script} }})"
    command = ["node", "--no-warnings", "-e", code, *args]
    if wait:
        return subprocess.run(command, capture_output=True, text=True, timeout=30).stdout.strip()
    return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def lease_check(sandbox):
    """A fifth launch: a session held by a terminal pi is not opened twice,
    and the app's own pi holds the session it opens."""
    home, auto = os.path.join(sandbox, "home5"), os.path.join(sandbox, "auto5")
    project = os.path.join(sandbox, "lease project")
    for folder in (home, auto, project):
        os.makedirs(folder)
    session = os.path.join(project, "held session.jsonl")
    with open(session, "w", encoding="utf-8") as file:
        file.write(json.dumps({"type": "session", "version": 3, "id": "01a21a28-0000-7000-8000-000000000001",
                               "timestamp": "2026-11-27T00:00:00.000Z", "cwd": project}) + "\n")
    lease_path = session + ".lease"

    def lease():
        try:
            return json.load(open(lease_path))
        except (OSError, ValueError):
            return None

    env = sandbox_env(home, auto)
    env.pop("PI_EXECUTABLE", None)
    quote = lambda path: "".join(c if c.isalnum() or c in "-._~" else "%%%02X" % ord(c)
                                 for c in path.replace(os.sep, "/"))
    link = f"pi-gpui://open?workspace={quote(project)}&session={quote(session)}"
    # The stand-in terminal pi: holds the session with the package's own code.
    terminal = node_lease("m.holdSession(a[0], 'a terminal pi'); setInterval(() => {}, 1e6);", session)
    app = subprocess.Popen([EXE], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pis = {}
    try:
        check("lease: the terminal pi's lease is written", bool(wait_for(
            lambda: (lease() or {}).get("pid") == terminal.pid, 15)), lease())
        check("lease: the app opens its window", bool(wait_for(lambda: app_window(app.pid), 20)))
        subprocess.run([EXE, link], env=env, capture_output=True, timeout=30)
        # The app saves the link's project before it looks at the lease; give
        # that background check a moment after the save.
        state = os.path.join(home, ".config", "pi-gpui", "state.json")
        listed = lambda: os.path.isfile(state) and "lease project" in open(state, encoding="utf-8").read()
        check("lease: the app took the link", bool(wait_for(listed, 15)))
        time.sleep(1)
        started = list(session_pis(app.pid))
        check("lease: a session a terminal pi holds is not opened", not started, started)

        terminal.terminate()
        terminal.wait(10)
        subprocess.run([EXE, link], env=env, capture_output=True, timeout=30)
        pis = wait_for(lambda: session_pis(app.pid), 25) or {}
        check("lease: once the terminal pi is gone, the session opens", bool(pis), descendants(app.pid))
        # pi.cmd starts two node processes (launcher, then pi itself): compare
        # with the session pis running when the lease is read.
        held = wait_for(lambda: (lease() or {}).get("pid") in session_pis(app.pid) and lease(), 25) or lease()
        check("lease: the app's pi holds the session now",
              bool(held) and held.get("pid") in session_pis(app.pid), (held, session_pis(app.pid)))
        check("lease: ... marked as Pi GPUI with the app's pid",
              bool(held) and held.get("surface") == "Pi GPUI" and held.get("appPid") == app.pid, held)
        holder = node_lease("console.log(m.leaseHolder(a[0]) ?? 'nobody');", session, wait=True)
        check("lease: a terminal pi sees that Pi GPUI holds it", holder == "Pi GPUI", holder)
    finally:
        for process in (terminal, app):
            if process.poll() is None:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
        for pid in pis:
            if alive(pid):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)


def write_pi_session(agent_dir, project, text):
    """Writes a session the way pi does (compact JSON lines: the app's quick
    scan looks for `"role":"user"`) into `agent_dir`'s session folder."""
    encoded = "--" + project.lstrip("/" + chr(92)).replace("/", "-").replace(chr(92), "-").replace(":", "-") + "--"
    folder = os.path.join(agent_dir, "sessions", encoded)
    os.makedirs(folder, exist_ok=True)
    compact = dict(separators=(",", ":"))
    path = os.path.join(folder, time.strftime("%Y-%m-%dT%H-%M-%S-000Z") + "_smoke.jsonl")
    with open(path, "w", encoding="utf-8") as file:
        file.write(json.dumps({"type": "session", "version": 3, "id": "smoke", "cwd": project,
                               "timestamp": "2026-11-27T00:00:00.000Z"}, **compact) + "\n")
        file.write(json.dumps({"type": "message", "id": "m1", "parentId": None, "timestamp": "2026-11-27T00:00:01.000Z",
                               "message": {"role": "user", "content": text}}, **compact) + "\n")
    return path


def refresh_check(sandbox):
    """A sixth launch: a session a terminal pi made while the app runs shows up
    after the project's reload button is clicked."""
    home, auto, agent = (os.path.join(sandbox, name) for name in ("home6", "auto6", "agent6"))
    project = os.path.join(sandbox, "refresh project")
    for folder in (home, auto, agent, project):
        os.makedirs(folder)
    # PI_CODING_AGENT_DIR: the app and its pi read sessions from the sandbox.
    env = dict(offline_environ(), HOME=home, PI_GPUI_AUTOMATION=auto, PI_CODING_AGENT_DIR=agent)
    env.pop("PI_EXECUTABLE", None)
    app = subprocess.Popen([EXE, project], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    driver = Automation(auto)

    def sidebar(name):
        """The project's rows in the sidebar, as pixels."""
        path = os.path.join(sandbox, name + ".png")
        driver.send("move 700 400", "wait 300", f"shot {path}")
        if not os.path.isfile(path):
            return None
        _, _, bpp, rows = decode_png(path)
        return [bytes(row[:260 * bpp]) for row in rows[70:240]]

    try:
        check("refresh: the app opens its window", bool(wait_for(lambda: app_window(app.pid), 20)))
        driver.settle(sandbox)
        before = sidebar("refresh-before")
        write_pi_session(agent, project, "made in a terminal pi")
        # The project row is the first one under "Projects"; its reload button
        # sits between "New session" and "Remove" and shows on hover.
        answer = driver.send("move 150 92", "wait 300", "move 232 92", "wait 300", "click 232 92", "wait 1000")
        check("refresh: clicking the project's reload button is ok",
              answer is not None and all(line.endswith(": ok") for line in answer), answer)
        after = sidebar("refresh-after")
        check("refresh: the new session appears in the sidebar", before and after and before != after)
        again = sidebar("refresh-again")
        check("refresh: ... and the sidebar is still after that (the change was the new row)", after == again)
    finally:
        if app.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)


def gpui_check(sandbox):
    """A real terminal pi (its TUI in a hidden pseudo console) moves its
    session into the running app with /gpui, typed while a run is going: the
    run finishes and is saved first, then the app opens that session."""
    sys.path.insert(0, os.path.join(ROOT, "script", "debug"))
    from terminal import Terminal

    home, auto, agent = (os.path.join(sandbox, name) for name in ("home7", "auto7", "agent7"))
    project = os.path.join(sandbox, "gpui project")
    for folder in (home, auto, agent, project):
        os.makedirs(folder)
    # One HOME for both, so the exe /gpui starts hands over to this app.
    env = dict(offline_environ(), HOME=home, PI_GPUI_AUTOMATION=auto, PI_CODING_AGENT_DIR=agent,
               PI_GPUI_EXE=EXE, PI_SLOW_MODEL_MS="2500")
    env.pop("PI_EXECUTABLE", None)
    app = subprocess.Popen([EXE], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    command = ["cmd.exe", "/d", "/c", shutil.which("pi"), "--offline", "--no-skills", "--no-prompt-templates",
               "--no-context-files", "-e", os.path.join(ROOT, "script", "debug", "slow-model.ts"),
               "-e", os.path.join(ROOT, "packages", "pi-gpui", "index.ts"),
               "--provider", "faux", "--model", "faux-1", "--thinking", "off"]
    pis, term = {}, None

    def session_file():
        found = [os.path.join(folder, name) for folder, _, names in os.walk(os.path.join(agent, "sessions"))
                 for name in names if name.endswith(".jsonl")]
        return found[0] if len(found) == 1 else None

    try:
        check("gpui: the app opens its window", bool(wait_for(lambda: app_window(app.pid), 20)))
        term = Terminal(command, cwd=project, env=env)
        check("gpui: the terminal pi starts with the pi-gpui package", term.wait_for("pi-gpui", 40) is not None,
              term.output()[-500:])
        term.type("hello")
        time.sleep(0.3)
        term.type("\r")
        check("gpui: the prompt is running", term.wait_for("Working", 15) is not None)
        mark = term.mark()
        term.type("/gpui")
        time.sleep(0.3)
        term.type("\r")
        check("gpui: /gpui says the app takes over when the run finishes",
              term.wait_for("takes over this session when the current run finishes", 10, after=mark) is not None,
              term.output()[mark:][-600:])
        check("gpui: ... and pi keeps running the prompt meanwhile", term.exit_code() is None)
        check("gpui: the terminal pi exits after the run", term.wait_exit(30) == 0, term.exit_code())
        session = session_file()
        saved = open(session, encoding="utf-8").read() if session else ""
        check("gpui: the run's reply was saved before handing over", "SLOW_REPLY_DONE" in saved, session)

        pis = wait_for(lambda: session_pis(app.pid), 30) or {}
        check("gpui: the running app opens the session (its pi starts)", bool(pis), descendants(app.pid))
        lease_path = (session or "") + ".lease"
        lease = wait_for(lambda: os.path.isfile(lease_path) and json.load(open(lease_path)), 20) or {}
        check("gpui: ... that exact session, held by the app's pi",
              lease.get("pid") in pis and lease.get("appPid") == app.pid, lease)
        # The exe /gpui started (its command line holds this check's link) must
        # have handed over and exited. Other checks may run apps meanwhile.
        ours = lambda line: "pi-gpui://" in line and ("gpui+project" in line or "gpui%20project" in line)
        second = lambda: [pid for pid, (_, exe) in process_table().items() if exe.lower() == "pi-gpui.exe"
                          and pid != app.pid and ours(command_line(pid) or "")]
        check("gpui: no second app is left running (the launch handed over)",
              wait_for(lambda: not second(), 10), second())
    finally:
        if term:
            term.close()
        if app.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)
        for pid in pis:
            if alive(pid):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)


def held_check(sandbox):
    """An eighth launch: while a real terminal pi has a session open, its row in
    the sidebar is tinted red; once that pi quits, the tint goes away."""
    sys.path.insert(0, os.path.join(ROOT, "script", "debug"))
    from terminal import Terminal

    home, auto, agent = (os.path.join(sandbox, name) for name in ("home8", "auto8", "agent8"))
    project = os.path.join(sandbox, "held project")
    for folder in (home, auto, agent, project):
        os.makedirs(folder)
    session = write_pi_session(agent, project, "held by a terminal pi")
    env = dict(offline_environ(), HOME=home, PI_GPUI_AUTOMATION=auto, PI_CODING_AGENT_DIR=agent)
    env.pop("PI_EXECUTABLE", None)
    # The project's own new session is the first row; the listed one the second.
    app = subprocess.Popen([EXE, project], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    driver = Automation(auto)
    term = None

    def redness(name):
        """How much redder than green the second session row is, on average."""
        path = os.path.join(sandbox, name + ".png")
        driver.send("move 700 400", "wait 200", f"shot {path}")
        if not os.path.isfile(path):
            return None
        _, _, bpp, rows = decode_png(path)
        pixels = [(row[x * bpp], row[x * bpp + 1]) for row in rows[141:157] for x in range(12, 250)]
        return sum(r - g for r, g in pixels) / len(pixels)

    try:
        check("held: the app opens its window", bool(wait_for(lambda: app_window(app.pid), 20)))
        driver.settle(sandbox)
        free = redness("held-free")
        command = ["cmd.exe", "/d", "/c", shutil.which("pi"), "--offline", "--no-skills", "--no-prompt-templates",
                   "--no-context-files", "-e", os.path.join(ROOT, "packages", "pi-gpui", "index.ts"),
                   "--session", session]
        term = Terminal(command, cwd=project, env=env)
        check("held: a terminal pi opens the session", term.wait_for("pi-gpui", 40) is not None, term.output()[-400:])
        tinted = wait_for(lambda: (redness("held-busy") or 0) > (free or 0) + 3 and redness("held-busy"), 10)
        check("held: its row turns red in the app", bool(tinted), f"free {free}, now {redness('held-busy')}")
        term.type("\x04")  # Ctrl+D: quit
        check("held: the terminal pi quits", term.wait_exit(15) is not None, term.output()[-300:])
        cleared = wait_for(lambda: abs((redness("held-after") or 99) - (free or 0)) < 1.5, 10)
        check("held: the red goes away once it has quit", bool(cleared), f"free {free}, now {redness('held-after')}")
    finally:
        if term:
            term.close()
        if app.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)


# ---------------------------------------------------------------------- PNG
def decode_png(path):
    """(width, height, rows of RGB(A) bytes) of an 8-bit, non-interlaced PNG."""
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, header = 8, b"", None
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
    width, height, depth, color, _, _, interlace = header
    assert depth == 8 and interlace == 0 and color in (2, 6), f"unsupported PNG {header}"
    bpp = 3 if color == 2 else 4
    raw, stride = zlib.decompress(idat), width * bpp
    rows, previous, at = [], bytearray(stride), 0
    for _ in range(height):
        kind = raw[at]
        line = bytearray(raw[at + 1:at + 1 + stride])
        at += 1 + stride
        if kind == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif kind == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 255
        elif kind == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b, c = previous[i], (previous[i - bpp] if i >= bpp else 0)
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        previous = line
    return width, height, bpp, rows


def picture_stats(path):
    width, height, bpp, rows = decode_png(path)
    colors, bright = set(), 0
    for y in range(0, height, 2):
        row = rows[y]
        for x in range(0, width, 2):
            pixel = bytes(row[x * bpp:x * bpp + 3])
            colors.add(pixel)
            if max(pixel) > 150:
                bright += 1
    return width, height, len(colors), bright


# --------------------------------------------------------------- automation
class Automation:
    def __init__(self, folder):
        self.folder = folder

    def send(self, *commands, timeout=30, gone=None):
        """Runs automation commands and returns the answer lines. `gone`: a
        test that the app has exited (it cannot answer then, e.g. after ctrl-q)."""
        done = os.path.join(self.folder, "done")
        if os.path.exists(done):
            os.remove(done)
        staged = os.path.join(self.folder, "command.tmp")
        with open(staged, "w", encoding="utf-8") as file:
            file.write("\n".join(commands) + "\n")
        os.replace(staged, os.path.join(self.folder, "command"))
        if not wait_for(lambda: os.path.exists(done) or (gone and gone()), timeout, 0.05) or not os.path.exists(done):
            return None
        time.sleep(0.05)
        return open(done, encoding="utf-8").read().strip().splitlines()

    def settle(self, folder, quiet=1.0, timeout=15, interval=0.25):
        """Waits until the window has not changed for `quiet` seconds (screenshots
        compared as bytes, no decoding). Returns False if it never settles."""
        path, previous, since = os.path.join(folder, f"settle-{id(self)}.png"), None, time.time()
        end = time.time() + timeout
        while time.time() < end:
            self.send(f"shot {path}")
            current = open(path, "rb").read() if os.path.isfile(path) else None
            if not current or current != previous:
                previous, since = current, time.time()
            elif time.time() - since >= quiet:
                return True
            time.sleep(interval)
        return False


def main_check(sandbox):
    """The first launch, on a project: start-up, automation, sessions, quitting."""
    home, auto, project = (os.path.join(sandbox, name) for name in ("home", "auto", "my project"))
    for folder in (home, auto, project):
        os.makedirs(folder)
    log_path = os.path.join(sandbox, "app.log")
    env = sandbox_env(home, auto)
    env.pop("PI_EXECUTABLE", None)

    log = open(log_path, "w")
    app = subprocess.Popen([EXE, project], env=env, stdout=log, stderr=subprocess.STDOUT)
    driver = Automation(auto)
    every_pi = set()
    try:
        # --- start-up
        def started():
            log.flush()
            return "using pi" in open(log_path, errors="replace").read()

        check("app starts and finds pi without PI_EXECUTABLE", wait_for(started, 20))
        text = open(log_path, errors="replace").read()
        check("pi is a Windows launcher (.cmd/.exe), not a bare script",
              any(ext in text.split("using pi", 1)[-1].splitlines()[0].lower() for ext in (".cmd", ".exe", ".bat")),
              text)
        check("the app is still running", app.poll() is None)

        # --- automation protocol
        answer = driver.send("wait 300", "frobnicate", "key bogus-key", "click 10", "wait x")
        check("automation answers every command", answer is not None and len(answer) == 5, answer)
        if answer and len(answer) == 5:
            check("wait is ok", answer[0].endswith(": ok"), answer[0])
            check("unknown command is an error line", answer[1].endswith("error: unknown command"), answer[1])
            check("bad key is an error line", ": error:" in answer[2], answer[2])
            check("bad click is an error line", ": error:" in answer[3], answer[3])
            check("bad wait is an error line", ": error:" in answer[4], answer[4])

        # --- screenshot has real content
        shot = os.path.join(sandbox, "window.png")
        answer = driver.send(f"shot {shot}")
        check("shot reports ok", answer == [f"shot {shot}: ok"], answer)
        if os.path.isfile(shot):
            width, height, colors, bright = picture_stats(shot)
            check("screenshot is window sized", width >= 600 and height >= 400, f"{width}x{height}")
            check("screenshot is not blank (many colours, some light text)", colors >= 6 and bright > 50,
                  f"{colors} colours, {bright} bright pixels")
        else:
            check("screenshot file was written", False)

        # --- focus and console windows
        # Headless there is nothing else on the desktop to keep the focus.
        if not HEADLESS:
            check("automation does not take the focus (best effort: Windows may block a focus steal anyway)",
                  foreground_pid() != app.pid, f"foreground pid {foreground_pid()}")

        # --- the project is saved without the verbatim prefix
        state_path = os.path.join(home, ".config", "pi-gpui", "state.json")
        check("state.json is written", wait_for(lambda: os.path.isfile(state_path), 10))
        if os.path.isfile(state_path):
            paths = [w["path"] for w in json.load(open(state_path))["workspaces"]]
            check("project path has no \\\\?\\ prefix", paths and not any(p.startswith("\\\\?\\") for p in paths), paths)
            check("project path is the real folder (spaces kept)",
                  paths and os.path.normcase(os.path.realpath(paths[0])) == os.path.normcase(os.path.realpath(project)), paths)

        # --- opening the app on a folder starts a session of its own
        startup = wait_for(lambda: session_pis(app.pid) and descendants(app.pid), 20) or {}
        startup_pi = list(session_pis(app.pid))
        every_pi.update(startup)
        check("opening a folder starts a session: pi (node.exe) runs under the app", bool(startup_pi), startup)
        # Let the new session finish loading, so the window is still for the click test.
        driver.settle(sandbox)

        # --- a click really reaches the UI: the sidebar button hides and shows it
        def picture(name):
            path = os.path.join(sandbox, name + ".png")
            driver.send(f"shot {path}")
            return open(path, "rb").read() if os.path.isfile(path) else b""

        before = picture("before")
        answer = driver.send("move 23 18", "click 23 18", "wait 300")
        check("clicking the sidebar button is ok", answer is not None and all(l.endswith(": ok") for l in answer), answer)
        hidden = picture("hidden")
        check("the click changed the window (sidebar hidden)", before and hidden and hidden != before)
        # Move the pointer off the button so its hover highlight is gone again.
        driver.send("click 23 18", "move 700 300")
        # Under load the redraw can lag: compare once the window is still again.
        driver.settle(sandbox, quiet=0.5)
        check("a second click restores the window exactly", picture("after") == before)

        # --- ctrl-n starts one more pi, and nothing pops up
        # (no click here: clicking the empty transcript takes the focus off the
        # chat, and then ctrl-w would not reach the session)
        answer = driver.send("key ctrl-n", "wait 500")
        check("new session is ok", answer is not None and all(line.endswith(": ok") for line in answer), answer)

        def second_pi():
            now = descendants(app.pid)
            fresh = {pid: exe for pid, exe in now.items() if pid not in startup}
            return fresh if any(pid in fresh for pid in session_pis(app.pid)) else None

        second = wait_for(second_pi, 20) or {}
        every_pi.update(second)
        check("ctrl-n starts a second pi (new node.exe)", bool(second), descendants(app.pid))
        # --- a file copied in Explorer pastes as an attachment (overwrites the clipboard)
        attachment = os.path.join(sandbox, "attach me.txt")
        with open(attachment, "w") as file:
            file.write("hello")
        copied = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Set-Clipboard -Path '{attachment}'"],
            capture_output=True).returncode == 0
        check("the test could put a file on the clipboard", copied)
        before_paste = picture("paste-before")
        driver.send("key ctrl-v", "wait 500")
        after_paste = picture("paste-after")
        check("pasting a copied file changes the message field",
              before_paste and after_paste and before_paste != after_paste)
        # A broken file paste falls back to the path as plain text (the
        # clipboard has that too), which changes the field as well: tell them
        # apart by the chip's accent colour.
        chips = (chip_pixels(os.path.join(sandbox, "paste-before.png")),
                 chip_pixels(os.path.join(sandbox, "paste-after.png")))
        check("... as an attachment chip, not the file's path as text", chips[1] - chips[0] > 60, chips)

        everyone = {app.pid, *descendants(app.pid)}
        # Windows' input-language indicator lives in the app's process (shown on a
        # fresh hidden desktop): system UI, not ours.
        consoles = [w for w in visible_windows(everyone)
                    if w[0] != "Zed::Window" and not w[0].replace(" ", "_").startswith("UAC_Input_Indicator")
                    and not w[0].startswith("UAC_InputIndicator")]
        check("no console or stray window appears", not consoles, consoles)
        check("the app's own window is visible", any(w[0] == "Zed::Window" for w in visible_windows({app.pid})))
        hwnd = app_window(app.pid)
        check("the window has the app icon (taskbar, Alt+Tab)", bool(hwnd and window_icon(hwnd)))

        # --- closing a session stops exactly its pi, and leaves the other
        answer = driver.send("key ctrl-w", "wait 300")
        check("closing the session is ok", answer is not None and answer[0].endswith(": ok"), answer)
        check("closing a session stops its pi completely",
              wait_for(lambda: not any(alive(pid) for pid in second), 15),
              [pid for pid in second if alive(pid)])
        # Only pi itself: `startup` may also hold short-lived helpers (the update check's curl.exe).
        check("the other session's pi keeps running", startup_pi and all(alive(pid) for pid in startup_pi),
              [pid for pid in startup_pi if not alive(pid)])

        # --- quitting with a session open stops its pi too
        driver.send("key ctrl-q", gone=lambda: app.poll() is not None)
        check("ctrl-q quits the app", wait_for(lambda: app.poll() is not None, 15))
        check("quitting leaves no pi process behind",
              wait_for(lambda: not any(alive(pid) for pid in every_pi), 15),
              [pid for pid in every_pi if alive(pid)])
    finally:
        if app.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)
        for pid in every_pi:
            if alive(pid):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        log.close()


CHECKS = {"main": main_check, "controls": window_controls_check, "crash": crash_check,
          "handover": handover_check, "lease": lease_check, "refresh": refresh_check,
          "gpui": gpui_check, "held": held_check, "ask": ask_check,
          "template": template_check, "edit": edit_check,
          "images": images_check, "grill": grill_check, "subagents": subagents_check}


def run_check(name, sandbox):
    """One check in this thread: (name, its output lines, seconds)."""
    if HEADLESS:
        join_headless_desktop()  # the desktop is per thread
    _output.lines = []
    started = time.time()
    try:
        CHECKS[name](sandbox)
    except Exception as error:  # a bug in the check itself
        check(f"{name}: the check ran to the end", False, repr(error))
    return name, _output.lines, time.time() - started


def main():
    if not os.path.isfile(EXE):
        sys.exit(f"{EXE} not found: run `cargo build --release` first")
    if not shutil.which("pi"):
        sys.exit("pi is not on PATH: this test checks that the app finds it there")
    names = ONLY or list(CHECKS)
    unknown = [name for name in names if name not in CHECKS]
    if unknown:
        sys.exit(f"unknown check(s) {unknown}; choose from {sorted(CHECKS)}")
    if HEADLESS:
        join_headless_desktop()
    else:
        os.environ.pop("PI_GPUI_HEADLESS", None)

    sandbox = tempfile.mkdtemp(prefix="pi-gpui-smoke-")
    print(f"exe      {EXE}\nsandbox  {sandbox}\nheadless {HEADLESS}\n", flush=True)
    started = time.time()
    try:
        # Each check has its own HOME, automation folder and app, so they can
        # run side by side; --serial runs them one after another.
        print(f"started {len(names)} group(s){' one by one' if SERIAL else ' side by side'}: {', '.join(names)}",
              flush=True)
        with ThreadPoolExecutor(1 if SERIAL else len(names)) as pool:
            running = [pool.submit(run_check, name, sandbox) for name in names]
            # Each group is printed as soon as it is done, with its time.
            for done, future in enumerate(as_completed(running), 1):
                name, lines, seconds = future.result()
                failed = sum(line.startswith("FAIL") for line in lines)
                print(f"--- {name}: {seconds:.1f} s, {len(lines) - failed} passed"
                      + (f", {failed} FAILED" if failed else "") + f"   [{done}/{len(names)} at {time.time() - started:.1f} s]",
                      flush=True)
                for line in lines:
                    if VERBOSE or line.startswith("FAIL"):
                        print(line)
    finally:
        if os.environ.get("SMOKE_KEEP") and failures:
            print(f"kept the sandbox for inspection: {sandbox}")
        else:
            shutil.rmtree(sandbox, ignore_errors=True)
    print(f"\n{len(names)} check group(s) in {time.time() - started:.1f} s")
    report()


def report():
    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for name in failures:
            print("  -", name)
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
