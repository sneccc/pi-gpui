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

    python -I script/windows-smoke.py [path\\to\\pi-gpui.exe]

Needs pi on PATH. Sends no prompt, so it costs no tokens. Exit code 0 = all
checks passed. See docs/WINDOWS_UI_CHECKS.md.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "target", "release", "pi-gpui.exe")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

failures = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(name)
    return ok


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


def descendants(pid):
    table = process_table()
    found, queue = {}, [pid]
    while queue:
        parent = queue.pop()
        for child, (parent_of_child, exe) in table.items():
            if parent_of_child == parent and child not in found:
                found[child] = exe
                queue.append(child)
    return found


def alive(pid):
    return pid in process_table()


# ------------------------------------------------------------------ windows
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def visible_windows(pids):
    """[(class name, title)] of visible top-level windows owned by `pids`."""
    found = []

    def visit(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd):
            cls, title = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            user32.GetWindowTextW(hwnd, title, 256)
            found.append((cls.value, title.value))
        return True

    user32.EnumWindows(WNDENUMPROC(visit), 0)
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
    # gpui follows the pointer itself: move it there first, as real input does
    user32.PostMessageW(hwnd, 0x0200, 0, (y << 16) | x)  # WM_MOUSEMOVE
    time.sleep(0.2)
    return user32.SendMessageW(hwnd, 0x0084, 0, screen_position(hwnd, x, y))  # WM_NCHITTEST


def caption_click(hwnd, x, y=18, double=False):
    """The messages a real left click (or double click) on the title bar sends."""
    hit = hit_test(hwnd, x, y)
    where = screen_position(hwnd, x, y)
    messages = [0x00A0, 0x00A1, 0x00A2]  # WM_NCMOUSEMOVE, WM_NCLBUTTONDOWN, WM_NCLBUTTONUP
    if double:
        messages += [0x00A3, 0x00A2]  # WM_NCLBUTTONDBLCLK, WM_NCLBUTTONUP
    for message in messages:
        user32.PostMessageW(hwnd, message, hit, where)
        time.sleep(0.05)
    time.sleep(0.7)
    return hit


def window_controls_check(sandbox):
    """A second launch, without a project: the caption buttons."""
    home, auto = os.path.join(sandbox, "home2"), os.path.join(sandbox, "auto2")
    os.makedirs(home)
    os.makedirs(auto)
    env = dict(os.environ, HOME=home, PI_GPUI_AUTOMATION=auto)
    env.pop("PI_EXECUTABLE", None)
    app = subprocess.Popen([EXE], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        hwnd = wait_for(lambda: app_window(app.pid), 20)
        check("controls: the window opens without a project", bool(hwnd))
        if not hwnd:
            return
        time.sleep(1.0)
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
        check("controls: maximize button maximizes",
              caption_click(hwnd, client_width(hwnd) - 79) == 9 and zoomed())
        check("controls: the same button restores",
              caption_click(hwnd, client_width(hwnd) - 79) == 9 and not zoomed())
        caption_click(hwnd, 600, double=True)
        check("controls: double click on the caption maximizes", zoomed())
        caption_click(hwnd, 600, double=True)
        check("controls: double click again restores", not zoomed())
        check("controls: minimize button minimizes",
              caption_click(hwnd, client_width(hwnd) - 125) == 8 and bool(user32.IsIconic(hwnd)))
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(0.5)
        check("controls: close button closes the app",
              caption_click(hwnd, client_width(hwnd) - 33) == 20
              and bool(wait_for(lambda: app.poll() is not None, 10)))
    finally:
        if app.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)


