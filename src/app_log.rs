//! The app log, `<app cache>/logs/pi-gpui.log`: everything the app writes to
//! stderr (warnings and errors, the notices it shows, plugin and pi
//! problems, panics) with the local time on each line.
//!
//! A Windows GUI app has no stderr, so before this all of it was lost when
//! the app ran normally. `init` puts a pipe in place of stderr and a thread
//! copies each line to the log file and to wherever stderr pointed before (a
//! terminal, or the file a test captures), so nothing changes for them. The
//! log rotates at `MAX_BYTES`, keeping one older file.

use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};

const MAX_BYTES: u64 = 5 * 1024 * 1024;

static LOG: OnceLock<Mutex<File>> = OnceLock::new();

/// Where the log is written.
pub fn path() -> Option<PathBuf> {
    crate::sessions::app_cache_directory().map(|cache| cache.join("logs").join("pi-gpui.log"))
}

/// `2026-11-27 14:18:51.123`, local time.
fn timestamp() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let seconds = now.as_secs() as libc::time_t;
    // SAFETY: localtime_r / localtime_s only write into the struct we pass.
    let tm = unsafe {
        let mut tm: libc::tm = std::mem::zeroed();
        #[cfg(unix)]
        libc::localtime_r(&seconds, &mut tm);
        #[cfg(windows)]
        libc::localtime_s(&mut tm, &seconds);
        tm
    };
    format!(
        "{}-{:02}-{:02} {:02}:{:02}:{:02}.{:03}",
        1900 + tm.tm_year,
        tm.tm_mon + 1,
        tm.tm_mday,
        tm.tm_hour,
        tm.tm_min,
        tm.tm_sec,
        now.subsec_millis()
    )
}

/// Appends one line, timed, to the log file (not to stderr).
fn write_line(line: &str) {
    if let Some(log) = LOG.get()
        && let Ok(mut file) = log.lock()
    {
        let _ = writeln!(
            file,
            "{} {}",
            timestamp(),
            line.trim_end_matches(['\r', '\n'])
        );
    }
}

/// Opens the log and starts copying stderr into it. Call once, early in main.
/// Does nothing (and the app runs as before) when the log cannot be opened.
pub fn init() {
    let Some(path) = path() else {
        return;
    };
    let Some(folder) = path.parent() else {
        return;
    };
    if std::fs::create_dir_all(folder).is_err() {
        return;
    }
    if std::fs::metadata(&path).is_ok_and(|meta| meta.len() > MAX_BYTES) {
        let _ = std::fs::rename(&path, path.with_extension("1.log"));
    }
    let Ok(file) = OpenOptions::new().create(true).append(true).open(&path) else {
        return;
    };
    if LOG.set(Mutex::new(file)).is_err() {
        return;
    }
    install_panic_hook();
    let Some((reader, mut previous)) = redirect_stderr() else {
        write_line("stderr could not be captured; only panics are logged");
        return;
    };
    std::thread::Builder::new()
        .name("app-log".into())
        .spawn(move || {
            let mut reader = BufReader::new(reader);
            let mut line = Vec::new();
            while reader
                .read_until(b'\n', &mut line)
                .is_ok_and(|read| read > 0)
            {
                if let Some(previous) = previous.as_mut() {
                    let _ = previous.write_all(&line);
                    let _ = previous.flush();
                }
                write_line(&String::from_utf8_lossy(&line));
                line.clear();
            }
        })
        .ok();
    write_line(&format!(
        "Pi GPUI {} started (pid {}, {} {}), args {:?}",
        env!("CARGO_PKG_VERSION"),
        std::process::id(),
        std::env::consts::OS,
        std::env::consts::ARCH,
        std::env::args().skip(1).collect::<Vec<_>>()
    ));
}

/// Panics go to the log directly, with a backtrace: the copying thread may
/// not get to run before the process ends.
fn install_panic_hook() {
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let backtrace = std::backtrace::Backtrace::force_capture();
        write_line(&format!("PANIC {info}\n{backtrace}"));
        previous(info);
    }));
}

/// The last `max_lines` lines of the log, for session traces.
pub fn tail(max_lines: usize) -> Option<String> {
    let mut text = String::new();
    File::open(path()?).ok()?.read_to_string(&mut text).ok()?;
    let lines: Vec<&str> = text.lines().collect();
    Some(lines[lines.len().saturating_sub(max_lines)..].join("\n"))
}

/// Replaces stderr with a pipe. Returns its read end and where stderr went
/// before (if anywhere).
#[cfg(windows)]
fn redirect_stderr() -> Option<(File, Option<File>)> {
    use std::os::windows::io::FromRawHandle;
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn CreatePipe(read: *mut isize, write: *mut isize, attributes: *const u8, size: u32)
        -> i32;
        fn GetStdHandle(kind: u32) -> isize;
        fn SetStdHandle(kind: u32, handle: isize) -> i32;
    }
    const STD_ERROR_HANDLE: u32 = -12i32 as u32;
    let (mut read, mut write) = (0isize, 0isize);
    // SAFETY: plain kernel32 calls with valid out pointers. The handles are
    // owned by the returned Files, except the pipe's write end, which stays
    // in place as this process's stderr until it exits.
    unsafe {
        if CreatePipe(&mut read, &mut write, std::ptr::null(), 0) == 0 {
            return None;
        }
        let previous = GetStdHandle(STD_ERROR_HANDLE);
        if SetStdHandle(STD_ERROR_HANDLE, write) == 0 {
            return None;
        }
        let previous =
            (previous != 0 && previous != -1).then(|| File::from_raw_handle(previous as _));
        Some((File::from_raw_handle(read as _), previous))
    }
}

#[cfg(unix)]
fn redirect_stderr() -> Option<(File, Option<File>)> {
    use std::os::unix::io::FromRawFd;
    let mut fds = [0; 2];
    // SAFETY: plain libc calls on descriptors this function owns; fd 2 is
    // replaced by the pipe's write end and the old one kept as `previous`.
    unsafe {
        if libc::pipe(fds.as_mut_ptr()) != 0 {
            return None;
        }
        let previous = libc::dup(2);
        if libc::dup2(fds[1], 2) < 0 {
            return None;
        }
        libc::close(fds[1]);
        let previous = (previous >= 0).then(|| File::from_raw_fd(previous));
        Some((File::from_raw_fd(fds[0]), previous))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn timestamps_are_local_date_and_time_with_milliseconds() {
        let stamp = timestamp();
        // 2026-11-27 14:18:51.123
        assert_eq!(stamp.len(), 23, "{stamp}");
        assert_eq!(&stamp[4..5], "-");
        assert_eq!(&stamp[10..11], " ");
        assert_eq!(&stamp[19..20], ".");
    }
}
