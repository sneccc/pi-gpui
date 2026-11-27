//! Read-only access to pi's session files: where they live, short summaries for
//! the sidebar and pi-gui's single-writer leases. Never writes into pi's data
//! (the leases of our own pi processes are written by the bridge extension).
//!
//! All functions block on the filesystem; call them from a background task.

use std::collections::HashSet;
use std::fs::{self, File};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
#[cfg(not(windows))]
use std::process::{Command, Stdio};
use std::time::{Duration, SystemTime};

use serde_json::Value;

/// pi-gui refreshes its leases every minute and treats them as stale after five.
const LEASE_TTL: Duration = Duration::from_secs(5 * 60);
const TITLE_MAX_CHARS: usize = 80;

#[derive(Clone, Debug)]
pub struct SessionSummary {
    pub path: PathBuf,
    pub title: String,
    pub modified: SystemTime,
}

/// The most recently modified sessions whose header `cwd` is `workspace`.
/// Sessions without a user message and without a name are skipped: pi creates
/// them, but there is nothing to resume.
pub fn list_sessions(workspace: &Path, limit: usize) -> Vec<SessionSummary> {
    let mut seen = HashSet::new();
    let mut candidates = Vec::new();
    for directory in session_directories(workspace) {
        let Ok(entries) = fs::read_dir(&directory) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path
                .extension()
                .is_none_or(|extension| extension != "jsonl")
            {
                continue;
            }
            let Ok(modified) = entry.metadata().and_then(|metadata| metadata.modified()) else {
                continue;
            };
            if seen.insert(path.clone()) {
                candidates.push((path, modified));
            }
        }
    }
    candidates.sort_by(|left, right| right.1.cmp(&left.1));

    candidates
        .into_iter()
        .filter_map(|(path, modified)| {
            let title = read_title(&path, workspace)?;
            Some(SessionSummary {
                path,
                title,
                modified,
            })
        })
        .take(limit)
        .collect()
}

/// Mirrors pi's lookup order: `PI_CODING_AGENT_SESSION_DIR`, the project and
/// global `sessionDir` settings, then `<agent dir>/sessions`. Sessions are
/// normally grouped in a per-workspace subdirectory; a custom directory may be
/// flat, so both locations are scanned and filtered by the header `cwd`.
pub fn session_directories(workspace: &Path) -> Vec<PathBuf> {
    let agent_directory = std::env::var_os("PI_CODING_AGENT_DIR")
        .map(PathBuf::from)
        .or_else(|| home_directory().map(|home| home.join(".pi/agent")));

    let base = std::env::var_os("PI_CODING_AGENT_SESSION_DIR")
        .map(PathBuf::from)
        .or_else(|| setting_session_dir(&workspace.join(".pi/settings.json")))
        .or_else(|| {
            agent_directory
                .as_ref()
                .and_then(|directory| setting_session_dir(&directory.join("settings.json")))
        })
        .or_else(|| agent_directory.map(|directory| directory.join("sessions")));

    let Some(base) = base else {
        return Vec::new();
    };
    let base = if base.is_relative() {
        workspace.join(base)
    } else {
        base
    };
    vec![base.join(encode_workspace(workspace)), base]
}

fn setting_session_dir(settings_path: &Path) -> Option<PathBuf> {
    let settings: Value = serde_json::from_slice(&fs::read(settings_path).ok()?).ok()?;
    settings["sessionDir"].as_str().map(PathBuf::from)
}

/// `/Users/me/project` → `--Users-me-project--`, as documented in pi's session format.
fn encode_workspace(workspace: &Path) -> String {
    let path = workspace.to_string_lossy();
    let path = strip_verbatim(&path);
    let trimmed = path.trim_start_matches(['/', '\\']);
    format!("--{}--", trimmed.replace(['/', '\\', ':'], "-"))
}

