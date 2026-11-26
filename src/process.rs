//! Child processes.
//!
//! On Windows a GUI app that starts a console program (pi, git, curl) would
//! get a console window for each one, and pi itself is a `cmd.exe` wrapper
//! around `node.exe`: stopping the wrapper alone leaves node running.

use std::ffi::OsStr;
use std::process::{Child, Command};

/// `Command::new`, without a console window on Windows.
pub fn command(program: impl AsRef<OsStr>) -> Command {
    let command = Command::new(program);
    #[cfg(windows)]
    let command = {
        use std::os::windows::process::CommandExt as _;
        let mut command = command;
        command.creation_flags(windows::CREATE_NO_WINDOW);
        command
    };
    command
}

/// Stops the child and everything it started. On Windows `Child::kill` only
/// ends the process itself, and pi's `cmd.exe` wrapper would leave `node.exe`
/// behind.
pub fn kill_tree(child: &mut Child) -> std::io::Result<()> {
    #[cfg(windows)]
    {
        let id = child.id().to_string();
        // `taskkill /T` walks the tree; it fails when the process is already gone.
        let _ = command("taskkill").args(["/F", "/T", "/PID", &id]).output();
    }
    child.kill()
}

/// Starts a child that may outlive the app (an editor, Explorer). Children
/// normally die with the app (see `contain_children`); this one breaks away.
/// Where the app runs inside a job that forbids breakaway, it starts the
/// ordinary way instead.
pub fn spawn_outliving(command: &mut Command) -> std::io::Result<Child> {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt as _;
        command.creation_flags(windows::CREATE_BREAKAWAY_FROM_JOB | windows::CREATE_NO_WINDOW);
        if let Ok(child) = command.spawn() {
            return Ok(child);
        }
        command.creation_flags(windows::CREATE_NO_WINDOW);
    }
    command.spawn()
}

/// Makes every child die with the app, even when the app is killed or
/// crashes: the app joins a job object that kills its members when the last
/// handle to it closes, which the system does when the app's process ends.
/// Does nothing outside Windows, where a child sees its parent's pipes close.
pub fn contain_children() {
    #[cfg(windows)]
    if let Err(error) = windows::join_kill_on_close_job() {
        eprintln!("pi-gpui: children will outlive a crash: {error}");
    }
}

#[cfg(windows)]
mod windows {
    use std::ffi::c_void;

    pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    pub const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x0100_0000;

    const JOB_OBJECT_LIMIT_BREAKAWAY_OK: u32 = 0x0000_0800;
    const JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: u32 = 0x0000_2000;
    const JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: i32 = 9;

    #[repr(C)]
    #[derive(Default)]
    pub struct BasicLimits {
        per_process_user_time: i64,
        per_job_user_time: i64,
        limit_flags: u32,
        minimum_working_set: usize,
        maximum_working_set: usize,
        active_process_limit: u32,
        affinity: usize,
        priority_class: u32,
        scheduling_class: u32,
    }

