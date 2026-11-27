//! The Subagents panel: the active session's subagents from the
//! pi-durable-subagents extension, and a view of one subagent's conversation.
//!
//! The extension's own overview runs only in a terminal. Started by the app
//! (`PI_GPUI_SUBAGENTS_DIR`), it writes `<dir>/<session id>/fleet.json` and
//! `agents/<agent id>.json` on every change and removes them when the session
//! closes. The panel reads them: it checks the files' times a few times a
//! second while it (or a subagent's conversation) is shown, and parses only
//! what changed.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime};

use gpui::{
    AnyElement, Context, Entity, FocusHandle, ScrollHandle, Task, Window, div, prelude::*, px,
};
use serde_json::Value;

use crate::markdown::{self, Node};
use crate::selectable::TextSelection;
use crate::ui::prelude::*;

/// How often the files are checked while something shows them.
const POLL: Duration = Duration::from_millis(400);

/// Where the extension writes for this app: the app's cache folder.
pub fn publish_root() -> Option<PathBuf> {
    crate::sessions::app_cache_directory().map(|cache| cache.join("subagents"))
}

/// The folder of one parent session (its id, as the extension names it).
fn session_folder(root: &Path, session_id: &str) -> PathBuf {
    root.join(sanitize(session_id))
}

fn sanitize(id: &str) -> String {
    id.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || "_.-".contains(c) {
                c
            } else {
                '_'
            }
        })
        .collect()
}

