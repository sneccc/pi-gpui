//! Helpers for unit tests.

use std::path::{Path, PathBuf};

/// A fresh folder under the system temp folder, removed when dropped: also
/// when an assertion fails, so failed runs leave nothing behind.
pub struct TempDir(PathBuf);

impl TempDir {
    /// `pi-gpui-<name>-<process id>-<counter>`, created empty.
    pub fn new(name: &str) -> Self {
        static COUNTER: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let number = COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let path =
            std::env::temp_dir().join(format!("pi-gpui-{name}-{}-{number}", std::process::id()));
        std::fs::remove_dir_all(&path).ok();
        std::fs::create_dir_all(&path).expect("create a temp folder");
        Self(path)
    }

    pub fn path(&self) -> &Path {
        &self.0
    }

    pub fn join(&self, name: impl AsRef<Path>) -> PathBuf {
        self.0.join(name)
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        std::fs::remove_dir_all(&self.0).ok();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_folder_is_removed_even_when_the_test_panics() {
        let kept = std::panic::catch_unwind(|| {
            let folder = TempDir::new("panic");
            std::fs::write(folder.join("file"), "x").unwrap();
            let path = folder.path().to_path_buf();
            std::panic::panic_any(path);
        });
        let path = kept.unwrap_err().downcast::<PathBuf>().unwrap();
        assert!(!path.exists(), "{} was left behind", path.display());
    }
}
