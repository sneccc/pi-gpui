"""Run a terminal program (such as pi's TUI) in a hidden pseudo console,
type into it and wait for text on its screen. Windows ConPTY through ctypes:
no window, no focus, no extra packages.

    from terminal import Terminal          # sys.path.insert(0, "script/debug") first
    with Terminal(["pi.cmd", "--offline"], cwd=folder, env=env) as term:
        term.wait_for("ready")             # text anywhere in the output so far (ANSI stripped)
        term.type("hello\\r")               # \\r is Enter
        term.wait_for("reply", after=term.mark())   # only output after the mark
        print(term.screen()[-2000:])

    python -I script/debug/terminal.py [--cwd DIR] -- command args...   interactive-ish: prints output

Output is kept in memory (ANSI escape sequences removed for matching). Use
mark() before an action and `after=` to match only what came after it.
"""
import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import sys
import threading
import time

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HPCON = wt.HANDLE
kernel32.CreatePseudoConsole.argtypes = [wt._COORD, wt.HANDLE, wt.HANDLE, wt.DWORD, ctypes.POINTER(HPCON)]
kernel32.CreatePseudoConsole.restype = ctypes.c_long
kernel32.ClosePseudoConsole.argtypes = [HPCON]
kernel32.CreatePipe.argtypes = [ctypes.POINTER(wt.HANDLE), ctypes.POINTER(wt.HANDLE), ctypes.c_void_p, wt.DWORD]
kernel32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t)]
kernel32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                               ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
kernel32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
kernel32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]

PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
                ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE),
                ("hStdError", wt.HANDLE)]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD),
                ("dwThreadId", wt.DWORD)]


ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]|[\x00-\x08\x0e-\x1f]")


def strip_ansi(text):
    return ANSI.sub("", text)


class Terminal:
    def __init__(self, command, cwd=None, env=None, columns=120, rows=40):
        read_in, self._write_in = wt.HANDLE(), wt.HANDLE()
        self._read_out, write_out = wt.HANDLE(), wt.HANDLE()
        if not kernel32.CreatePipe(ctypes.byref(read_in), ctypes.byref(self._write_in), None, 0) or \
                not kernel32.CreatePipe(ctypes.byref(self._read_out), ctypes.byref(write_out), None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        self._console = HPCON()
        result = kernel32.CreatePseudoConsole(wt._COORD(columns, rows), read_in, write_out, 0,
                                              ctypes.byref(self._console))
        if result != 0:
            raise OSError(f"CreatePseudoConsole failed: {result:#x}")
        # The console owns these ends now.
        kernel32.CloseHandle(read_in)
        kernel32.CloseHandle(write_out)

        size = ctypes.c_size_t()
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        self._attributes = ctypes.create_string_buffer(size.value)
        if not kernel32.InitializeProcThreadAttributeList(self._attributes, 1, 0, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel32.UpdateProcThreadAttribute(self._attributes, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                                                  self._console, ctypes.sizeof(HPCON), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        startup = STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        # Empty std handles: otherwise a child of a redirected parent (a test
        # runner, bash) inherits those pipes and never talks to the console.
        startup.StartupInfo.dwFlags = 0x00000100  # STARTF_USESTDHANDLES
        startup.lpAttributeList = ctypes.addressof(self._attributes)
        block = None
        if env is not None:
            block = ctypes.create_unicode_buffer("".join(f"{k}={v}\0" for k, v in env.items()) + "\0")
        line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        info = PROCESS_INFORMATION()
        if not kernel32.CreateProcessW(None, line, None, None, False,
                                       EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT,
                                       block, cwd, ctypes.byref(startup), ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        kernel32.CloseHandle(info.hThread)
        self._process, self.pid = info.hProcess, info.dwProcessId
        self._raw = []
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        buffer = ctypes.create_string_buffer(65536)
        got = wt.DWORD()
        while kernel32.ReadFile(self._read_out, buffer, len(buffer), ctypes.byref(got), None) and got.value:
            with self._lock:
                self._raw.append(buffer.raw[:got.value].decode("utf-8", "replace"))

    def output(self):
        """Everything printed so far, with escape sequences removed."""
        with self._lock:
            return strip_ansi("".join(self._raw))

    screen = output

    def mark(self):
        return len(self.output())

    def type(self, text, delay=0.0):
        """Sends keystrokes (\\r is Enter, \\x1b is Escape). `delay` between characters."""
        for chunk in (text if delay else [text]):
            data = chunk.encode("utf-8")
            written = wt.DWORD()
            kernel32.WriteFile(self._write_in, data, len(data), ctypes.byref(written), None)
            if delay:
                time.sleep(delay)

    def wait_for(self, needle, timeout=30, after=0):
        """Waits until `needle` (str or compiled regex) is in the output after
        position `after`; returns the match position, or None on timeout."""
        end = time.time() + timeout
        while True:
            text = self.output()[after:]
            found = needle.search(text) if hasattr(needle, "search") else (needle in text)
            if found:
                return after + (found.start() if hasattr(found, "start") else text.index(needle))
            if time.time() > end:
                return None
            time.sleep(0.05)

    def exit_code(self):
        """The exit code, or None while it runs."""
        code = wt.DWORD()
        kernel32.GetExitCodeProcess(self._process, ctypes.byref(code))
        return None if code.value == 259 else code.value

    def wait_exit(self, timeout=30):
        """Waits for the program to end and for its last output to arrive."""
        kernel32.WaitForSingleObject(self._process, int(timeout * 1000))
        self.settle()
        return self.exit_code()

    def settle(self, quiet=0.25, timeout=5):
        """Waits until no new output has come for `quiet` seconds."""
        end, last, since = time.time() + timeout, -1, time.time()
        while time.time() < end:
            with self._lock:
                size = sum(map(len, self._raw))
            if size != last:
                last, since = size, time.time()
            elif time.time() - since >= quiet:
                return
            time.sleep(0.05)

    def close(self):
        if self.exit_code() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.pid)], capture_output=True)
        kernel32.ClosePseudoConsole(self._console)
        kernel32.CloseHandle(self._write_in)
        kernel32.CloseHandle(self._process)
        kernel32.DeleteProcThreadAttributeList(self._attributes)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    cwd = None
    if args[:1] == ["--cwd"]:
        cwd, args = args[1], args[2:]
    if args[:1] == ["--"]:
        args = args[1:]
    if not args:
        sys.exit(__doc__)
    with Terminal(args, cwd=cwd, env=dict(os.environ)) as term:
        code = term.wait_exit(60)
        print(term.output())
        print(f"[exit {code}]")