/// The `id` in a pi session file's header line.
fn session_id(file: &Path) -> Option<String> {
    use std::io::BufRead as _;
    let mut line = String::new();
    std::io::BufReader::new(std::fs::File::open(file).ok()?)
        .read_line(&mut line)
        .ok()?;
    let header: Value = serde_json::from_str(&line).ok()?;
    header["id"].as_str().map(str::to_string)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Status {
    Queued,
    Running,
    Completed,
    Failed,
    Stopped,
    Idle,
}

impl Status {
    fn parse(text: &str) -> Self {
        match text {
            "running" => Self::Running,
            "completed" => Self::Completed,
            "failed" => Self::Failed,
            "stopped" => Self::Stopped,
            "queued" => Self::Queued,
            _ => Self::Idle,
        }
    }

    fn color(self) -> Color {
        match self {
            Self::Running => Color::Accent,
            Self::Completed => Color::Success,
            Self::Failed => Color::Error,
            Self::Stopped => Color::Warning,
            Self::Queued | Self::Idle => Color::Muted,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::Queued => "queued",
            Self::Running => "running",
            Self::Completed => "done",
            Self::Failed => "failed",
            Self::Stopped => "stopped",
            Self::Idle => "idle",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct AgentSummary {
    pub id: String,
    pub name: String,
    pub description: String,
    pub role: String,
    pub model: String,
    pub status: Status,
    pub activity: String,
    pub stats: String,
}

/// `fleet.json`: the subagents, oldest first.
pub fn parse_fleet(json: &Value) -> Vec<AgentSummary> {
    let text = |value: &Value| value.as_str().unwrap_or_default().to_string();
    json["agents"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|agent| {
            Some(AgentSummary {
                id: agent["id"].as_str()?.to_string(),
                name: text(&agent["name"]),
                description: text(&agent["description"]),
                role: text(&agent["role"]),
                model: text(&agent["model"]),
                status: Status::parse(agent["status"].as_str().unwrap_or_default()),
                activity: text(&agent["activity"]),
                stats: text(&agent["stats"]),
            })
        })
        .collect()
}

/// One step of a subagent's conversation.
#[derive(Clone, Debug, PartialEq)]
pub enum Item {
    User(String),
    Assistant(String),
    Tool {
        call_id: String,
        name: String,
        detail: String,
        output: String,
        failed: bool,
        done: bool,
    },
}

/// The text parts of a pi message's content (a string or a list of parts).
fn content_text(content: &Value) -> String {
    if let Some(text) = content.as_str() {
        return text.to_string();
    }
    content
        .as_array()
        .into_iter()
        .flatten()
        .filter(|part| part["type"] == "text")
        .filter_map(|part| part["text"].as_str())
        .collect::<Vec<_>>()
        .join("\n")
}

/// What a tool call is about, in one line: a command, a path, a prompt.
fn tool_detail(arguments: &Value) -> String {
    let detail = [
        "command",
        "path",
        "file_path",
        "pattern",
        "query",
        "url",
        "prompt",
        "description",
    ]
    .iter()
    .find_map(|key| arguments[*key].as_str())
    .map(|value| value.lines().next().unwrap_or_default().to_string())
    .unwrap_or_else(|| {
        if arguments.is_null() {
            String::new()
        } else {
            arguments.to_string()
        }
    });
    detail.chars().take(160).collect()
}

fn push_assistant(items: &mut Vec<Item>, message: &Value, done: bool) {
    let mut text = String::new();
    for part in message["content"].as_array().into_iter().flatten() {
        match part["type"].as_str() {
            Some("text") => {
                if !text.is_empty() {
                    text.push('\n');
                }
                text.push_str(part["text"].as_str().unwrap_or_default());
            }
            Some("toolCall") => {
                if !text.trim().is_empty() {
                    items.push(Item::Assistant(std::mem::take(&mut text)));
                }
                items.push(Item::Tool {
                    call_id: part["id"].as_str().unwrap_or_default().to_string(),
                    name: part["name"].as_str().unwrap_or_default().to_string(),
                    detail: tool_detail(&part["arguments"]),
                    output: String::new(),
                    failed: false,
                    done,
                });
            }
            _ => {}
        }
    }
    if !text.trim().is_empty() {
        items.push(Item::Assistant(text));
    }
}

/// `agents/<id>.json`: pi messages (user, assistant, toolResult) and the
/// answer in progress, as items to show.
pub fn parse_transcript(json: &Value) -> Vec<Item> {
    let mut items = Vec::new();
    for message in json["messages"].as_array().into_iter().flatten() {
        match message["role"].as_str() {
            Some("user") => {
                let text = content_text(&message["content"]);
                if !text.trim().is_empty() {
                    items.push(Item::User(text));
                }
            }
            Some("assistant") => push_assistant(&mut items, message, true),
            Some("toolResult") => {
                let id = message["toolCallId"].as_str().unwrap_or_default();
                let output = content_text(&message["content"]);
                let failed = message["isError"].as_bool().unwrap_or(false);
                if let Some(Item::Tool {
                    output: slot,
                    failed: slot_failed,
                    ..
                }) = items
                    .iter_mut()
                    .rev()
                    .find(|item| matches!(item, Item::Tool { call_id, .. } if call_id == id))
                {
                    *slot = output;
                    *slot_failed = failed;
                }
            }
            _ => {}
        }
    }
    if json["streaming"].is_object() {
        push_assistant(&mut items, &json["streaming"], false);
    }
    // Tool calls of the latest answer without a result yet are still running.
    let answered: HashSet<&str> = json["messages"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|message| message["role"] == "toolResult")
        .filter_map(|message| message["toolCallId"].as_str())
        .collect();
    for item in &mut items {
        if let Item::Tool { call_id, done, .. } = item {
            *done = answered.contains(call_id.as_str());
        }
    }
    items
}

fn modified(path: &Path) -> Option<SystemTime> {
    std::fs::metadata(path)
        .and_then(|meta| meta.modified())
        .ok()
}

fn read_json(path: &Path) -> Option<Value> {
    serde_json::from_slice(&std::fs::read(path).ok()?).ok()
}

/// The list in the dock, and which subagent the main area shows.
pub struct SubagentsPanel {
    /// The active session's file; its header names the session.
    session_file: Option<PathBuf>,
    session_id: Option<String>,
    /// Whether the panel is in a visible dock.
    visible: bool,
    agents: Vec<AgentSummary>,
    fleet_time: Option<SystemTime>,
    /// The subagent shown in the main area.
    viewing: Option<Entity<TranscriptView>>,
    _poll: Task<()>,
}

pub enum SubagentsEvent {
    /// The main area changes: a subagent's conversation or the session.
    ViewChanged,
}

impl gpui::EventEmitter<SubagentsEvent> for SubagentsPanel {}

impl SubagentsPanel {
    pub fn new(cx: &mut Context<Self>) -> Self {
        let poll = cx.spawn(async move |this, cx| {
            loop {
                cx.background_executor().timer(POLL).await;
                if this.update(cx, |this, cx| this.poll(cx)).is_err() {
                    return;
                }
            }
        });
        Self {
            session_file: None,
            session_id: None,
            visible: false,
            agents: Vec::new(),
            fleet_time: None,
            viewing: None,
            _poll: poll,
        }
    }

    /// Follows the active session. Another session closes the subagent view.
    pub fn set_session(&mut self, file: Option<PathBuf>, visible: bool, cx: &mut Context<Self>) {
        self.visible = visible;
        if self.session_file == file {
            return;
        }
        self.session_file = file;
        self.session_id = None;
        self.agents.clear();
        self.fleet_time = None;
        if self.viewing.take().is_some() {
            cx.emit(SubagentsEvent::ViewChanged);
        }
        self.poll(cx);
        cx.notify();
    }

    pub fn viewing(&self) -> Option<&Entity<TranscriptView>> {
        self.viewing.as_ref()
    }

    fn folder(&mut self) -> Option<PathBuf> {
        if self.session_id.is_none() {
            self.session_id = self.session_file.as_deref().and_then(session_id);
        }
        Some(session_folder(
            &publish_root()?,
            self.session_id.as_deref()?,
        ))
    }

    /// Reads what changed. Cheap when nothing did: two file times.
    fn poll(&mut self, cx: &mut Context<Self>) {
        if !self.visible && self.viewing.is_none() {
            return;
        }
        let Some(folder) = self.folder() else {
            return;
        };
        let fleet_path = folder.join("fleet.json");
        let time = modified(&fleet_path);
        if time != self.fleet_time {
            self.fleet_time = time;
            let agents = read_json(&fleet_path)
                .map(|json| parse_fleet(&json))
                .unwrap_or_default();
            if agents != self.agents {
                self.agents = agents;
                if let Some(view) = &self.viewing {
                    let summary = view
                        .read(cx)
                        .agent
                        .as_ref()
                        .and_then(|shown| self.agents.iter().find(|agent| agent.id == shown.id))
                        .cloned();
                    if let Some(summary) = summary {
                        view.update(cx, |view, cx| view.set_summary(summary, cx));
                    }
                }
                cx.notify();
            }
        }
        if let Some(view) = &self.viewing {
            let folder = folder.join("agents");
            view.update(cx, |view, cx| view.poll(&folder, cx));
        }
    }

    pub fn show_agent(&mut self, id: Option<String>, window: &mut Window, cx: &mut Context<Self>) {
        let summary = id.and_then(|id| self.agents.iter().find(|agent| agent.id == id).cloned());
        self.viewing = summary.map(|summary| {
            let view = cx.new(|cx| TranscriptView::new(cx.focus_handle(), cx));
            view.update(cx, |view, cx| view.set_summary(summary, cx));
            view
        });
        let _ = window;
        self.poll(cx);
        cx.emit(SubagentsEvent::ViewChanged);
        cx.notify();
    }

    fn render_row(
        &self,
        index: usize,
        agent: Option<&AgentSummary>,
        selected: bool,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let colors = cx.theme().colors.clone();
        let id = agent.map(|agent| agent.id.clone());
        let marker = match agent.map(|agent| agent.status) {
            Some(Status::Running) => Spinner::new()
                .size(IconSize::XSmall)
                .color(Color::Accent)
                .into_any_element(),
            Some(status) => Indicator::dot(status.color()).into_any_element(),
            None => Icon::new(IconName::Message)
                .size(IconSize::XSmall)
                .color(Color::Muted)
                .into_any_element(),
        };
        let (title, subtitle, meta) = match agent {
            Some(agent) => (
                agent.name.clone(),
                if agent.activity.is_empty() {
                    agent.description.clone()
                } else {
                    agent.activity.clone()
                },
                format!(
                    "{} · {} · {}",
                    agent.role,
                    agent.status.label(),
                    agent.stats
                ),
            ),
            None => (
                "Main".to_string(),
                "the session itself".to_string(),
                String::new(),
            ),
        };
        div()
            .id(("subagent-row", index))
            .flex()
            .w_full()
            .items_start()
            .gap(px(8.))
            .px(px(10.))
            .py(px(6.))
            .mx(px(4.))
            .rounded(px(5.))
            .cursor_pointer()
            .when(selected, |this| this.bg(colors.ghost_element_selected))
            .when(!selected, |this| {
                this.hover(|style| style.bg(colors.ghost_element_hover))
            })
            .child(
                div()
                    .pt(px(3.))
                    .w(px(12.))
                    .flex()
                    .justify_center()
                    .child(marker),
            )
            .child(
                v_flex()
                    .flex_1()
                    .min_w(px(0.))
                    .overflow_hidden()
                    // One row per line: a truncating label measures its width
                    // in a row, not in a column (there it shrinks to "…").
                    .child(
                        h_flex().w_full().child(
                            Label::new(title)
                                .size(LabelSize::Small)
                                .weight(gpui::FontWeight::MEDIUM)
                                .truncate(),
                        ),
                    )
                    .child(
                        h_flex().w_full().child(
                            Label::new(subtitle)
                                .size(LabelSize::XSmall)
                                .color(Color::Muted)
                                .truncate(),
                        ),
                    )
                    .when(!meta.is_empty(), |this| {
                        this.child(
                            h_flex().w_full().child(
                                Label::new(meta)
                                    .size(LabelSize::XSmall)
                                    .color(Color::Placeholder)
                                    .truncate(),
                            ),
                        )
                    }),
            )
            .on_click(
                cx.listener(move |this, _, window, cx| this.show_agent(id.clone(), window, cx)),
            )
            .into_any_element()
    }
}

impl Render for SubagentsPanel {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let viewing = self
            .viewing
            .as_ref()
            .and_then(|view| view.read(cx).agent.as_ref().map(|agent| agent.id.clone()));
        let mut rows = vec![self.render_row(0, None, viewing.is_none(), cx)];
        let agents = self.agents.clone();
        for (index, agent) in agents.iter().enumerate() {
            let selected = viewing.as_deref() == Some(agent.id.as_str());
            rows.push(self.render_row(index + 1, Some(agent), selected, cx));
        }
        v_flex()
            .id("subagents-panel")
            .size_full()
            .overflow_y_scroll()
            .py(px(4.))
            .gap(px(2.))
            .children(rows)
            .when(agents.is_empty(), |this| {
                this.child(
                    div().px(px(14.)).py(px(10.)).child(
                        Label::new(if self.session_file.is_none() {
                            "Subagents of the open session show here."
                        } else {
                            "No subagents in this session yet. They show here when pi delegates work (pi-durable-subagents)."
                        })
                        .size(LabelSize::XSmall)
                        .color(Color::Muted),
                    ),
                )
            })
    }
}

/// One subagent's conversation, in the main area.
pub struct TranscriptView {
    agent: Option<AgentSummary>,
    items: Vec<Item>,
    /// Parsed answers, by item index and text.
    markdown: HashMap<usize, (String, std::sync::Arc<[Node]>)>,
    /// Tool calls the user opened.
    open_tools: HashSet<usize>,
    file_time: Option<SystemTime>,
    scroll: ScrollHandle,
    selection: Entity<TextSelection>,
    /// New output scrolls into view while the user is at the bottom.
    follow: bool,
}

impl TranscriptView {
    fn new(focus: FocusHandle, cx: &mut Context<Self>) -> Self {
        Self {
            agent: None,
            items: Vec::new(),
            markdown: HashMap::new(),
            open_tools: HashSet::new(),
            file_time: None,
            scroll: ScrollHandle::new(),
            selection: cx.new(|_| TextSelection::new(focus)),
            follow: true,
        }
    }

    fn set_summary(&mut self, summary: AgentSummary, cx: &mut Context<Self>) {
        if self.agent.as_ref() != Some(&summary) {
            self.agent = Some(summary);
            cx.notify();
        }
    }

    fn poll(&mut self, folder: &Path, cx: &mut Context<Self>) {
        let Some(agent) = &self.agent else {
            return;
        };
        let path = folder.join(format!("{}.json", sanitize(&agent.id)));
        let time = modified(&path);
        if time == self.file_time {
            return;
        }
        self.file_time = time;
        let items = read_json(&path)
            .map(|json| parse_transcript(&json))
            .unwrap_or_default();
        if items != self.items {
            let at_bottom = {
                let max = self.scroll.max_offset().height;
                max <= px(1.) || -self.scroll.offset().y >= max - px(24.)
            };
            self.follow = at_bottom;
            self.items = items;
            if self.follow {
                self.scroll.scroll_to_bottom();
            }
            cx.notify();
        }
    }

    fn render_item(
        &mut self,
        index: usize,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) -> AnyElement {
        let colors = cx.theme().colors.clone();
        match self.items[index].clone() {
            Item::User(text) => div()
                .px(px(12.))
                .py(px(8.))
                .rounded(px(8.))
                .border_1()
                .border_color(colors.border_variant)
                .bg(colors.editor_background)
                .child(Label::new(text).size(LabelSize::Small))
                .into_any_element(),
            Item::Assistant(text) => {
                let nodes = match self.markdown.get(&index) {
                    Some((source, nodes)) if *source == text => nodes.clone(),
                    _ => {
                        let nodes: std::sync::Arc<[Node]> = markdown::parse(&text).into();
                        self.markdown.insert(index, (text, nodes.clone()));
                        nodes
                    }
                };
                markdown::render(
                    &format!("subagent:{index}"),
                    &nodes,
                    &self.selection,
                    window,
                    cx,
                )
            }
            Item::Tool {
                name,
                detail,
                output,
                failed,
                done,
                ..
            } => {
                let open = self.open_tools.contains(&index);
                let status = if !done {
                    Spinner::new()
                        .size(IconSize::XSmall)
                        .color(Color::Accent)
                        .into_any_element()
                } else if failed {
                    Icon::new(IconName::Close)
                        .size(IconSize::XSmall)
                        .color(Color::Error)
                        .into_any_element()
                } else {
                    Icon::new(IconName::Check)
                        .size(IconSize::XSmall)
                        .color(Color::Success)
                        .into_any_element()
                };
                v_flex()
                    .rounded(px(6.))
                    .border_1()
                    .border_color(colors.border_variant)
                    .overflow_hidden()
                    .child(
                        h_flex()
                            .id(("subagent-tool", index))
                            .gap(px(6.))
                            .h(px(28.))
                            .px(px(8.))
                            .bg(colors.panel_background)
                            .cursor_pointer()
                            .child(status)
                            .child(
                                Label::new(name)
                                    .size(LabelSize::Small)
                                    .weight(gpui::FontWeight::MEDIUM),
                            )
                            .child(
                                div().flex_1().min_w(px(0.)).child(
                                    Label::new(detail)
                                        .size(LabelSize::Small)
                                        .color(Color::Muted)
                                        .monospace()
                                        .truncate(),
                                ),
                            )
                            .when(!output.is_empty(), |this| {
                                this.child(
                                    Icon::new(if open {
                                        IconName::ChevronDown
                                    } else {
                                        IconName::ChevronRight
                                    })
                                    .size(IconSize::XSmall)
                                    .color(Color::Muted),
                                )
                            })
                            .on_click(cx.listener(move |this, _, _, cx| {
                                if !this.open_tools.remove(&index) {
                                    this.open_tools.insert(index);
                                }
                                cx.notify();
                            })),
                    )
                    .when(open && !output.is_empty(), |this| {
                        this.child(
                            div()
                                .px(px(8.))
                                .py(px(6.))
                                .border_t_1()
                                .border_color(colors.border_variant)
                                .child(
                                    Label::new(output)
                                        .size(LabelSize::XSmall)
                                        .color(Color::Muted)
                                        .monospace(),
                                ),
                        )
                    })
                    .into_any_element()
            }
        }
    }
}

impl Render for TranscriptView {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let colors = cx.theme().colors.clone();
        let header = self.agent.clone().map(|agent| {
            h_flex()
                .gap(px(8.))
                .px(px(16.))
                .h(px(36.))
                .border_b_1()
                .border_color(colors.border_variant)
                .child(Indicator::dot(agent.status.color()))
                .child(
                    Label::new(agent.name.clone())
                        .size(LabelSize::Small)
                        .weight(gpui::FontWeight::MEDIUM),
                )
                .child(
                    Label::new(format!(
                        "{} · {} · {} · {}",
                        agent.role,
                        agent.model,
                        agent.status.label(),
                        agent.stats
                    ))
                    .size(LabelSize::XSmall)
                    .color(Color::Muted)
                    .truncate(),
                )
        });
        let items: Vec<AnyElement> = (0..self.items.len())
            .map(|index| self.render_item(index, window, cx))
            .collect();
        v_flex().size_full().children(header).child(
            div()
                .id("subagent-transcript")
                .flex_1()
                .min_h(px(0.))
                .overflow_y_scroll()
                .track_scroll(&self.scroll)
                .child(
                    v_flex()
                        .w_full()
                        .max_w(px(860.))
                        .mx_auto()
                        .px(px(24.))
                        .py(px(16.))
                        .gap(px(12.))
                        .children(items)
                        .when(self.items.is_empty(), |this| {
                            this.child(
                                Label::new("Nothing yet.")
                                    .size(LabelSize::Small)
                                    .color(Color::Muted),
                            )
                        }),
                ),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn reads_the_fleet_the_extension_writes() {
        let fleet = parse_fleet(&json!({
            "version": 1,
            "agents": [
                {"id": "a1", "name": "auth-scout", "description": "find auth", "role": "Explore",
                 "model": "faux/faux-1", "status": "running", "activity": "bash", "stats": "3s · 10 tokens"},
                {"id": "a2", "name": "planner", "status": "completed"},
                {"name": "no id is skipped"}
            ]
        }));
        assert_eq!(fleet.len(), 2);
        assert_eq!(fleet[0].name, "auth-scout");
        assert_eq!(fleet[0].status, Status::Running);
        assert_eq!(fleet[1].status, Status::Completed);
        assert_eq!(fleet[1].role, "");
    }

    #[test]
    fn turns_pi_messages_into_items_with_tool_results_attached() {
        let items = parse_transcript(&json!({
            "messages": [
                {"role": "user", "content": "look for auth"},
                {"role": "assistant", "content": [
                    {"type": "thinking", "thinking": "hmm"},
                    {"type": "text", "text": "Checking."},
                    {"type": "toolCall", "id": "c1", "name": "bash", "arguments": {"command": "rg auth\nmore"}}
                ]},
                {"role": "toolResult", "toolCallId": "c1", "content": [{"type": "text", "text": "src/auth.rs"}], "isError": false},
                {"role": "assistant", "content": [{"type": "text", "text": "Found **it**."}]}
            ],
            "streaming": null
        }));
        assert_eq!(
            items,
            vec![
                Item::User("look for auth".into()),
                Item::Assistant("Checking.".into()),
                Item::Tool {
                    call_id: "c1".into(),
                    name: "bash".into(),
                    detail: "rg auth".into(),
                    output: "src/auth.rs".into(),
                    failed: false,
                    done: true
                },
                Item::Assistant("Found **it**.".into()),
            ]
        );
    }

    #[test]
    fn an_answer_in_progress_shows_with_its_running_tools() {
        let items = parse_transcript(&json!({
            "messages": [{"role": "user", "content": [{"type": "text", "text": "go"}]}],
            "streaming": {"role": "assistant", "content": [
                {"type": "text", "text": "Working"},
                {"type": "toolCall", "id": "c9", "name": "read", "arguments": {"path": "a.txt"}}
            ]}
        }));
        assert_eq!(items.len(), 3);
        assert!(matches!(&items[2], Item::Tool { done: false, name, .. } if name == "read"));
    }

    #[test]
    fn session_ids_become_safe_folder_names() {
        assert_eq!(sanitize("01a2:b/c"), "01a2_b_c");
        let root = Path::new("root");
        assert_eq!(session_folder(root, "x:y"), root.join("x_y"));
    }
}
