//! Windows: `pi-gpui.exe --register-url-scheme` makes `pi-gpui://` links open
//! this exe, for the current user only (HKCU, no admin rights);
//! `--unregister-url-scheme` undoes it. Opt-in, done by the installer. A link
//! then starts `"pi-gpui.exe" "pi-gpui://..."`, and a second launch hands the
//! link to the running app (single_instance.rs). macOS registers the scheme in
//! Info.plist, Linux in the .desktop file.

use std::path::Path;

/// Where the scheme lives, under HKEY_CURRENT_USER.
pub const KEY: &str = r"Software\Classes\pi-gpui";

/// The registry values that route `pi-gpui://` links to `exe`:
/// (subkey under the scheme key, value name, data). An empty value name is
/// the key's default value.
pub fn entries(exe: &Path) -> Vec<(&'static str, &'static str, String)> {
    let exe = exe.display();
    vec![
        ("", "", "URL:Pi GPUI".to_string()),
        ("", "URL Protocol", String::new()),
        ("DefaultIcon", "", format!("\"{exe}\",0")),
        (r"shell\open\command", "", format!("\"{exe}\" \"%1\"")),
    ]
}

/// Handles `--register-url-scheme` / `--unregister-url-scheme`. Returns the
/// exit code when the argument was one of them.
pub fn run_cli(argument: &str) -> Option<i32> {
    let register = match argument {
        "--register-url-scheme" => true,
        "--unregister-url-scheme" => false,
        _ => return None,
    };
    #[cfg(windows)]
    {
        crate::platform::attach_parent_console();
        let outcome = if register {
            std::env::current_exe()
                .and_then(|exe| registry::register(KEY, &exe).map(|()| exe))
                .map(|exe| format!("pi-gpui:// links now open {}", exe.display()))
        } else {
            registry::unregister(KEY).map(|()| "pi-gpui:// links are no longer registered".into())
        };
        Some(match outcome {
            Ok(message) => {
                println!("{message}");
                0
            }
            Err(error) => {
                eprintln!("pi-gpui: {error}");
                1
            }
        })
    }
    #[cfg(not(windows))]
    {
        let _ = register;
        eprintln!(
            "pi-gpui: only Windows needs this; macOS and Linux register pi-gpui:// on install"
        );
        Some(1)
    }
}

#[cfg(windows)]
pub mod registry {
    use std::io;
    use std::path::Path;

    const HKEY_CURRENT_USER: isize = 0x8000_0001_u32 as i32 as isize;
    const REG_SZ: u32 = 1;
    #[cfg(test)]
    const RRF_RT_REG_SZ: u32 = 0x2;
    const ERROR_FILE_NOT_FOUND: i32 = 2;

