"""One PNG that shows what the example plugins do, from real headless runs.

    python -I script/debug/plugin-gallery.py [out.png] [--only ask,secrets,grill,doom] [--doom-python PATH]

Each plugin gets its own sandbox and app (headless, side by side). A scripted
fake model (script/debug/scripted-model.ts) calls the plugin's tool, the
automation answers like a user, and the chat is captured at the interesting
moments. Prints one line per stage with its time. DOOM needs a Python with
ViZDoom (`--doom-python`, default .scratch/doom-venv); it is skipped without.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
args = sys.argv[1:]
ONLY = args[args.index("--only") + 1].split(",") if "--only" in args else None
DOOM_PYTHON = (args[args.index("--doom-python") + 1] if "--doom-python" in args
               else os.path.join(ROOT, ".scratch", "doom-venv", "Scripts", "python.exe"))
positional = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] not in ("--only", "--doom-python"))]
OUT = os.path.abspath(positional[0]) if positional else os.path.join(ROOT, ".scratch", "shots", "plugins.png")

sys.argv = [sys.argv[0]]  # the smoke script parses its own arguments on import
spec = importlib.util.spec_from_file_location("smoke", os.path.join(ROOT, "script", "windows-smoke.py"))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
STARTED = time.time()


def log(text):
    print(f"[{time.time() - STARTED:6.1f} s] {text}", flush=True)


# What each plugin is asked to do, and when to take pictures. "shots" are
# (caption, automation commands before the shot).
SCENARIOS = {
    "ask": {
        "steps": [{"tool": "ask_user_question", "args": {"questions": [
            {"question": "Which database should the new service use?", "details": "Both are already in production.",
             "options": [{"label": "PostgreSQL (Recommended)", "description": "Fits the reporting queries"},
                         {"label": "SQLite", "description": "One file, no server"}]},
            {"question": "Anything else I should know?"}]}},
            {"echo": "Thanks! "}],
        "prompt": "set up the new service",
        "shots": [("Ask: the agent's questions as a form in the chat", ["key down", "key space"]),
                  ("Ask: the answers go back to the agent",
                   ["key enter", "wait 300", "type keep it small", "key enter", "wait 1500"])],
    },
    "secrets": {
        "steps": [{"tool": "request_secret", "args": {
            "name": "DEMO_API_KEY", "description": "Your API key from the provider's dashboard."}},
            {"echo": ""}],
        "prompt": "deploy with my API key",
        "shots": [("Secrets: a masked field; the agent never sees the value", ["type sk-demo-1234567890"]),
                  ("Secrets: the agent only learns where it was saved", ["key enter", "wait 1500"])],
    },
    "grill": {
        "steps": [{"tool": "grill_round", "args": {"round": 2, "progress": "3 settled, 3 open", "questions": [
            {"title": "Ship the beta on Friday?",
             "body": "The release train leaves Friday 5pm. QA signed off on the core flows; billing has two open bugs.",
             "recommended": "Yes, ship Friday with billing behind a feature flag"},
            {"title": "Which database for the reporting service?", "choices": ["SQLite", "PostgreSQL", "DuckDB"],
             "recommended": "PostgreSQL: it already runs in production", "recommendedChoice": 1},
            {"title": "Who owns on-call for launch week?", "recommended": "The platform team, as usual"}]}},
            {"echo": ""}],
        "prompt": "grill me on the launch plan",
        "shots": [("Grill: a grilling round as a form, the recommendation first", []),
                   ("Grill: the agent gets each decision back",
                    ["key 1", "key enter", "wait 300", "key 3", "key enter", "wait 300", "key down",
                     "type Maya, she wrote the billing code", "key enter", "wait 1500"])],
    },
    "doom": {
        "steps": [{"tool": "doom", "args": {}}, {"text": "Have fun!"}],
        "prompt": "can you run doom?",
        "shots": [("DOOM: a live view the plugin streams into the tool call",
                   ["wait 4000", "click 726 330", "wait 300", "key up", "key up", "key up", "wait 1500"])],
    },
}


def run_plugin(name, sandbox):
    """Runs one plugin's scenario; returns [(caption, png path)]."""
    smoke.join_headless_desktop()  # per thread
    scenario = SCENARIOS[name]
    home, auto, project = (os.path.join(sandbox, f"{name}-{part}") for part in ("home", "auto", "project"))
    for folder in (home, auto, project):
        os.makedirs(folder)
    plugin_dir = os.path.join(home, ".config", "pi-gpui", "plugins", name)
    shutil.copytree(os.path.join(ROOT, "examples", "plugins", name), plugin_dir)
    if name == "doom":
        # Windows cannot run ./run.sh: start doom.py with a Python that has ViZDoom.
        manifest = os.path.join(plugin_dir, "plugin.json")
        data = json.load(open(manifest))
        data["run"] = [DOOM_PYTHON, "doom.py"]
        json.dump(data, open(manifest, "w"))
    agent = smoke.fake_pi_agent(sandbox, f"{name}-agent", "scripted-model.ts")
    steps = os.path.join(sandbox, f"{name}-steps.json")
    json.dump(scenario["steps"], open(steps, "w"))
    # USERPROFILE too: node's home folder, where Secrets saves the value.
    env = dict(smoke.offline_environ(), HOME=home, USERPROFILE=home, PI_GPUI_AUTOMATION=auto,
               PI_CODING_AGENT_DIR=agent, PI_SCRIPTED_MODEL=steps)
    env.pop("PI_EXECUTABLE", None)
    started = time.time()
    app = subprocess.Popen([smoke.EXE, project], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    driver = smoke.Automation(auto)
    shots = []
    try:
        if not smoke.wait_for(lambda: smoke.app_window(app.pid), 20):
            raise RuntimeError("no window")
        driver.settle(sandbox)
        log(f"{name}: app ready ({time.time() - started:.1f} s)")
        baseline = smoke.chat_accent(driver, sandbox)
        driver.send(f"type {scenario['prompt']}", "key enter")
        # Wait for the tool call itself (a spinner never lets the window settle).
        tool = scenario["steps"][0]["tool"]
        sessions = os.path.join(agent, "sessions")
        called = lambda: any(tool in open(os.path.join(folder, file), encoding="utf-8").read()
                             for folder, _, files in os.walk(sessions) for file in files if file.endswith(".jsonl"))
        if not smoke.wait_for(called, 20, 0.1):
            raise RuntimeError(f"pi never called {tool}")
        if name != "doom" and not smoke.wait_for_form(driver, sandbox, baseline):
            raise RuntimeError("the plugin's form never appeared")
        log(f"{name}: tool called ({time.time() - started:.1f} s)")
        for index, (caption, commands) in enumerate(scenario["shots"]):
            moment = time.time()
            if commands:
                driver.send(*commands)
            driver.send("move 1170 790", "wait 300")
            path = os.path.join(sandbox, f"{name}-{index}.png")
            driver.send(f"shot {path}")
            shots.append((caption, path))
            log(f"{name}: shot {index + 1}/{len(scenario['shots'])} ({time.time() - moment:.1f} s)")
    finally:
        if app.poll() is None:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(app.pid)], capture_output=True)
    return shots


