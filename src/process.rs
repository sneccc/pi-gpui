//! Child processes. A Windows GUI app that starts a console program (pi,
//! git, curl) would otherwise get a console window for each one.

use std::ffi::OsStr;
use std::process::Command;

/// `Command::new`, without a console window on Windows.
pub fn command(program: impl AsRef<OsStr>) -> Command {
    let command = Command::new(program);
    #[cfg(windows)]
    let command = {
        use std::os::windows::process::CommandExt as _;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let mut command = command;
        command.creation_flags(CREATE_NO_WINDOW);
        command
    };
    command
}