    #[link(name = "advapi32")]
    unsafe extern "system" {
        fn RegSetKeyValueW(
            key: isize,
            subkey: *const u16,
            name: *const u16,
            kind: u32,
            data: *const u16,
            size: u32,
        ) -> i32;
        #[cfg(test)]
        fn RegGetValueW(
            key: isize,
            subkey: *const u16,
            name: *const u16,
            flags: u32,
            kind: *mut u32,
            data: *mut u16,
            size: *mut u32,
        ) -> i32;
        fn RegDeleteTreeW(key: isize, subkey: *const u16) -> i32;
    }

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain([0]).collect()
    }

    fn check(status: i32) -> io::Result<()> {
        if status == 0 {
            Ok(())
        } else {
            Err(io::Error::from_raw_os_error(status))
        }
    }

    fn path(key: &str, subkey: &str) -> String {
        if subkey.is_empty() {
            key.to_string()
        } else {
            format!(r"{key}\{subkey}")
        }
    }

    /// Writes the scheme under HKCU\`key`, routing links to `exe`.
    pub fn register(key: &str, exe: &Path) -> io::Result<()> {
        for (subkey, name, data) in super::entries(exe) {
            set(&path(key, subkey), name, &data)?;
        }
        Ok(())
    }

    /// Removes HKCU\`key` and everything below it; fine if it is not there.
    pub fn unregister(key: &str) -> io::Result<()> {
        let key = wide(key);
        // SAFETY: a NUL-terminated key name under a predefined root key.
        let status = unsafe { RegDeleteTreeW(HKEY_CURRENT_USER, key.as_ptr()) };
        if status == ERROR_FILE_NOT_FOUND {
            return Ok(());
        }
        check(status)
    }

    /// Sets a string value, creating the key.
    pub fn set(key: &str, name: &str, data: &str) -> io::Result<()> {
        let (key, name, data) = (wide(key), wide(name), wide(data));
        // SAFETY: NUL-terminated strings; `size` is the data's length in bytes
        // including the terminator, as REG_SZ requires.
        check(unsafe {
            RegSetKeyValueW(
                HKEY_CURRENT_USER,
                key.as_ptr(),
                name.as_ptr(),
                REG_SZ,
                data.as_ptr(),
                (data.len() * 2) as u32,
            )
        })
    }

    /// Reads a string value (tests check what was written); `None` if the key or value is missing.
    #[cfg(test)]
    pub fn get(key: &str, name: &str) -> Option<String> {
        let (key, name) = (wide(key), wide(name));
        let mut size = 0u32;
        // SAFETY: first call asks for the size only (null buffer), the second
        // fills a buffer of that size.
        unsafe {
            let ask = |data: *mut u16, size: &mut u32| {
                RegGetValueW(
                    HKEY_CURRENT_USER,
                    key.as_ptr(),
                    name.as_ptr(),
                    RRF_RT_REG_SZ,
                    std::ptr::null_mut(),
                    data,
                    size,
                )
            };
            if ask(std::ptr::null_mut(), &mut size) != 0 {
                return None;
            }
            let mut buffer = vec![0u16; (size as usize).div_ceil(2)];
            if ask(buffer.as_mut_ptr(), &mut size) != 0 {
                return None;
            }
            let length = buffer
                .iter()
                .position(|&unit| unit == 0)
                .unwrap_or(buffer.len());
            Some(String::from_utf16_lossy(&buffer[..length]))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_command_quotes_the_exe_and_passes_the_link_as_one_argument() {
        let exe = Path::new(r"C:\Users\Me\AppData\Local\Programs\pi-gpui\pi-gpui.exe");
        let entries = entries(exe);
        let command = entries
            .iter()
            .find(|(subkey, _, _)| *subkey == r"shell\open\command")
            .map(|(_, _, data)| data.as_str());
        assert_eq!(
            command,
            Some(r#""C:\Users\Me\AppData\Local\Programs\pi-gpui\pi-gpui.exe" "%1""#)
        );
        assert!(
            entries
                .iter()
                .any(|(subkey, name, _)| subkey.is_empty() && *name == "URL Protocol"),
            "without the URL Protocol value Windows does not treat the key as a scheme"
        );
    }

    #[test]
    fn other_arguments_are_not_handled() {
        assert_eq!(run_cli("--version"), None);
        assert_eq!(run_cli(r"C:\project"), None);
    }

    #[cfg(windows)]
    #[test]
    fn register_writes_the_scheme_and_unregister_removes_it() {
        use registry::{get, register, unregister};
        // A throwaway key, not Software\Classes: the test must not change
        // which app opens real links.
        let key = format!(r"Software\pi-gpui-test-{}", std::process::id());
        let exe = Path::new(r"C:\some where\pi-gpui.exe");
        register(&key, exe).unwrap();
        let read = |subkey: &str, name: &str| {
            get(
                &if subkey.is_empty() {
                    key.clone()
                } else {
                    format!(r"{key}\{subkey}")
                },
                name,
            )
        };
        assert_eq!(read("", "").as_deref(), Some("URL:Pi GPUI"));
        assert_eq!(read("", "URL Protocol").as_deref(), Some(""));
        assert_eq!(
            read(r"shell\open\command", "").as_deref(),
            Some(r#""C:\some where\pi-gpui.exe" "%1""#)
        );
        assert_eq!(
            read("DefaultIcon", "").as_deref(),
            Some(r#""C:\some where\pi-gpui.exe",0"#)
        );
        // Registering again (an update moved the exe) replaces the command.
        register(&key, Path::new(r"D:\new\pi-gpui.exe")).unwrap();
        assert_eq!(
            read(r"shell\open\command", "").as_deref(),
            Some(r#""D:\new\pi-gpui.exe" "%1""#)
        );
        unregister(&key).unwrap();
        assert_eq!(read("", ""), None);
        assert_eq!(read(r"shell\open\command", ""), None);
        // Unregistering what is not there is fine.
        unregister(&key).unwrap();
    }
}