def crash_check(sandbox):
    """A third launch: the app is killed without warning (as a crash would),
    and pi must not be left running."""
    home, auto = os.path.join(sandbox, "home3"), os.path.join(sandbox, "auto3")
    project = os.path.join(sandbox, "crash project")
    for folder in (home, auto, project):
        os.makedirs(folder)
    env = dict(os.environ, HOME=home, PI_GPUI_AUTOMATION=auto)
    env.pop("PI_EXECUTABLE", None)
    app = subprocess.Popen([EXE, project], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pis = {}
    try:
        pis = wait_for(lambda: any(e.lower() == "node.exe" for e in descendants(app.pid).values())
                       and descendants(app.pid), 25) or {}
        check("crash: the app has a session running pi", bool(pis), pis)
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


def handover_check(sandbox):
    """A fourth launch, with a HOME of its own: later launches hand their
    folder or pi-gpui:// link to the running app and exit."""
    home, auto = os.path.join(sandbox, "home4"), os.path.join(sandbox, "auto4")
    second_project = os.path.join(sandbox, "second project")
    third_project = os.path.join(sandbox, "third project")
    for folder in (home, auto, second_project, third_project):
        os.makedirs(folder)
    env = dict(os.environ, HOME=home, PI_GPUI_AUTOMATION=auto)
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

    def send(self, *commands, timeout=30):
        done = os.path.join(self.folder, "done")
        if os.path.exists(done):
            os.remove(done)
        staged = os.path.join(self.folder, "command.tmp")
        with open(staged, "w", encoding="utf-8") as file:
            file.write("\n".join(commands) + "\n")
        os.replace(staged, os.path.join(self.folder, "command"))
        if not wait_for(lambda: os.path.exists(done), timeout, 0.1):
            return None
        time.sleep(0.05)
        return open(done, encoding="utf-8").read().strip().splitlines()


def main():
    if not os.path.isfile(EXE):
        sys.exit(f"{EXE} not found: run `cargo build --release` first")
    if not shutil.which("pi"):
        sys.exit("pi is not on PATH: this test checks that the app finds it there")

    sandbox = tempfile.mkdtemp(prefix="pi-gpui-smoke-")
    home, auto, project = (os.path.join(sandbox, name) for name in ("home", "auto", "my project"))
    for folder in (home, auto, project):
        os.makedirs(folder)
    log_path = os.path.join(sandbox, "app.log")
    env = dict(os.environ, HOME=home, PI_GPUI_AUTOMATION=auto)
    env.pop("PI_EXECUTABLE", None)

    print(f"exe      {EXE}\nsandbox  {sandbox}\n")
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
        check("automation does not take the focus (best effort: Windows may block a focus steal anyway)", foreground_pid() != app.pid,
              f"foreground pid {foreground_pid()}")

        # --- the project is saved without the verbatim prefix
        state_path = os.path.join(home, ".config", "pi-gpui", "state.json")
        check("state.json is written", wait_for(lambda: os.path.isfile(state_path), 10))
        if os.path.isfile(state_path):
            paths = [w["path"] for w in json.load(open(state_path))["workspaces"]]
            check("project path has no \\\\?\\ prefix", paths and not any(p.startswith("\\\\?\\") for p in paths), paths)
            check("project path is the real folder (spaces kept)",
                  paths and os.path.normcase(os.path.realpath(paths[0])) == os.path.normcase(os.path.realpath(project)), paths)

        # --- opening the app on a folder starts a session of its own
        startup = wait_for(lambda: any(exe.lower() == "node.exe" for exe in descendants(app.pid).values())
                           and descendants(app.pid), 20) or {}
        every_pi.update(startup)
        check("opening a folder starts a session: pi (node.exe) runs under the app",
              any(exe.lower() == "node.exe" for exe in startup.values()), startup)
        # Let the new session finish loading, so the window is still for the click test.
        driver.send("wait 2500")

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
        driver.send("click 23 18", "move 700 300", "wait 300")
        check("a second click restores the window exactly", picture("after") == before)

        # --- ctrl-n starts one more pi, and nothing pops up
        # (no click here: clicking the empty transcript takes the focus off the
        # chat, and then ctrl-w would not reach the session)
        answer = driver.send("key ctrl-n", "wait 500")
        check("new session is ok", answer is not None and all(line.endswith(": ok") for line in answer), answer)

        def second_pi():
            now = descendants(app.pid)
            fresh = {pid: exe for pid, exe in now.items() if pid not in startup}
            return fresh if any(exe.lower() == "node.exe" for exe in fresh.values()) else None

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

        everyone = {app.pid, *descendants(app.pid)}
        consoles = [w for w in visible_windows(everyone) if w[0] not in ("Zed::Window",)]
        check("no console or stray window appears", not consoles, consoles)
        check("the app's own window is visible", any(w[0] == "Zed::Window" for w in visible_windows({app.pid})))

        # --- closing a session stops exactly its pi, and leaves the other
        answer = driver.send("key ctrl-w", "wait 300")
        check("closing the session is ok", answer is not None and answer[0].endswith(": ok"), answer)
        check("closing a session stops its pi completely",
              wait_for(lambda: not any(alive(pid) for pid in second), 15),
              [pid for pid in second if alive(pid)])
        check("the other session's pi keeps running", all(alive(pid) for pid in startup),
              [pid for pid in startup if not alive(pid)])

        # --- quitting with a session open stops its pi too
        driver.send("key ctrl-q")
        check("ctrl-q quits the app", wait_for(lambda: app.poll() is not None, 15))
        check("quitting leaves no pi process behind",
              wait_for(lambda: not any(alive(pid) for pid in every_pi), 15),
              [pid for pid in every_pi if alive(pid)])
        window_controls_check(sandbox)
        crash_check(sandbox)
        handover_check(sandbox)
    finally:
        if app.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)
        for pid in every_pi:
            if alive(pid):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        log.close()
        shutil.rmtree(sandbox, ignore_errors=True)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for name in failures:
            print("  -", name)
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