/// Session name if one was set, otherwise the first user message. Only lines
/// that can matter are parsed as JSON, so large sessions stay cheap to scan.
fn read_title(path: &Path, workspace: &Path) -> Option<String> {
    let mut reader = BufReader::new(File::open(path).ok()?);
    let mut line = Vec::new();

    reader.read_until(b'\n', &mut line).ok()?;
    let header: Value = serde_json::from_slice(trim_line(&line)).ok()?;
    if header["type"] != "session" || !same_directory(header["cwd"].as_str()?, workspace) {
        return None;
    }

    let mut first_user_text: Option<String> = None;
    let mut session_name: Option<String> = None;
    loop {
        line.clear();
        match reader.read_until(b'\n', &mut line) {
            Ok(0) | Err(_) => break,
            Ok(_) => {}
        }
        let text = trim_line(&line);
        let is_session_info = contains(text, br#""type":"session_info""#);
        let is_user_message = first_user_text.is_none() && contains(text, br#""role":"user""#);
        if !is_session_info && !is_user_message {
            continue;
        }
        let Ok(entry) = serde_json::from_slice::<Value>(text) else {
            continue;
        };
        if entry["type"] == "session_info" {
            // The latest entry wins; an absent name clears an earlier one.
            session_name = entry["name"]
                .as_str()
                .map(str::trim)
                .filter(|name| !name.is_empty())
                .map(str::to_string);
        } else if entry["type"] == "message" && entry["message"]["role"] == "user" {
            first_user_text = Some(message_text(&entry["message"]));
        }
    }

    let title = session_name.or(first_user_text)?;
    Some(one_line(
        &crate::attachments::display_text(&title),
        TITLE_MAX_CHARS,
    ))
}

/// The sidebar title of a session file whose header `cwd` is `workspace`.
pub fn session_title(path: &Path, workspace: &Path) -> Option<String> {
    read_title(path, workspace)
}

/// Who else holds `session` open, according to pi-gui's lease convention.
/// pi itself does not prevent two writers, so this is the only signal we get.
/// Our own pi processes hold leases too (the bridge extension, marked with
/// our pid as `appPid`); those do not count. A terminal pi with the pi-gpui
/// package holds one as "a terminal pi".
pub fn foreign_lease_holder(session: &Path) -> Option<String> {
    let lease_path = PathBuf::from(format!("{}.lease", session.display()));
    let age = fs::metadata(&lease_path)
        .and_then(|metadata| metadata.modified())
        .ok()?
        .elapsed()
        .unwrap_or_default();
    if age > LEASE_TTL {
        return None;
    }
    let lease: Value = serde_json::from_slice(&fs::read(&lease_path).ok()?).ok()?;
    lease_holder(&lease, u64::from(std::process::id()))
}

/// How far a lease's `startedAt` may be from the process's real start: node
/// takes it at start-up, a little after the process was created.
const START_TOLERANCE_MS: u64 = 10_000;

/// The holder named by a lease, unless it is one of our own pi processes or
/// its process is gone (or the pid now belongs to a newer process).
fn lease_holder(lease: &Value, own_pid: u64) -> Option<String> {
    if lease["appPid"].as_u64() == Some(own_pid) {
        return None;
    }
    if let Some(pid) = lease["pid"].as_u64() {
        if !process_is_alive(pid) {
            return None;
        }
        if let (Some(claimed), Some(actual)) = (lease["startedAt"].as_u64(), process_start_ms(pid))
            && claimed.abs_diff(actual) > START_TOLERANCE_MS
        {
            return None;
        }
    }
    Some(
        lease["surface"]
            .as_str()
            .unwrap_or("another app")
            .to_string(),
    )
}

/// When process `pid` started, in ms since the Unix epoch. Windows only:
/// there pids come back into use quickly, so a lease's pid alone can point at
/// an unrelated process. Elsewhere `None` (the check is skipped).
#[cfg(windows)]
fn process_start_ms(pid: u64) -> Option<u64> {
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn OpenProcess(access: u32, inherit: i32, pid: u32) -> isize;
        fn GetProcessTimes(
            process: isize,
            creation: *mut u64,
            exit: *mut u64,
            kernel: *mut u64,
            user: *mut u64,
        ) -> i32;
        fn CloseHandle(handle: isize) -> i32;
    }
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    /// 100 ns ticks between 1601-01-01 (FILETIME) and 1970-01-01.
    const UNIX_EPOCH_TICKS: u64 = 116_444_736_000_000_000;

    let pid = u32::try_from(pid).ok()?;
    // SAFETY: plain kernel32 calls with valid out pointers; the handle is
    // closed before returning.
    unsafe {
        let process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if process == 0 {
            return None;
        }
        let (mut creation, mut exit, mut kernel, mut user) = (0, 0, 0, 0);
        let ok = GetProcessTimes(process, &mut creation, &mut exit, &mut kernel, &mut user) != 0;
        CloseHandle(process);
        ok.then(|| creation.saturating_sub(UNIX_EPOCH_TICKS) / 10_000)
    }
}

#[cfg(not(windows))]
fn process_start_ms(_pid: u64) -> Option<u64> {
    None
}

/// Whether a process with this id is running (Windows: it has not exited).
#[cfg(windows)]
fn process_is_alive(pid: u64) -> bool {
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn OpenProcess(access: u32, inherit: i32, pid: u32) -> isize;
        fn GetExitCodeProcess(process: isize, code: *mut u32) -> i32;
        fn CloseHandle(handle: isize) -> i32;
        fn GetLastError() -> u32;
    }
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    const STILL_ACTIVE: u32 = 259;
    const ERROR_ACCESS_DENIED: u32 = 5;

    let Ok(pid) = u32::try_from(pid) else {
        return false;
    };
    // SAFETY: plain kernel32 calls; the handle is closed before returning.
    unsafe {
        let process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if process == 0 {
            // Another user's process exists but cannot be opened.
            return GetLastError() == ERROR_ACCESS_DENIED;
        }
        let mut code = 0;
        let alive = GetExitCodeProcess(process, &mut code) != 0 && code == STILL_ACTIVE;
        CloseHandle(process);
        alive
    }
}