def compose(shots, out):
    """Chat area of each shot, captioned, in a two-column grid."""
    tiles = []
    for index, (caption, path) in enumerate(shots):
        tile = os.path.join(os.path.dirname(path), f"tile-{index}.png")
        # The chat column without sidebar and title bar, trimmed to its content.
        subprocess.run(["magick", path, "-crop", "880x715+290+82", "+repage",
                        "-bordercolor", "#0b0d12", "-border", "1", "-fuzz", "4%", "-trim", "+repage",
                        "-background", "#0b0d12", "-gravity", "north", "-extent", "880x",
                        "-fill", "#e8e8e8", "-font", "Segoe-UI",
                        "-pointsize", "22", "-gravity", "center", f"label:{caption}",
                        "-append", "-bordercolor", "#0b0d12", "-border", "12", tile], check=True)
        tiles.append(tile)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run(["magick", "montage", *tiles, "-tile", "2x", "-geometry", "+8+8",
                    "-background", "#1b1f27", out], check=True)


def main():
    names = [name for name in (ONLY or SCENARIOS)]
    if "doom" in names and not os.path.isfile(DOOM_PYTHON):
        log(f"doom: skipped (no ViZDoom Python at {DOOM_PYTHON})")
        names.remove("doom")
    smoke.join_headless_desktop()
    sandbox = tempfile.mkdtemp(prefix="pi-gpui-gallery-")
    results = {}
    try:
        log(f"started {len(names)} plugin(s) side by side: {', '.join(names)}")
        with ThreadPoolExecutor(len(names)) as pool:
            futures = {pool.submit(run_plugin, name, sandbox): name for name in names}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                    log(f"{name}: done")
                except Exception as error:
                    log(f"{name}: FAILED {error!r}")
        shots = [shot for name in names for shot in results.get(name, [])]
        moment = time.time()
        compose(shots, OUT)
        log(f"composed {len(shots)} picture(s) ({time.time() - moment:.1f} s): {OUT}")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    main()
