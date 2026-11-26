//! Drives the app's own window for UI checks: macOS debug builds and Windows
//! builds, and only when `PI_GPUI_AUTOMATION` names a folder.
//!
//! Keys and clicks are dispatched inside the app and the screenshot is of the
//! app's own window, so the checks never touch the real pointer or keyboard
//! and work while the window sits behind others. On macOS that needs neither
//! the Screen Recording nor the Accessibility permission. See
//! docs/MAC_UI_CHECKS.md (macOS) and docs/WINDOWS_UI_CHECKS.md.
//!
//! Write commands, one per line, to `<folder>/command` (rename the file into
//! place). The app runs them in order, removes the file and writes
//! `<folder>/done` with one result line per command:
//!
//! ```text
//! key down space cmd-enter     keystrokes, in gpui's notation
//! type some text               typed character by character
//! click 640 400                press and release at window coordinates
//! move 640 400                 move the pointer (hover)
//! wait 500                     milliseconds
//! shot /tmp/window.png         PNG of the window, 1 px per point (`2x` after
//!                              the path for full resolution)
//! ```

// `objc` 0.2 macros probe a `cargo-clippy` feature that modern rustc flags.
#![allow(unexpected_cfgs)]

use std::path::{Path, PathBuf};
use std::time::Duration;

use gpui::{AnyWindowHandle, App, AsyncApp, Keystroke, Modifiers, Window};

#[cfg(target_os = "macos")]
mod macos;
#[cfg(target_os = "macos")]
use macos as native;
#[cfg(windows)]
mod windows;
use native::{NativeWindow, native_window};
#[cfg(windows)]
use windows as native;

const POLL: Duration = Duration::from_millis(100);
/// Time for the app to react to an event before the next one.
const SETTLE: Duration = Duration::from_millis(40);

/// The folder commands are read from, when automation is on.
pub fn folder() -> Option<PathBuf> {
    std::env::var_os("PI_GPUI_AUTOMATION")
        .filter(|folder| !folder.is_empty())
        .map(PathBuf::from)
}

pub fn start(window: AnyWindowHandle, cx: &mut App) {
    let Some(folder) = folder() else {
        return;
    };
    eprintln!("pi-gpui: automation commands from {}", folder.display());
    cx.spawn(async move |cx| {
        let (command, done) = (folder.join("command"), folder.join("done"));
        loop {
            cx.background_executor().timer(POLL).await;
            let Ok(script) = std::fs::read_to_string(&command) else {
                continue;
            };
            std::fs::remove_file(&command).ok();
            let mut report = String::new();
            for line in script
                .lines()
                .map(str::trim)
                .filter(|line| !line.is_empty())
            {
                let outcome = match run(line, window, cx).await {
                    Ok(()) => "ok".to_string(),
                    Err(error) => format!("error: {error}"),
                };
                report.push_str(&format!("{line}: {outcome}\n"));
            }
            let staged = folder.join("done.tmp");
            if std::fs::write(&staged, report).is_ok() {
                std::fs::rename(&staged, &done).ok();
            }
        }
    })
    .detach();
}

async fn run(line: &str, window: AnyWindowHandle, cx: &mut AsyncApp) -> Result<(), String> {
    let (name, rest) = line.split_once(' ').unwrap_or((line, ""));
    let rest = rest.trim();
    match name {
        "key" => {
            for keys in rest.split_whitespace() {
                let keystroke = Keystroke::parse(keys).map_err(|error| error.to_string())?;
                dispatch_key(window, keystroke, cx).await?;
            }
        }
        "type" => {
            for character in rest.chars() {
                dispatch_key(window, typed(character), cx).await?;
            }
        }
        "click" | "move" => {
            let (x, y) = parse_point(rest)?;
            let target =
                update(window, cx, |window, _| native_window(window))?.ok_or("no native window")?;
            let events: &[MouseEvent] = if name == "click" {
                &[MouseEvent::Move, MouseEvent::Down, MouseEvent::Up]
            } else {
                &[MouseEvent::Move]
            };
            for event in events {
                native::mouse(window, cx, target, *event, x, y)?;
                settle(window, cx).await?;
            }
        }
        "wait" => {
            let milliseconds: u64 = rest.parse().map_err(|_| "wait needs milliseconds")?;
            cx.background_executor()
                .timer(Duration::from_millis(milliseconds))
                .await;
        }
        "shot" => {
            let (path, scale) = rest.split_once(' ').unwrap_or((rest, ""));
            if path.is_empty() {
                return Err("shot needs a path".into());
            }
            let target = settle(window, cx).await?;
            // The frame reaches the window server when the run loop turns.
            cx.background_executor().timer(SETTLE).await;
            // SAFETY: as in `settle`.
            unsafe { native::capture(target, Path::new(path), scale.trim() == "2x")? };
        }
        _ => return Err("unknown command".into()),
    }
    Ok(())
}

