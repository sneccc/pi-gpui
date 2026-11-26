//! One app per user on Linux. macOS routes `pi-gpui://` links and reopening to
//! the running app by itself; on Linux every launch (from the dock, a
//! `.desktop` link handler or a terminal) is a new process. The first one
//! listens on a socket in `$XDG_RUNTIME_DIR`; later ones hand it their link
//! and exit.

#[cfg(unix)]
use std::io::{BufRead as _, BufReader, Write as _};
#[cfg(unix)]
use std::os::unix::net::{UnixListener, UnixStream};
#[cfg(unix)]
use std::path::PathBuf;

#[cfg(windows)]
pub use windows::{ACTIVATE, forward, listen};

/// Windows: the first launch listens on a named pipe; later ones write their
/// link to it and exit. The pipe is named after the app data folder, so one
/// user gets one app (and a test with its own `HOME` gets its own).
#[cfg(windows)]
mod windows {
    use std::ffi::c_void;
    use std::io::Write as _;
    use std::os::windows::ffi::OsStrExt as _;
    use std::path::Path;
    use std::time::Duration;

    /// Sent when the app is launched again without a link: bring it forward.
    pub const ACTIVATE: &str = "activate";

    /// A message is one line; anything longer is cut.
    const MAX_MESSAGE: usize = 64 * 1024;

    const ERROR_PIPE_BUSY: i32 = 231;
    const ERROR_PIPE_CONNECTED: u32 = 535;
    /// The client connected, wrote and closed before we called ConnectNamedPipe.
    /// Its data is still in the pipe.
    const ERROR_NO_DATA: u32 = 232;

