//! "Copy session trace" in a session's menu: one Markdown document with
//! everything needed to diagnose a session, to paste into an issue or an
//! agent. The app and pi versions, the session's state in the app, its whole
//! session file (every message, tool call, model change and error), its
//! lease, and what pi-durable-subagents published about its subagents.
//!
//! The trace is always written to `<app cache>/traces/`. The clipboard gets
//! the whole text when it is small enough to paste, else a summary and the
//! file's path.

use std::path::{Path, PathBuf};

/// Larger traces are copied as their path (pasting megabytes helps nobody).
pub const CLIPBOARD_LIMIT: usize = 256 * 1024;
/// How much of the app log a trace carries.
const APP_LOG_LINES: usize = 300;

/// What the app knows about the session; read on the main thread.
#[derive(Clone, Debug, Default)]
pub struct SessionState {
    pub title: String,
    pub workspace: PathBuf,
    pub file: Option<PathBuf>,
    /// Open in the app: model and status; `None` when only listed.
    pub open: Option<(String, String)>,
    pub app_version: String,
    pub pi: Option<(PathBuf, String)>,
}

/// What was read from disk; gathered off the main thread.
#[derive(Clone, Debug, Default)]
pub struct Files {
    pub session_id: Option<String>,
    pub session_jsonl: Option<String>,
    pub lease: Option<String>,
    pub lease_holder: Option<String>,
    pub fleet: Option<String>,
    /// (agent file name, contents), sorted.
    pub agents: Vec<(String, String)>,
    pub subagents_folder: Option<PathBuf>,
    /// The end of the app log (see app_log.rs): errors and notices.
    pub app_log: Option<String>,
}

/// Reads the session's files. Blocks on the disk: run it in the background.
pub fn read_files(state: &SessionState) -> Files {
    let mut files = Files {
        app_log: crate::app_log::tail(APP_LOG_LINES),
        ..Files::default()
    };
    let Some(file) = &state.file else {
        return files;
    };
    files.session_jsonl = std::fs::read_to_string(file).ok();
    files.session_id = files
        .session_jsonl
        .as_deref()
        .and_then(|text| text.lines().next())
        .and_then(|line| serde_json::from_str::<serde_json::Value>(line).ok())
        .and_then(|header| header["id"].as_str().map(str::to_string));
    files.lease = std::fs::read_to_string(format!("{}.lease", file.display())).ok();
    files.lease_holder = crate::sessions::foreign_lease_holder(file);
    if let Some(folder) = files
        .session_id
        .as_deref()
        .and_then(crate::subagents::published_folder)
    {
        files.fleet = std::fs::read_to_string(folder.join("fleet.json")).ok();
        let mut agents: Vec<(String, String)> = std::fs::read_dir(folder.join("agents"))
            .into_iter()
            .flatten()
            .flatten()
            .filter(|entry| entry.path().extension().is_some_and(|ext| ext == "json"))
            .filter_map(|entry| {
                let name = entry.file_name().to_string_lossy().to_string();
                Some((name, std::fs::read_to_string(entry.path()).ok()?))
            })
            .collect();
        agents.sort();
        files.agents = agents;
        files.subagents_folder = Some(folder);
    }
    files
}

/// A fenced block whose fence is longer than any backtick run inside, so the
/// content cannot end it early.
fn fenced(language: &str, text: &str) -> String {
    let mut longest = 0;
    let mut run = 0;
    for c in text.chars() {
        run = if c == '`' { run + 1 } else { 0 };
        longest = longest.max(run);
    }
    let fence = "`".repeat((longest + 1).max(3));
    format!("{fence}{language}\n{}\n{fence}\n", text.trim_end())
}

/// The trace as Markdown.
pub fn render(state: &SessionState, files: &Files, now: &str) -> String {
    let mut out = String::new();
    let line = |out: &mut String, label: &str, value: &str| {
        out.push_str(&format!("- {label}: {value}\n"));
    };
    out.push_str(&format!("# Pi GPUI session trace: {}\n\n", state.title));
    line(&mut out, "written", now);
    line(
        &mut out,
        "app",
        &format!(
            "Pi GPUI {} on {} {}",
            state.app_version,
            std::env::consts::OS,
            std::env::consts::ARCH
        ),
    );
    match &state.pi {
        Some((path, version)) => line(&mut out, "pi", &format!("{version} ({})", path.display())),
        None => line(&mut out, "pi", "not found yet"),
    }
    line(&mut out, "project", &state.workspace.display().to_string());
    line(
        &mut out,
        "session file",
        &state
            .file
            .as_ref()
            .map_or("none yet (no message sent)".into(), |file| {
                file.display().to_string()
            }),
    );
    line(
        &mut out,
        "session id",
        files.session_id.as_deref().unwrap_or("unknown"),
    );
    match &state.open {
        Some((model, status)) => line(
            &mut out,
            "in the app",
            &format!("open, {status}, model {model}"),
        ),
        None => line(&mut out, "in the app", "not open"),
    }
    line(
        &mut out,
        "lease",
        &match (&files.lease, &files.lease_holder) {
            (None, _) => "none".to_string(),
            (Some(_), Some(holder)) => format!("held by {holder} (a live process)"),
            (Some(_), None) => {
                "present; its holder is this app's pi or no longer running".to_string()
            }
        },
    );
    let messages = files.session_jsonl.as_deref().map_or(0, |text| {
        text.lines()
            .filter(|line| line.contains("\"type\":\"message\""))
            .count()
    });
    line(
        &mut out,
        "messages in the session file",
        &messages.to_string(),
    );
    line(
        &mut out,
        "subagents published",
        &files.agents.len().to_string(),
    );

    if let Some(lease) = &files.lease {
        out.push_str("\n## Lease\n\n");
        out.push_str(&fenced("json", lease));
    }
    out.push_str("\n## Subagents (pi-durable-subagents)\n\n");
    match (&files.fleet, &files.subagents_folder) {
        (Some(fleet), Some(folder)) => {
            out.push_str(&format!("Published in `{}`.\n\n### Fleet\n\n", folder.display()));
            out.push_str(&fenced("json", fleet));
            for (name, text) in &files.agents {
                out.push_str(&format!("\n### {name}\n\n"));
                out.push_str(&fenced("json", text));
            }
        }
        _ => out.push_str(
            "Nothing published: the session is not open in the app, has no subagents, or pi-durable-subagents without the Pi GPUI publisher. Its database is `<pi agent dir>/durable-subagents/<sha256 of the session id>.sqlite`.\n",
        ),
    }
    out.push_str("\n## App log (last lines)\n\n");
    match &files.app_log {
        Some(log) if !log.trim().is_empty() => {
            if let Some(path) = crate::app_log::path() {
                out.push_str(&format!("From `{}`.\n\n", path.display()));
            }
            out.push_str(&fenced("text", log));
        }
        _ => out.push_str("No app log yet.\n"),
    }
    out.push_str("\n## Session file (JSONL)\n\n");
    match &files.session_jsonl {
        Some(text) => out.push_str(&fenced("jsonl", text)),
        None => out.push_str("Not on disk yet.\n"),
    }
    out
}

