// Windows: embed the icon and the version shown in Explorer's Properties.
// resources/windows/pi-gpui.rc is a template: the version from Cargo.toml and
// the icon's absolute path are filled in here, then rc.exe compiles it.
use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-changed=resources/windows/pi-gpui.rc");
    println!("cargo:rerun-if-changed=resources/windows/pi-gpui.ico");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let env = |name: &str| std::env::var(name).unwrap();
    let root = PathBuf::from(env("CARGO_MANIFEST_DIR"));
    let icon = root.join("resources/windows/pi-gpui.ico");
    let template = std::fs::read_to_string(root.join("resources/windows/pi-gpui.rc")).unwrap();
    let numbers = format!(
        "{},{},{},0",
        env("CARGO_PKG_VERSION_MAJOR"),
        env("CARGO_PKG_VERSION_MINOR"),
        env("CARGO_PKG_VERSION_PATCH")
    );
    let script = template
        .replace("@ICON@", &icon.display().to_string().replace('\\', "\\\\"))
        .replace("@VERSION_NUMBERS@", &numbers)
        .replace("@VERSION@", &env("CARGO_PKG_VERSION"));
    let generated = PathBuf::from(env("OUT_DIR")).join("pi-gpui.rc");
    std::fs::write(&generated, script).unwrap();
    embed_resource::compile(&generated, embed_resource::NONE)
        .manifest_optional()
        .unwrap();
}