#[cfg(not(windows))]
fn process_is_alive(pid: u64) -> bool {
    Command::new("kill")
        .args(["-0", &pid.to_string()])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

pub fn message_text(message: &Value) -> String {
    if let Some(text) = message["content"].as_str() {
        return text.to_string();
    }
    message["content"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|block| block["text"].as_str())
        .collect::<Vec<_>>()
        .join("\n")
}

pub fn one_line(text: &str, limit: usize) -> String {
    let line = text
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or_default();
    match line.char_indices().nth(limit) {
        Some((index, _)) => format!("{}…", &line[..index]),
        None => line.to_string(),
    }
}

/// "now", "5m", "3h", "2d".
pub fn relative_age(modified: SystemTime) -> String {
    let seconds = modified.elapsed().map_or(0, |elapsed| elapsed.as_secs());
    match seconds {
        0..60 => "now".to_string(),
        60..3_600 => format!("{}m", seconds / 60),
        3_600..86_400 => format!("{}h", seconds / 3_600),
        _ => format!("{}d", seconds / 86_400),
    }
}

/// `/Users/me/project` → `~/project`.
pub fn display_path(path: &Path) -> String {
    match home_directory() {
        Some(home) => match path.strip_prefix(&home) {
            Ok(relative_path) => format!("~/{}", relative_path.display()),
            Err(_) => path.display().to_string(),
        },
        None => path.display().to_string(),
    }
}

pub fn home_directory() -> Option<PathBuf> {
    // Windows has no HOME unless launched from a Unix-style shell.
    std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

/// The app's own files (settings, projects, plugins): `~/Library/Application
/// Support/pi-gpui` on macOS, `$XDG_CONFIG_HOME/pi-gpui` (`~/.config/pi-gpui`)
/// elsewhere.
pub fn app_data_directory() -> Option<PathBuf> {
    if cfg!(target_os = "macos") {
        return Some(home_directory()?.join("Library/Application Support/pi-gpui"));
    }
    Some(xdg_directory("XDG_CONFIG_HOME", ".config")?.join("pi-gpui"))
}

/// Downloads that can be thrown away: `~/Library/Caches/pi-gpui` on macOS,
/// `$XDG_CACHE_HOME/pi-gpui` (`~/.cache/pi-gpui`) elsewhere.
pub fn app_cache_directory() -> Option<PathBuf> {
    if cfg!(target_os = "macos") {
        return Some(home_directory()?.join("Library/Caches/pi-gpui"));
    }
    Some(xdg_directory("XDG_CACHE_HOME", ".cache")?.join("pi-gpui"))
}

/// An XDG base directory; relative values are ignored, as the spec says.
fn xdg_directory(variable: &str, fallback: &str) -> Option<PathBuf> {
    std::env::var_os(variable)
        .map(PathBuf::from)
        .filter(|path| path.is_absolute())
        .or_else(|| Some(home_directory()?.join(fallback)))
}

/// `canonicalize` without Windows' `\\?\` prefix, which pi does not use in
/// session paths or `cwd`. Identical to `canonicalize` elsewhere.
pub fn canonical_path(path: &Path) -> std::io::Result<PathBuf> {
    let canonical = path.canonicalize()?;
    let plain = strip_verbatim(&canonical.to_string_lossy()).to_string();
    if canonical.to_string_lossy() == plain {
        Ok(canonical)
    } else {
        Ok(PathBuf::from(plain))
    }
}

/// `\\?\C:\dir` → `C:\dir`. Only drive paths: a verbatim UNC share
/// (`\\?\UNC\server\share`) or volume path has no plain form this simple.
fn strip_verbatim(path: &str) -> &str {
    match path.strip_prefix(r"\\?\") {
        Some(plain) if plain.as_bytes().get(1) == Some(&b':') => plain,
        _ => path,
    }
}

pub(crate) fn same_directory(cwd: &str, workspace: &Path) -> bool {
    let cwd = Path::new(cwd);
    cwd == workspace || canonical_path(cwd).is_ok_and(|cwd| cwd == workspace)
}

pub(crate) fn trim_line(line: &[u8]) -> &[u8] {
    let mut end = line.len();
    while end > 0 && matches!(line[end - 1], b'\n' | b'\r') {
        end -= 1;
    }
    &line[..end]
}

pub(crate) fn contains(haystack: &[u8], needle: &[u8]) -> bool {
    haystack
        .windows(needle.len())
        .any(|window| window == needle)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write as _;

    fn now_ms() -> u64 {
        SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .unwrap()
            .as_millis() as u64
    }

    #[test]
    fn a_live_lease_names_its_holder_but_our_own_pi_does_not_count() {
        let me = u64::from(std::process::id());
        let other = me + 1;
        let lease = serde_json::json!({ "pid": me, "surface": "a terminal pi" });
        assert_eq!(
            lease_holder(&lease, other).as_deref(),
            Some("a terminal pi")
        );
        let ours = serde_json::json!({ "pid": me, "surface": "Pi GPUI", "appPid": other });
        assert_eq!(lease_holder(&ours, other), None, "our own pi's lease");
        assert_eq!(
            lease_holder(&ours, other + 1).as_deref(),
            Some("Pi GPUI"),
            "another Pi GPUI's pi"
        );
        let unnamed = serde_json::json!({ "pid": me });
        assert_eq!(
            lease_holder(&unnamed, other).as_deref(),
            Some("another app")
        );
    }

    #[test]
    fn a_lease_of_a_dead_process_does_not_count() {
        let mut child = crate::process::command(if cfg!(windows) { "cmd" } else { "true" })
            .args(if cfg!(windows) {
                &["/c", "exit"][..]
            } else {
                &[][..]
            })
            .spawn()
            .unwrap();
        let pid = u64::from(child.id());
        child.wait().unwrap();
        let lease = serde_json::json!({ "pid": pid, "surface": "a terminal pi" });
        assert_eq!(lease_holder(&lease, 1), None);
    }

    #[cfg(windows)]
    #[test]
    fn a_reused_pid_does_not_keep_a_lease_alive() {
        let me = u64::from(std::process::id());
        let started = process_start_ms(me).expect("our own start time");
        assert!(started <= now_ms() && now_ms() - started < 24 * 3600 * 1000);
        let fresh = serde_json::json!({ "pid": me, "surface": "a terminal pi", "startedAt": started + 300 });
        assert_eq!(lease_holder(&fresh, 1).as_deref(), Some("a terminal pi"));
        // Written by a process that started long before ours, under the same pid.
        let reused = serde_json::json!({ "pid": me, "surface": "a terminal pi", "startedAt": started - 3_600_000 });
        assert_eq!(lease_holder(&reused, 1), None);
    }

    #[test]
    fn foreign_lease_holder_reads_the_lease_beside_the_session() {
        let folder = crate::test_support::TempDir::new("lease");
        let session = folder.join("s.jsonl");
        fs::write(&session, "{}\n").unwrap();
        assert_eq!(foreign_lease_holder(&session), None, "no lease file");
        let lease = serde_json::json!({ "pid": std::process::id(), "surface": "a terminal pi" });
        fs::write(folder.join("s.jsonl.lease"), lease.to_string()).unwrap();
        assert_eq!(
            foreign_lease_holder(&session).as_deref(),
            Some("a terminal pi")
        );
        let ours = serde_json::json!({ "pid": std::process::id(), "appPid": std::process::id() });
        fs::write(folder.join("s.jsonl.lease"), ours.to_string()).unwrap();
        assert_eq!(foreign_lease_holder(&session), None, "our own pi's lease");
    }

    #[cfg(windows)]
    #[test]
    fn canonical_path_has_no_verbatim_prefix() {
        let directory = std::env::temp_dir().canonicalize().expect("canonical");
        let plain = canonical_path(&directory).expect("canonical");
        assert!(!plain.to_string_lossy().starts_with(r"\\?\"));
        assert_eq!(canonical_path(&plain).expect("canonical"), plain);
        assert_eq!(
            encode_workspace(&directory),
            encode_workspace(&plain),
            "session folder names ignore the prefix"
        );
    }

    #[cfg(windows)]
    #[test]
    fn finds_live_and_dead_processes() {
        assert!(process_is_alive(u64::from(std::process::id())));
        let mut child = crate::process::command("cmd")
            .args(["/c", "exit 0"])
            .spawn()
            .expect("spawn");
        let pid = u64::from(child.id());
        child.wait().expect("wait");
        assert!(!process_is_alive(pid));
    }

    #[test]
    fn workspace_encoding_matches_pi() {
        assert_eq!(
            encode_workspace(Path::new("/Users/me/projects/pi-gpui")),
            "--Users-me-projects-pi-gpui--"
        );
    }

    #[test]
    fn title_prefers_latest_session_name() {
        let directory = std::env::temp_dir().join(format!("pi-gpui-test-{}", std::process::id()));
        fs::create_dir_all(&directory).expect("temp dir");
        let workspace = canonical_path(&directory).expect("canonical");
        let session = workspace.join("session.jsonl");
        let mut file = File::create(&session).expect("session file");
        // JSON escapes the backslashes of a Windows path.
        let header = serde_json::json!({
            "type": "session",
            "version": 3,
            "id": "x",
            "cwd": workspace.to_string_lossy(),
        });
        writeln!(file, "{header}").expect("write");
        writeln!(file, r#"{{"type":"message","id":"a","message":{{"role":"user","content":"  \nFirst question\nsecond line"}}}}"#).expect("write");
        drop(file);
        assert_eq!(
            read_title(&session, &workspace).as_deref(),
            Some("First question")
        );

        let mut file = fs::OpenOptions::new()
            .append(true)
            .open(&session)
            .expect("append");
        writeln!(
            file,
            r#"{{"type":"session_info","id":"b","name":"Refactoring"}}"#
        )
        .expect("write");
        drop(file);
        assert_eq!(
            read_title(&session, &workspace).as_deref(),
            Some("Refactoring")
        );

        assert_eq!(read_title(&session, Path::new("/elsewhere")), None);
        fs::remove_dir_all(&directory).ok();
    }

    #[test]
    fn windows_paths_encode_the_way_pi_names_session_folders() {
        // Seen on disk: ~/.pi/agent/sessions/--P--Projects-svg_experiments--
        assert_eq!(
            encode_workspace(Path::new(r"P:\Projects\svg_experiments")),
            "--P--Projects-svg_experiments--"
        );
        assert_eq!(
            encode_workspace(Path::new(r"C:\Users\Daniel")),
            "--C--Users-Daniel--"
        );
        // Forward slashes (as in `pi-gpui://` links) give the same name.
        assert_eq!(
            encode_workspace(Path::new("P:/Projects/svg_experiments")),
            "--P--Projects-svg_experiments--"
        );
    }

    #[test]
    fn the_verbatim_prefix_does_not_change_the_session_folder() {
        assert_eq!(
            encode_workspace(Path::new(r"\\?\P:\Projects\x")),
            encode_workspace(Path::new(r"P:\Projects\x")),
        );
    }

    #[cfg(windows)]
    #[test]
    fn the_same_directory_matches_across_prefix_and_case() {
        let directory = std::env::temp_dir().join(format!("pi-gpui-same-{}", std::process::id()));
        fs::create_dir_all(&directory).expect("temp dir");
        let workspace = canonical_path(&directory).expect("canonical");
        let text = workspace.to_string_lossy().to_string();
        // pi writes the cwd it was started in: plain, verbatim or differently cased.
        assert!(same_directory(&text, &workspace));
        assert!(same_directory(&format!(r"\\?\{text}"), &workspace));
        assert!(same_directory(&text.to_uppercase(), &workspace));
        assert!(same_directory(&text.replace('\\', "/"), &workspace));
        assert!(!same_directory(r"\elsewhere\else", &workspace));
        fs::remove_dir_all(&directory).ok();
    }

    #[test]
    fn strip_verbatim_only_touches_drive_paths() {
        assert_eq!(strip_verbatim(r"\\?\C:\dir\x"), r"C:\dir\x");
        assert_eq!(strip_verbatim(r"\\?\p:"), "p:");
        // Already plain, Unix, or relative: unchanged.
        assert_eq!(strip_verbatim(r"C:\dir"), r"C:\dir");
        assert_eq!(strip_verbatim("/home/me"), "/home/me");
        assert_eq!(strip_verbatim(""), "");
        // A UNC share or a volume GUID has no drive: stripping would break it.
        assert_eq!(
            strip_verbatim(r"\\?\UNC\server\share"),
            r"\\?\UNC\server\share"
        );
        assert_eq!(strip_verbatim(r"\\?\Volume{1234}\x"), r"\\?\Volume{1234}\x");
    }
}