/// Where the trace goes: `<app cache>/traces/<session>-<time>.md`.
pub fn trace_path(files: &Files, stamp: &str) -> Option<PathBuf> {
    let name = files.session_id.as_deref().unwrap_or("new-session");
    Some(
        crate::sessions::app_cache_directory()?
            .join("traces")
            .join(format!(
                "{}-{stamp}.md",
                name.replace(|c: char| !c.is_ascii_alphanumeric() && c != '-', "_")
            )),
    )
}

/// What goes on the clipboard: the trace, or a summary and its path.
pub fn clipboard_text(trace: &str, path: &Path) -> String {
    if trace.len() <= CLIPBOARD_LIMIT {
        return trace.to_string();
    }
    let summary: String = trace
        .lines()
        .take_while(|line| !line.starts_with("## "))
        .collect::<Vec<_>>()
        .join("\n");
    format!(
        "{}\n\nThe full trace ({} KB) is in `{}`.\n",
        summary.trim_end(),
        trace.len() / 1024,
        path.display()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn state() -> SessionState {
        SessionState {
            title: "fix the login".into(),
            workspace: PathBuf::from("C:/work/app"),
            file: Some(PathBuf::from("C:/s/a.jsonl")),
            open: Some(("claude-sonnet".into(), "ready".into())),
            app_version: "0.2.11".into(),
            pi: Some((PathBuf::from("pi.cmd"), "1.6.1".into())),
        }
    }

    #[test]
    fn the_trace_has_the_facts_the_session_and_its_subagents() {
        let files = Files {
            session_id: Some("s1".into()),
            session_jsonl: Some(
                "{\"type\":\"session\",\"id\":\"s1\"}\n{\"type\":\"message\",\"x\":\"```\"}".into(),
            ),
            lease: Some("{\"pid\":1}".into()),
            lease_holder: Some("a terminal pi".into()),
            fleet: Some("{\"agents\":[]}".into()),
            agents: vec![("scout.json".into(), "{\"name\":\"scout\"}".into())],
            subagents_folder: Some(PathBuf::from("C:/cache/subagents/s1")),
            app_log: Some(
                "2026-11-27 14:00:00.000 pi-gpui: Durable error: database is locked".into(),
            ),
        };
        let trace = render(&state(), &files, "2026-11-27 12:00");
        for expected in [
            "# Pi GPUI session trace: fix the login",
            "- pi: 1.6.1 (pi.cmd)",
            "- session id: s1",
            "- in the app: open, ready, model claude-sonnet",
            "- lease: held by a terminal pi (a live process)",
            "- messages in the session file: 1",
            "- subagents published: 1",
            "### scout.json",
            "## App log (last lines)",
            "Durable error: database is locked",
            "## Session file (JSONL)",
        ] {
            assert!(trace.contains(expected), "missing {expected:?} in\n{trace}");
        }
        // The session's own backticks cannot close the block early.
        assert!(trace.contains("````jsonl\n"), "{trace}");
    }

    #[test]
    fn a_session_without_a_file_or_subagents_says_so() {
        let mut state = state();
        state.file = None;
        state.open = None;
        let trace = render(&state, &Files::default(), "now");
        assert!(trace.contains("- session file: none yet (no message sent)"));
        assert!(trace.contains("- in the app: not open"));
        assert!(trace.contains("Nothing published"));
        assert!(trace.contains("Not on disk yet."));
    }

    #[test]
    fn big_traces_are_copied_as_a_summary_and_path() {
        let path = Path::new("C:/cache/traces/s1.md");
        assert_eq!(clipboard_text("# small\n", path), "# small\n");
        let big = format!(
            "# t\n- a: b\n\n## Session file\n{}",
            "x".repeat(CLIPBOARD_LIMIT)
        );
        let copied = clipboard_text(&big, path);
        assert!(copied.starts_with("# t\n- a: b"));
        assert!(
            copied.contains("C:/cache/traces/s1.md") || copied.contains("C:\\cache\\traces\\s1.md")
        );
        assert!(!copied.contains("xxxx"));
    }
}