    const PIPE_ACCESS_INBOUND: u32 = 0x0000_0001;
    const FILE_FLAG_FIRST_PIPE_INSTANCE: u32 = 0x0008_0000;
    const PIPE_TYPE_BYTE: u32 = 0x0;
    const PIPE_READMODE_BYTE: u32 = 0x0;
    const PIPE_WAIT: u32 = 0x0;
    const PIPE_REJECT_REMOTE_CLIENTS: u32 = 0x8;
    const INVALID_HANDLE_VALUE: isize = -1;

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn CreateNamedPipeW(
            name: *const u16,
            open_mode: u32,
            pipe_mode: u32,
            max_instances: u32,
            out_buffer: u32,
            in_buffer: u32,
            default_timeout: u32,
            security: *const c_void,
        ) -> isize;
        fn ConnectNamedPipe(pipe: isize, overlapped: *mut c_void) -> i32;
        fn DisconnectNamedPipe(pipe: isize) -> i32;
        fn ReadFile(
            file: isize,
            buffer: *mut c_void,
            length: u32,
            read: *mut u32,
            overlapped: *mut c_void,
        ) -> i32;
        fn GetLastError() -> u32;
    }

    #[link(name = "user32")]
    unsafe extern "system" {
        fn AllowSetForegroundWindow(process_id: u32) -> i32;
    }

    /// `\\.\pipe\pi-gpui-<hash>`, one per app data folder.
    pub(super) fn pipe_name(data_directory: &Path) -> String {
        // FNV-1a: stable across runs, unlike std's randomly seeded hasher.
        let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
        for unit in data_directory
            .as_os_str()
            .to_string_lossy()
            .to_lowercase()
            .encode_utf16()
        {
            for byte in unit.to_le_bytes() {
                hash = (hash ^ u64::from(byte)).wrapping_mul(0x0000_0100_0000_01b3);
            }
        }
        format!(r"\\.\pipe\pi-gpui-{hash:016x}")
    }

    fn app_pipe_name() -> Option<String> {
        Some(pipe_name(&crate::sessions::app_data_directory()?))
    }

    /// Hands `message` to an app that is already running. `true` when it took
    /// it, so this process should exit.
    pub fn forward(message: &str) -> bool {
        app_pipe_name().is_some_and(|name| forward_to(&name, message))
    }

    /// Listens for later launches; each message goes to `deliver` on a
    /// background thread.
    pub fn listen(deliver: impl Fn(String) + Send + 'static) {
        let Some(name) = app_pipe_name() else {
            return;
        };
        if let Err(error) = listen_on(&name, deliver) {
            eprintln!("pi-gpui: cannot listen on {name}: {error}");
        }
    }

    pub(super) fn forward_to(name: &str, message: &str) -> bool {
        // This process was just started by the user, so it may let the app
        // that is already running come to the front.
        // SAFETY: ASFW_ANY (-1 as a DWORD) takes no pointer.
        unsafe { AllowSetForegroundWindow(u32::MAX) };
        let line = format!("{}\n", message.replace(['\n', '\r'], " "));
        // The pipe has one instance: while it serves one client, another waits.
        for _ in 0..40 {
            match std::fs::OpenOptions::new().write(true).open(name) {
                Ok(mut pipe) => return pipe.write_all(line.as_bytes()).is_ok(),
                Err(error) if error.raw_os_error() == Some(ERROR_PIPE_BUSY) => {
                    std::thread::sleep(Duration::from_millis(50));
                }
                // Not found: nobody is listening.
                Err(_) => return false,
            }
        }
        false
    }

    pub(super) fn listen_on(
        name: &str,
        deliver: impl Fn(String) + Send + 'static,
    ) -> Result<(), String> {
        let wide: Vec<u16> = std::ffi::OsStr::new(name)
            .encode_wide()
            .chain(Some(0))
            .collect();
        // SAFETY: `wide` is nul-terminated and outlives the call. No security
        // descriptor: the default lets only this user (and admins) write.
        let pipe = unsafe {
            CreateNamedPipeW(
                wide.as_ptr(),
                PIPE_ACCESS_INBOUND | FILE_FLAG_FIRST_PIPE_INSTANCE,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                1,
                0,
                MAX_MESSAGE as u32,
                0,
                std::ptr::null(),
            )
        };
        if pipe == INVALID_HANDLE_VALUE {
            // SAFETY: reads the calling thread's last error.
            return Err(format!("CreateNamedPipe failed ({})", unsafe {
                GetLastError()
            }));
        }
        std::thread::spawn(move || {
            loop {
                // SAFETY: `pipe` stays valid for the life of the process.
                let connected = unsafe {
                    ConnectNamedPipe(pipe, std::ptr::null_mut()) != 0 || {
                        let error = GetLastError();
                        error == ERROR_PIPE_CONNECTED || error == ERROR_NO_DATA
                    }
                };
                if !connected {
                    // SAFETY: resets the pipe so the next client can connect.
                    unsafe { DisconnectNamedPipe(pipe) };
                    std::thread::sleep(Duration::from_millis(100));
                    continue;
                }
                let mut data = Vec::new();
                let mut chunk = [0u8; 4096];
                loop {
                    let mut read = 0u32;
                    // SAFETY: `chunk` is 4096 bytes.
                    let ok = unsafe {
                        ReadFile(
                            pipe,
                            chunk.as_mut_ptr().cast(),
                            chunk.len() as u32,
                            &mut read,
                            std::ptr::null_mut(),
                        )
                    };
                    // A client that has closed still leaves its data to read.
                    data.extend_from_slice(&chunk[..read as usize]);
                    if ok == 0 || read == 0 || data.len() >= MAX_MESSAGE || data.contains(&b'\n') {
                        break;
                    }
                }
                // SAFETY: ends this client's connection; the next one reuses the pipe.
                unsafe { DisconnectNamedPipe(pipe) };
                data.truncate(MAX_MESSAGE);
                let text = String::from_utf8_lossy(&data);
                let message = text.lines().next().unwrap_or("").trim();
                if !message.is_empty() {
                    deliver(message.to_string());
                }
            }
        });
        Ok(())
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::windows::{forward_to, listen_on, pipe_name};
    use std::path::Path;
    use std::sync::mpsc;
    use std::time::Duration;

    /// A pipe name no other test (or running app) uses.
    fn unique(label: &str) -> String {
        use std::sync::atomic::{AtomicU32, Ordering};
        static NEXT: AtomicU32 = AtomicU32::new(0);
        format!(
            r"\\.\pipe\pi-gpui-test-{}-{}-{label}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        )
    }

    fn listener(name: &str) -> mpsc::Receiver<String> {
        let (sender, receiver) = mpsc::channel();
        listen_on(name, move |message| {
            sender.send(message).ok();
        })
        .expect("listen");
        receiver
    }

    fn next(receiver: &mpsc::Receiver<String>) -> String {
        receiver
            .recv_timeout(Duration::from_secs(5))
            .expect("a message arrives")
    }

    #[test]
    fn a_message_reaches_the_listener() {
        let name = unique("one");
        let received = listener(&name);
        assert!(forward_to(&name, "pi-gpui://open?workspace=C:/my%20dir"));
        assert_eq!(next(&received), "pi-gpui://open?workspace=C:/my%20dir");
    }

    #[test]
    fn a_client_that_wrote_and_closed_before_the_listener_ran_is_not_lost() {
        // Create the pipe, let a client write and close, and only then start
        // serving: the same order a fast second launch can produce.
        for round in 0..20 {
            let name = unique(&format!("early{round}"));
            let (sender, receiver) = mpsc::channel();
            listen_on(&name, move |message| {
                sender.send(message).ok();
            })
            .expect("listen");
            assert!(forward_to(&name, &format!("early {round}")));
            assert_eq!(next(&receiver), format!("early {round}"));
            // and the pipe still works afterwards
            assert!(forward_to(&name, "later"));
            assert_eq!(next(&receiver), "later");
        }
    }

    #[test]
    fn forward_reports_false_when_nobody_listens() {
        assert!(!forward_to(&unique("nobody"), "activate"));
    }

    #[test]
    fn messages_arrive_in_order_one_after_another() {
        let name = unique("order");
        let received = listener(&name);
        for number in 0..25 {
            assert!(forward_to(&name, &format!("message {number}")));
        }
        for number in 0..25 {
            assert_eq!(next(&received), format!("message {number}"));
        }
    }

    #[test]
    fn simultaneous_launches_all_get_through() {
        let name = unique("parallel");
        let received = listener(&name);
        let senders: Vec<_> = (0..6)
            .map(|sender| {
                let name = name.clone();
                std::thread::spawn(move || {
                    (0..8).all(|number| forward_to(&name, &format!("{sender}:{number}")))
                })
            })
            .collect();
        for sender in senders {
            assert!(
                sender.join().unwrap(),
                "a launch could not hand over its link"
            );
        }
        let mut all: Vec<String> = (0..48).map(|_| next(&received)).collect();
        all.sort();
        let mut expected: Vec<String> = (0..6)
            .flat_map(|sender| (0..8).map(move |number| format!("{sender}:{number}")))
            .collect();
        expected.sort();
        assert_eq!(all, expected);
    }

    #[test]
    fn unicode_and_long_messages_survive() {
        let name = unique("long");
        let received = listener(&name);
        let unicode = "pi-gpui://open?workspace=C:/caf\u{e9}/\u{4e2d}\u{6587}/\u{1f600}";
        assert!(forward_to(&name, unicode));
        assert_eq!(next(&received), unicode);
        let long = format!("pi-gpui://open?session={}", "x".repeat(20_000));
        assert!(forward_to(&name, &long));
        assert_eq!(next(&received), long);
    }

    #[test]
    fn a_message_is_one_line() {
        let name = unique("lines");
        let received = listener(&name);
        assert!(forward_to(&name, "first\nsecond"));
        assert_eq!(next(&received), "first second");
    }

    #[test]
    fn a_second_listener_on_the_same_pipe_is_refused() {
        let name = unique("twice");
        let _first = listener(&name);
        let second = listen_on(&name, |_| {});
        assert!(second.is_err(), "two apps must not both listen");
    }

    #[test]
    fn pipe_names_depend_on_the_data_folder_only() {
        let a = pipe_name(Path::new(r"C:\Users\me\.config\pi-gpui"));
        assert_eq!(a, pipe_name(Path::new(r"C:\Users\me\.config\pi-gpui")));
        assert_eq!(
            a,
            pipe_name(Path::new(r"c:\users\ME\.config\pi-gpui")),
            "Windows paths ignore case"
        );
        assert_ne!(a, pipe_name(Path::new(r"C:\Users\other\.config\pi-gpui")));
        assert!(a.starts_with(r"\\.\pipe\pi-gpui-"));
        assert!(a.len() < 100);
    }
}

/// Sent when the app is launched again without a link: bring it forward.
#[cfg(unix)]
pub const ACTIVATE: &str = "activate";

/// Hands `message` to an app that is already running. `true` when it took
/// it, so this process should exit.
#[cfg(unix)]
pub fn forward(message: &str) -> bool {
    let Some(path) = socket_path() else {
        return false;
    };
    let Ok(mut stream) = UnixStream::connect(&path) else {
        return false;
    };
    stream.write_all(format!("{message}\n").as_bytes()).is_ok()
}

/// Listens for later launches; each message goes to `deliver` on a
/// background thread.
#[cfg(unix)]
pub fn listen(deliver: impl Fn(String) + Send + 'static) {
    let Some(path) = socket_path() else {
        return;
    };
    // `forward` found nobody listening, so a file left here is stale.
    let _ = std::fs::remove_file(&path);
    let listener = match UnixListener::bind(&path) {
        Ok(listener) => listener,
        Err(error) => {
            eprintln!("pi-gpui: cannot listen on {}: {error}", path.display());
            return;
        }
    };
    std::thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            let mut line = String::new();
            if BufReader::new(stream).read_line(&mut line).is_ok() {
                let message = line.trim().to_string();
                if !message.is_empty() {
                    deliver(message);
                }
            }
        }
    });
}

#[cfg(unix)]
fn socket_path() -> Option<PathBuf> {
    let runtime = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .filter(|path| path.is_absolute() && path.is_dir())?;
    // Development builds keep to themselves, so `cargo run` works while the
    // installed app is open.
    Some(runtime.join(if cfg!(debug_assertions) {
        "pi-gpui-dev.sock"
    } else {
        "pi-gpui.sock"
    }))
}
