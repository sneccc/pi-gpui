"""Debug Pi GPUI on Windows without it popping up: start it headless in a
sandbox, drive it, look at it, stop it.

    python -I script/debug/app.py start [--fresh] [--show] [folder]
    python -I script/debug/app.py do "key ctrl-n" "wait 500" shot
    python -I script/debug/app.py status            process tree and windows
    python -I script/debug/app.py hittest [x ...]   WM_NCHITTEST along the title bar
    python -I script/debug/app.py ncclick X [Y]     a real title-bar click (X < 0: from the right)
    python -I script/debug/app.py dialog PATH       fill and confirm the "Select Folder" dialog
    python -I script/debug/app.py stop

`start` runs target/release/pi-gpui.exe (PI_GPUI_EXE overrides) with
PI_GPUI_HEADLESS=1, so its windows sit on a hidden desktop: nothing shows,
nothing takes the focus, and `shot` still works. `--show` puts it on screen.
The sandbox is .scratch/headless (HOME, automation folder, app.log); it is
kept between runs unless `--fresh`. `do` takes automation commands (see
src/automation.rs); a bare `shot` saves to .scratch/shots/ and prints the path.
The window commands join the hidden desktop themselves.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRATCH = os.path.join(ROOT, ".scratch")
SANDBOX = os.path.join(SCRATCH, "headless")
HOME, AUTO, LOG = (os.path.join(SANDBOX, name) for name in ("home", "auto", "app.log"))
STATE = os.path.join(SANDBOX, "app.json")
SHOTS = os.path.join(SCRATCH, "shots")
EXE = os.environ.get("PI_GPUI_EXE") or os.path.join(ROOT, "target", "release", "pi-gpui.exe")
DESKTOP = "pi-gpui-headless"  # src/platform.rs HEADLESS_DESKTOP

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, ctypes.c_ssize_t]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, ctypes.c_ssize_t]
user32.CreateDesktopW.restype = wt.HANDLE
user32.CreateDesktopW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.c_void_p]
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


# ------------------------------------------------------------------ the app
def state():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def alive(pid):
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = wt.DWORD()
    kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


def running():
    pid = state().get("pid")
    if not pid or not alive(pid):
        sys.exit("no debug app is running: python -I script/debug/app.py start")
    return pid


def send(*commands, timeout=60):
    """Runs automation commands, returns the result lines."""
    done = os.path.join(AUTO, "done")
    if os.path.exists(done):
        os.remove(done)
    staged = os.path.join(AUTO, "command.tmp")
    with open(staged, "w", encoding="utf-8") as file:
        file.write("\n".join(commands) + "\n")
    os.replace(staged, os.path.join(AUTO, "command"))
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(done):
            time.sleep(0.05)
            return open(done, encoding="utf-8").read().splitlines()
        time.sleep(0.1)
    return None


def start(args):
    fresh, show = "--fresh" in args, "--show" in args
    folders = [os.path.abspath(arg) for arg in args if not arg.startswith("--")]
    if state().get("pid") and alive(state()["pid"]):
        sys.exit(f"already running (pid {state()['pid']}): stop it first")
    if not os.path.isfile(EXE):
        sys.exit(f"{EXE} not found: cargo build --release")
    if fresh:
        shutil.rmtree(SANDBOX, ignore_errors=True)
    for folder in (HOME, AUTO):
        os.makedirs(folder, exist_ok=True)
    for stale in ("command", "done"):
        if os.path.exists(os.path.join(AUTO, stale)):
            os.remove(os.path.join(AUTO, stale))
    env = dict(os.environ, HOME=HOME, PI_GPUI_AUTOMATION=AUTO)
    env.pop("PI_GPUI_HEADLESS", None)
    if not show:
        env["PI_GPUI_HEADLESS"] = "1"
    log = open(LOG, "w")
    # Outlive this script (and the job of the tool that runs it, when allowed).
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        app = subprocess.Popen([EXE, *folders], env=env, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=flags | 0x01000000)  # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        app = subprocess.Popen([EXE, *folders], env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    json.dump({"pid": app.pid, "headless": not show}, open(STATE, "w"))
    answer = send("wait 1", timeout=30)
    if not answer or app.poll() is not None:
        print(open(LOG, errors="replace").read())
        sys.exit("the app did not answer: see the log above")
    print(f"pid      {app.pid}\nheadless {not show}\nhome     {HOME}\nlog      {LOG}")


def stop(_):
    pid = state().get("pid")
    if pid and alive(pid):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        print(f"stopped {pid}")
    else:
        print("not running")


def do(commands):
    running()
    os.makedirs(SHOTS, exist_ok=True)
    resolved = []
    for command in commands:
        word, _, rest = command.partition(" ")
        if word == "shot":
            path, _, scale = rest.partition(" ")
            path = os.path.abspath(path) if path else os.path.join(SHOTS, time.strftime("%H%M%S") + ".png")
            command = f"shot {path} {scale}".strip()
        resolved.append(command)
    answer = send(*resolved)
    if answer is None:
        sys.exit("no answer from the app")
    print("\n".join(answer))
    if any(": error" in line for line in answer):
        sys.exit(1)


# ------------------------------------------------------------------ windows
def join_desktop():
    """Moves this thread onto the hidden desktop when the app runs there."""
    if state().get("headless"):
        desktop = user32.CreateDesktopW(DESKTOP, None, None, 0, 0x10000000, None)  # GENERIC_ALL
        if not desktop or not user32.SetThreadDesktop(desktop):
            sys.exit(f"could not join the hidden desktop (error {ctypes.get_last_error()})")


def windows_of(pids):
    found = []

    def visit(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids:
            cls, title = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            user32.GetWindowTextW(hwnd, title, 256)
            found.append((hwnd, owner.value, bool(user32.IsWindowVisible(hwnd)), cls.value, title.value))
        return True

    user32.EnumWindows(WNDENUMPROC(visit), 0)
    return found


def main_window():
    join_desktop()
    pid = running()
    for hwnd, _, visible, cls, _ in windows_of({pid}):
        if visible and cls == "Zed::Window":
            return hwnd
    sys.exit("the app has no window")


class ProcessEntry(ctypes.Structure):
    _fields_ = [("size", wt.DWORD), ("usage", wt.DWORD), ("pid", wt.DWORD), ("heap", ctypes.c_size_t),
                ("module", wt.DWORD), ("threads", wt.DWORD), ("parent", wt.DWORD), ("priority", wt.LONG),
                ("flags", wt.DWORD), ("exe", wt.WCHAR * 260)]


kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.OpenProcess.restype = wt.HANDLE


def process_table():
    """{pid: (parent pid, exe)} from one Toolhelp snapshot (milliseconds, unlike WMI)."""
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    table, entry = {}, ProcessEntry()
    entry.size = ctypes.sizeof(ProcessEntry)
    more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
    while more:
        table[entry.pid] = (entry.parent, entry.exe)
        more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    kernel32.CloseHandle(snapshot)
    return table


def start_time(pid):
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    times = (ctypes.c_ulonglong * 4)()
    ok = kernel32.GetProcessTimes(handle, *(ctypes.byref(times, 8 * i) for i in range(4)))
    kernel32.CloseHandle(handle)
    return times[0] if ok else None


def process_tree(root):
    table = process_table()
    children = {}
    for pid, (parent, _) in table.items():
        children.setdefault(parent, []).append(pid)
    lines, pids = [], []

    def walk(pid, depth):
        pids.append(pid)
        lines.append("  " * depth + f"{table.get(pid, (0, '?'))[1]} {pid}")
        born = start_time(pid)
        for child in sorted(children.get(pid, [])):
            # A stale parent pid can point at a newer process: only real children.
            child_born = start_time(child)
            if child != pid and born is not None and child_born is not None and child_born >= born:
                walk(child, depth + 1)

    walk(root, 0)
    return lines, pids


def status(_):
    join_desktop()
    pid = running()
    lines, pids = process_tree(pid)
    print(f"headless {state().get('headless')}\n" + "\n".join(lines) + "\nwindows:")
    for hwnd, owner, visible, cls, title in windows_of(set(pids)):
        if visible:
            print(f"  {hwnd:#x} pid {owner} {cls!r} {title!r}")


def client_point(hwnd, x, y):
    point = wt.POINT(x, y)
    user32.ClientToScreen(hwnd, ctypes.byref(point))
    return (point.y & 0xFFFF) << 16 | (point.x & 0xFFFF)


def client_width(hwnd):
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    return rect.right


HIT = {0: "error", 1: "client", 2: "caption", 3: "sysmenu", 8: "minimize", 9: "maximize", 20: "close",
       10: "left", 11: "right", 12: "top", 15: "bottom", -1: "transparent"}


def hit_test(hwnd, x, y=18):
    # gpui hit-tests the last mouse position it saw, so move there first.
    user32.PostMessageW(hwnd, 0x0200, 0, (y << 16) | x)  # WM_MOUSEMOVE
    time.sleep(0.25)
    return user32.SendMessageW(hwnd, 0x0084, 0, client_point(hwnd, x, y))  # WM_NCHITTEST


def hittest(args):
    hwnd = main_window()
    width = client_width(hwnd)
    for x in [int(a) for a in args] or [23, 600, width - 125, width - 79, width - 33, width - 4]:
        hit = hit_test(hwnd, x)
        print(f"x={x:5d} y=18 -> {HIT.get(hit, hit)}")
    print("client width", width)


def ncclick(args):
    hwnd = main_window()
    x, y = int(args[0]), int(args[1]) if len(args) > 1 else 18
    if x < 0:
        x += client_width(hwnd)
    hit = hit_test(hwnd, x, y)
    where = client_point(hwnd, x, y)
    for message in (0x00A0, 0x00A1, 0x00A2):  # WM_NCMOUSEMOVE, WM_NCLBUTTONDOWN, WM_NCLBUTTONUP
        user32.PostMessageW(hwnd, message, hit, where)
        time.sleep(0.1)
    print(f"clicked x={x} y={y} ({HIT.get(hit, hit)})")


def dialog(args):
    """The native folder picker is a separate window: automation cannot reach it."""
    join_desktop()
    running()
    box = None
    end = time.time() + 10
    while time.time() < end and not box:
        box = user32.FindWindowW("#32770", "Select Folder")
        time.sleep(0.2)
    if not box:
        sys.exit("no Select Folder dialog")
    edits = []

    def visit(hwnd, _):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "Edit":
            edits.append(hwnd)
        return True

    user32.EnumChildWindows(box, WNDENUMPROC(visit), 0)
    if not edits:
        sys.exit("the dialog has no text field")
    text = ctypes.create_unicode_buffer(os.path.abspath(args[0]))
    user32.SendMessageW(edits[0], 0x000C, 0, ctypes.addressof(text))  # WM_SETTEXT
    user32.PostMessageW(user32.GetDlgItem(box, 1), 0x00F5, 0, 0)  # BM_CLICK on OK
    print("chose", os.path.abspath(args[0]))


COMMANDS = {"start": start, "stop": stop, "do": do, "status": status, "hittest": hittest,
            "ncclick": ncclick, "dialog": dialog}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]](sys.argv[2:])