    #[repr(C)]
    #[derive(Default)]
    pub struct ExtendedLimits {
        basic: BasicLimits,
        io_counters: [u64; 6],
        process_memory_limit: usize,
        job_memory_limit: usize,
        peak_process_memory_used: usize,
        peak_job_memory_used: usize,
    }

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn CreateJobObjectW(attributes: *const c_void, name: *const u16) -> isize;
        fn SetInformationJobObject(
            job: isize,
            class: i32,
            information: *const c_void,
            length: u32,
        ) -> i32;
        fn AssignProcessToJobObject(job: isize, process: isize) -> i32;
        fn GetCurrentProcess() -> isize;
        fn GetLastError() -> u32;
    }

    /// Puts this process into a new job that kills its members on close.
    pub fn join_kill_on_close_job() -> Result<(), String> {
        let mut limits = ExtendedLimits::default();
        limits.basic.limit_flags =
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK;
        // SAFETY: plain kernel32 calls. The job handle is never closed: it
        // lives as long as the process, and its closing is the point.
        unsafe {
            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if job == 0 {
                return Err(format!("CreateJobObject failed ({})", GetLastError()));
            }
            if SetInformationJobObject(
                job,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                (&raw const limits).cast(),
                size_of::<ExtendedLimits>() as u32,
            ) == 0
            {
                return Err(format!(
                    "SetInformationJobObject failed ({})",
                    GetLastError()
                ));
            }
            if AssignProcessToJobObject(job, GetCurrentProcess()) == 0 {
                return Err(format!(
                    "AssignProcessToJobObject failed ({})",
                    GetLastError()
                ));
            }
        }
        Ok(())
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use std::time::{Duration, Instant};

    #[repr(C)]
    struct Entry {
        size: u32,
        usage: u32,
        pid: u32,
        heap: usize,
        module: u32,
        threads: u32,
        parent: u32,
        priority: i32,
        flags: u32,
        exe: [u16; 260],
    }

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn CreateToolhelp32Snapshot(flags: u32, pid: u32) -> isize;
        fn Process32FirstW(snapshot: isize, entry: *mut Entry) -> i32;
        fn Process32NextW(snapshot: isize, entry: *mut Entry) -> i32;
        fn CloseHandle(handle: isize) -> i32;
    }

    /// `(pid, parent pid)` of every running process.
    fn processes() -> Vec<(u32, u32)> {
        let mut all = Vec::new();
        // SAFETY: a toolhelp snapshot walked with correctly sized entries.
        unsafe {
            let snapshot = CreateToolhelp32Snapshot(0x2, 0);
            let mut entry = Entry {
                size: size_of::<Entry>() as u32,
                usage: 0,
                pid: 0,
                heap: 0,
                module: 0,
                threads: 0,
                parent: 0,
                priority: 0,
                flags: 0,
                exe: [0; 260],
            };
            let mut more = Process32FirstW(snapshot, &mut entry);
            while more != 0 {
                all.push((entry.pid, entry.parent));
                more = Process32NextW(snapshot, &mut entry);
            }
            CloseHandle(snapshot);
        }
        all
    }

    fn children_of(parent: u32) -> Vec<u32> {
        processes()
            .into_iter()
            .filter(|&(_, p)| p == parent)
            .map(|(pid, _)| pid)
            .collect()
    }

    fn alive(pid: u32) -> bool {
        processes().iter().any(|&(p, _)| p == pid)
    }

    fn wait_until(mut done: impl FnMut() -> bool) -> bool {
        let end = Instant::now() + Duration::from_secs(10);
        while Instant::now() < end {
            if done() {
                return true;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        done()
    }

    #[test]
    fn job_limit_struct_has_the_size_windows_expects() {
        // JOBOBJECT_EXTENDED_LIMIT_INFORMATION is 144 bytes on x64 (112 on x86).
        let expected = if cfg!(target_pointer_width = "64") {
            144
        } else {
            112
        };
        assert_eq!(size_of::<windows::ExtendedLimits>(), expected);
    }

    #[test]
    fn kill_tree_stops_the_grandchildren_too() {
        // cmd.exe starts ping.exe: the same shape as pi.cmd and node.exe.
        let mut wrapper = command("cmd")
            .args(["/c", "ping -n 60 127.0.0.1 > nul"])
            .spawn()
            .expect("spawn");
        let wrapper_id = wrapper.id();
        assert!(
            wait_until(|| !children_of(wrapper_id).is_empty()),
            "the wrapper started a child"
        );
        let grandchildren = children_of(wrapper_id);

        // Child::kill alone would leave them: that is the bug.
        kill_tree(&mut wrapper).ok();
        wrapper.wait().ok();
        assert!(wait_until(|| !alive(wrapper_id)));
        for pid in grandchildren {
            assert!(wait_until(|| !alive(pid)), "grandchild {pid} still runs");
        }
    }

    #[test]
    fn plain_kill_leaves_the_grandchild_which_is_why_kill_tree_exists() {
        let mut wrapper = command("cmd")
            .args(["/c", "ping -n 60 127.0.0.1 > nul"])
            .spawn()
            .expect("spawn");
        let wrapper_id = wrapper.id();
        assert!(wait_until(|| !children_of(wrapper_id).is_empty()));
        let grandchildren = children_of(wrapper_id);
        wrapper.kill().unwrap();
        wrapper.wait().unwrap();
        std::thread::sleep(Duration::from_millis(500));
        assert!(
            grandchildren.iter().any(|&pid| alive(pid)),
            "if this fails, Child::kill became enough and kill_tree is unneeded"
        );
        for pid in grandchildren {
            let _ = command("taskkill")
                .args(["/F", "/PID", &pid.to_string()])
                .output();
        }
    }

    #[test]
    fn kill_tree_on_a_finished_child_is_not_a_panic() {
        let mut quick = command("cmd")
            .args(["/c", "exit 0"])
            .spawn()
            .expect("spawn");
        quick.wait().unwrap();
        // The process is gone: taskkill fails, kill reports it, nothing panics.
        let _ = kill_tree(&mut quick);
    }

    #[test]
    fn spawn_outliving_starts_the_command_and_passes_its_arguments() {
        let mut child = spawn_outliving(command("cmd").args(["/c", "exit 7"])).expect("spawn");
        assert_eq!(child.wait().unwrap().code(), Some(7));
    }

    #[test]
    fn commands_still_capture_output_without_a_console() {
        // (That no window appears is checked end to end by windows-smoke.py.)
        let output = command("cmd")
            .args(["/c", "echo hi"])
            .output()
            .expect("run");
        assert_eq!(String::from_utf8_lossy(&output.stdout).trim(), "hi");
    }
}