fn update<R>(
    window: AnyWindowHandle,
    cx: &mut AsyncApp,
    run: impl FnOnce(&mut Window, &mut App) -> R,
) -> Result<R, String> {
    window
        .update(cx, |_, window, cx| run(window, cx))
        .map_err(|error| error.to_string())
}

async fn dispatch_key(
    window: AnyWindowHandle,
    keystroke: Keystroke,
    cx: &mut AsyncApp,
) -> Result<(), String> {
    update(window, cx, |window, cx| {
        window.dispatch_keystroke(keystroke, cx);
    })?;
    settle(window, cx).await?;
    Ok(())
}

/// Lets the app react to an event, then draws a frame: the next event is
/// hit-tested and routed against what the last frame showed.
async fn settle(window: AnyWindowHandle, cx: &mut AsyncApp) -> Result<NativeWindow, String> {
    cx.background_executor().timer(SETTLE).await;
    let target =
        update(window, cx, |window, _| native_window(window))?.ok_or("no native window")?;
    // SAFETY: on the main thread, outside any app update (drawing re-enters
    // the app), with a live view from this window.
    unsafe { native::draw(target) };
    Ok(target)
}

/// The keystroke that types `character`.
fn typed(character: char) -> Keystroke {
    let key = match character {
        ' ' => "space".to_string(),
        '\n' => "enter".to_string(),
        _ => character.to_lowercase().to_string(),
    };
    Keystroke {
        modifiers: Modifiers {
            shift: character.is_uppercase(),
            ..Modifiers::default()
        },
        key,
        key_char: (character != '\n').then(|| character.to_string()),
    }
}

fn parse_point(text: &str) -> Result<(f64, f64), String> {
    let mut numbers = text.split_whitespace().map(str::parse::<f64>);
    match (numbers.next(), numbers.next()) {
        (Some(Ok(x)), Some(Ok(y))) => Ok((x, y)),
        _ => Err("expected `x y`".into()),
    }
}

#[derive(Clone, Copy)]
enum MouseEvent {
    Move,
    Down,
    Up,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn typed_characters_become_keystrokes_with_their_text() {
        let lower = typed('a');
        assert_eq!(lower.key, "a");
        assert_eq!(lower.key_char.as_deref(), Some("a"));
        assert!(!lower.modifiers.shift);

        // Capitals are shift + the lowercase key, and still type the capital.
        let upper = typed('A');
        assert_eq!(upper.key, "a");
        assert_eq!(upper.key_char.as_deref(), Some("A"));
        assert!(upper.modifiers.shift);
    }

    #[test]
    fn typed_space_and_newline_use_gpui_key_names() {
        let space = typed(' ');
        assert_eq!(space.key, "space");
        assert_eq!(space.key_char.as_deref(), Some(" "));

        // Enter submits: it presses the key but types no character.
        let enter = typed('\n');
        assert_eq!(enter.key, "enter");
        assert_eq!(enter.key_char, None);
    }

    #[test]
    fn digits_punctuation_and_non_ascii_are_typed_as_themselves() {
        for character in ['7', '/', '.', 'é'] {
            let keystroke = typed(character);
            assert_eq!(keystroke.key_char, Some(character.to_string()));
            assert!(!keystroke.modifiers.shift);
        }
    }

    #[test]
    fn points_parse_as_two_numbers() {
        assert_eq!(parse_point("640 400"), Ok((640.0, 400.0)));
        assert_eq!(parse_point("  12.5   0  "), Ok((12.5, 0.0)));
        assert_eq!(parse_point("-3 4"), Ok((-3.0, 4.0)));
    }

    #[test]
    fn bad_points_are_rejected_not_guessed() {
        for bad in ["", "640", "a b", "1 b", "1,2"] {
            assert!(parse_point(bad).is_err(), "{bad:?} should not parse");
        }
    }
}
