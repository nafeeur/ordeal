"""Ordeal's forensic, keyboard-first verification cockpit."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Container, Grid, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import (
    Button, ContentSwitcher, DataTable, Input, Label, OptionList, Select,
    Static,
)
from textual.widgets.option_list import Option

from ordeal_cli import Client, OrdealError


@dataclass(frozen=True)
class Tokens:
    background: str = "#050d12"
    sunken: str = "#081319"
    panel: str = "#0c1b22"
    raised: str = "#122b33"
    border: str = "#1b3a43"
    hairline: str = "#2d5660"
    primary: str = "#29d3c2"
    focus: str = "#8be7ff"
    success: str = "#53e0a1"
    warning: str = "#e7b65a"
    danger: str = "#ff6b7a"
    text: str = "#e7fafc"
    muted: str = "#7fa4ad"


T = Tokens()
ASCII = os.getenv("ORDEAL_ASCII", "0") == "1"
FROZEN = os.getenv("ORDEAL_FROZEN_UI", "0") == "1" or os.getenv("NO_MOTION", "0") == "1"
GLYPH = {
    "online": "*" if ASCII else "●", "pass": "+" if ASCII else "●",
    "fail": "x" if ASCII else "×", "running": "~" if ASCII else "◐",
    "branch": ">" if ASCII else "◆", "line": "-" if ASCII else "━",
    "track": "." if ASCII else "░", "fill": "#" if ASCII else "█",
}


CSS = f"""
$bg:{T.background};$sunken:{T.sunken};$panel:{T.panel};$raised:{T.raised};
$border:{T.border};$hairline:{T.hairline};$primary:{T.primary};$focus:{T.focus};
$success:{T.success};$warning:{T.warning};$danger:{T.danger};$text:{T.text};$muted:{T.muted};

Screen {{ background:$bg; color:$text; }}
#chrome {{ dock:top; height:6; background:$bg; }}
#topbar {{ height:3; padding:1 2; background:$panel; color:$text; }}
#nav {{ height:3; padding:1 2 0 2; background:$bg; color:$muted; }}
#status-rail {{ dock:bottom; height:2; padding:0 2; background:$sunken; color:$muted; border-top:solid $border; }}
#view {{ height:1fr; background:$bg; }}
.screen {{ height:1fr; padding:0 1; }}
.panel {{ background:$panel; border:round $border; padding:1 2; }}
.panel-title {{ color:$muted; text-style:bold; height:1; }}
.eyebrow {{ color:$primary; text-style:bold; }}
.focus-line {{ color:$focus; }} .success {{ color:$success; }} .warning {{ color:$warning; }} .failure {{ color:$danger; }} .muted {{ color:$muted; }}
#gate {{ column-span:2; border-left:thick $primary; }}
#system {{ column-span:1; }}
#coverage {{ column-span:3; }}
#recent {{ column-span:3; padding:0; }}
#overview-grid {{ layout:grid; grid-size:3 3; grid-columns:2fr 2fr 1fr; grid-rows:9 8 1fr; grid-gutter:1; height:1fr; }}
#gate-verdict {{ color:$success; text-style:bold; margin-top:1; }}
#gate-name {{ color:$text; text-style:bold; }}
#beam {{ height:1; color:$primary; }}
DataTable {{ background:$panel; color:$text; border:none; }}
DataTable > .datatable--header {{ background:$sunken; color:$muted; text-style:bold; }}
DataTable > .datatable--cursor {{ background:$raised; color:$focus; }}
#runs-table {{ height:1fr; border-left:thick $primary; }}
#evidence-layout {{ height:1fr; }}
#failure-list {{ width:38%; min-width:32; border-left:thick $danger; }}
#evidence-detail {{ width:62%; height:1fr; border-left:thick $focus; }}
#evidence-body {{ height:auto; }}
#evidence-actions {{ height:3; dock:bottom; align:right middle; }}
Button {{ background:$panel; color:$focus; border:tall $border; min-width:14; margin-left:1; }}
Button:hover,Button:focus {{ background:$raised; color:$focus; border:tall $focus; }}
Button.-primary {{ background:$primary; color:$bg; border:tall $primary; text-style:bold; }}
Input,Select {{ background:$sunken; color:$text; border:tall $border; height:3; }}
Input:focus,Select:focus {{ border:tall $focus; }}
#campaign-shell {{ width:86; max-width:100%; height:auto; align:center top; margin-top:1; border-left:thick $primary; }}
#runtime-shell {{ width:96; max-width:100%; height:1fr; align:center top; margin-top:1; border-left:thick $focus; }}
#runtime-result {{ height:1fr; margin-top:1; padding:1; background:$sunken; border-top:solid $border; }}
.step {{ height:4; margin-bottom:1; }} .step-number {{ width:5; color:$primary; text-style:bold; content-align:center middle; }} .step-field {{ width:1fr; }}
#estimate {{ height:3; color:$muted; padding:1 0; }} #run-result {{ height:auto; min-height:2; margin-top:1; }}
#palette {{ width:72; height:18; background:$panel; border:round $focus; padding:1; }}
#palette-title {{ height:1; color:$primary; text-style:bold; }} #palette-list {{ height:1fr; background:$panel; border:none; }}
#help {{ width:66; height:auto; background:$panel; border:round $primary; padding:2 3; }}
#toast-rack {{ dock:bottom; offset-y:-2; width:58; height:auto; max-height:8; align:right bottom; margin-right:2; }} .toast {{ height:2; background:$raised; border-left:thick $focus; padding:0 1; margin-top:1; }}
.compact-only {{ display:none; }}
.medium #overview-grid {{ grid-size:2 3; grid-columns:2fr 1fr; grid-rows:9 8 1fr; }}
.medium #gate {{ column-span:1; }} .medium #coverage,.medium #recent {{ column-span:2; }}
.medium #failure-list {{ width:34%; }} .medium #evidence-detail {{ width:66%; }}
.medium .wide-only {{ display:none; }} .medium .compact-only {{ display:block; }}
.compact #overview-grid {{ grid-size:1 4; grid-columns:1fr; grid-rows:8 6 7 1fr; }}
.compact #gate,.compact #system,.compact #coverage,.compact #recent {{ column-span:1; row-span:1; }}
.compact #evidence-layout {{ layout:vertical; }} .compact #failure-list,.compact #evidence-detail {{ width:100%; height:1fr; }}
.compact #nav {{ height:2; padding:0 1; }} .compact .optional {{ display:none; }}
"""


def meter(value: int, total: int, width: int = 20) -> str:
    ratio = value / total if total else 0
    filled = max(0, min(width, round(ratio * width)))
    return GLYPH["fill"] * filled + GLYPH["track"] * (width - filled)


class VerificationBeam(Static):
    running = reactive(False)
    passed = reactive(None)
    phase = reactive(0)

    def on_mount(self) -> None:
        if not FROZEN:
            self.set_interval(0.12, self.tick)
        self.render_beam()

    def tick(self) -> None:
        if self.running:
            self.phase = (self.phase + 1) % 32
            self.render_beam()

    def set_state(self, *, running: bool = False, passed: bool | None = None) -> None:
        self.running, self.passed = running, passed
        self.render_beam()

    def render_beam(self) -> None:
        width = max(20, self.size.width or 48)
        label = " VERIFYING " if self.running else " VERIFIED " if self.passed is True else " COUNTEREVIDENCE " if self.passed is False else " READY "
        side = max(2, (width - len(label)) // 2)
        left = GLYPH["line"] * side
        if self.running:
            pos = self.phase % max(1, side)
            left = left[:pos] + "╾" + left[pos + 1:] if not ASCII else left[:pos] + ">" + left[pos + 1:]
        colour = T.focus if self.running else T.success if self.passed is True else T.danger if self.passed is False else T.primary
        self.update(f"[{colour}]{left}{label}{GLYPH['line'] * side}[/]")


class ChromeBar(Static):
    def set_data(self, app: "OrdealTUI") -> None:
        branch = app.current_commit[:8] if app.current_commit else "workspace"
        online = f"[{T.success}]{GLYPH['online']} ONLINE[/]" if app.online else f"[{T.danger}]{GLYPH['fail']} OFFLINE[/]"
        self.update(f"[bold {T.primary}]ORDEAL[/]  [{T.hairline}]·[/]  [dim]TARGET /[/] {app.target_name}  [{T.hairline}]·[/]  [dim]REV /[/] {branch}  [{T.hairline}]·[/]  [dim]ENGINE /[/] DETERMINISTIC  [{T.hairline}]·[/]  {online}")


class CommandPalette(ModalScreen[str | None]):
    COMMANDS = [
        ("overview", "Open overview", "1"), ("runs", "Open verification runs", "2"),
        ("campaign", "Create verification campaign", "3"), ("evidence", "Inspect counterevidence", "4"),
        ("runtime", "Verify an autonomous runtime trace", "5"),
        ("refresh", "Refresh verifier telemetry", "R"), ("replay", "Replay selected counterexample", "R"),
        ("shrink", "Shrink selected counterexample", "S"), ("capture", "Save deterministic UI capture", ""),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="palette"):
            yield Label("ORDEAL // COMMAND", id="palette-title")
            yield Input(placeholder="Type a command…", id="palette-input")
            yield OptionList(id="palette-list")

    def on_mount(self) -> None:
        self.query_one("#palette-input", Input).focus()
        self.fill("")

    def fill(self, query: str) -> None:
        words = query.lower().split()
        options = [Option(f"{label:<42} [dim]{key}[/]", id=command) for command, label, key in self.COMMANDS if all(word in f"{command} {label}".lower() for word in words)]
        target = self.query_one("#palette-list", OptionList)
        target.clear_options(); target.add_options(options)

    @on(Input.Changed, "#palette-input")
    def filter(self, event: Input.Changed) -> None: self.fill(event.value)

    @on(Input.Submitted, "#palette-input")
    def submit_input(self) -> None:
        listing = self.query_one("#palette-list", OptionList)
        if listing.highlighted is not None:
            self.dismiss(str(listing.get_option_at_index(listing.highlighted).id))

    @on(OptionList.OptionSelected, "#palette-list")
    def select(self, event: OptionList.OptionSelected) -> None: self.dismiss(str(event.option.id))


class HelpScreen(ModalScreen):
    def compose(self) -> ComposeResult:
        with Vertical(id="help"):
            yield Label("ORDEAL // COMMAND MAP", classes="eyebrow")
            yield Static("1–5  switch view     ↑↓  navigate     enter  inspect\n"
                         "r    refresh         R   replay       S      shrink\n"
                         ":    command palette ?   help         q      quit", classes="muted")
            yield Button("CLOSE", id="close-help")

    @on(Button.Pressed, "#close-help")
    def close(self) -> None: self.dismiss()


class OrdealTUI(App):
    CSS = CSS
    TITLE = "ORDEAL"
    COLOR_SYSTEM = "truecolor"
    BINDINGS = [
        ("q", "quit", "Quit"), ("r", "refresh", "Refresh"), ("question_mark", "help", "Help"),
        ("colon", "palette", "Command"), ("ctrl+k", "palette", "Command"),
        ("1", "tab('overview')", "Overview"), ("2", "tab('runs')", "Runs"),
        ("3", "tab('campaign')", "Campaign"), ("4", "tab('evidence')", "Evidence"),
        ("5", "tab('runtime')", "Runtime"),
        ("shift+r", "replay", "Replay"), ("shift+s", "shrink", "Shrink"),
    ]

    active_view = reactive("overview")
    online = reactive(False)
    target_name = reactive("local")
    current_commit = reactive("")

    def __init__(self, api_url: str, api_key: str | None):
        super().__init__()
        self.client = Client(api_url, api_key)
        self.runs: list[dict[str, Any]] = []
        self.agents: list[dict[str, Any]] = []
        self.suites: list[dict[str, Any]] = []
        self.selected_run: dict[str, Any] | None = None
        self.selected_trial: dict[str, Any] | None = None
        self.started_at: float | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="chrome"):
            yield ChromeBar(id="topbar")
            yield Static(id="nav")
        with ContentSwitcher(initial="overview", id="view"):
            with Container(id="overview", classes="screen"):
                with Grid(id="overview-grid"):
                    with Vertical(id="gate", classes="panel"):
                        yield Label("CURRENT GATE", classes="panel-title")
                        yield Static(f"[{T.muted}]NO VERDICT YET[/]", id="gate-verdict")
                        yield Static("Run a campaign to establish evidence.", id="gate-name", classes="muted")
                        yield VerificationBeam(id="beam")
                    with Vertical(id="system", classes="panel"):
                        yield Label("SYSTEM", classes="panel-title")
                        yield Static("API       CONNECTING\nWORKERS            —\nQUEUE              —\nWORLDS             —\nADAPTERS           —", id="system-body")
                    with Vertical(id="coverage", classes="panel"):
                        yield Label("VERIFICATION BOUNDARY", classes="panel-title")
                        yield Static("No campaign evidence loaded.", id="coverage-body")
                    with Vertical(id="recent", classes="panel"):
                        yield Label("RECENT CAMPAIGNS", classes="panel-title")
                        yield DataTable(id="recent-table", cursor_type="row")
            with Container(id="runs", classes="screen"):
                yield Static("VERIFICATION RUNS  /  reproducible campaigns and fingerprints", classes="panel-title")
                yield DataTable(id="runs-table", cursor_type="row", zebra_stripes=True)
            with Container(id="campaign", classes="screen"):
                with Vertical(id="campaign-shell", classes="panel"):
                    yield Label("NEW VERIFICATION", classes="eyebrow")
                    yield Static("Define the trusted boundary, then ask the candidate to prove itself.", classes="muted")
                    with Horizontal(classes="step"):
                        yield Label("01", classes="step-number"); yield Input(value="candidate", placeholder="Campaign name", id="run-name", classes="step-field")
                    with Horizontal(classes="step"):
                        yield Label("02", classes="step-number"); yield Select([], prompt="Select agent", id="run-agent", classes="step-field")
                    with Horizontal(classes="step"):
                        yield Label("03", classes="step-number"); yield Select([], prompt="Select trusted suite", id="run-suite", classes="step-field")
                    with Horizontal(classes="step"):
                        yield Label("04", classes="step-number"); yield Select([("1 repetition",1),("3 repetitions",3),("5 repetitions",5),("10 repetitions",10)], value=3, id="run-reps", classes="step-field")
                    with Horizontal(classes="step"):
                        yield Label("05", classes="step-number"); yield Input(value="41", placeholder="Deterministic seed", id="run-seed", classes="step-field")
                    yield Static("ESTIMATE  select an agent and suite", id="estimate")
                    yield Button("RUN ORDEAL", id="run-button", variant="primary")
                    yield Static("", id="run-result")
            with Container(id="evidence", classes="screen"):
                with Horizontal(id="evidence-layout"):
                    with Vertical(id="failure-list", classes="panel"):
                        yield Label("COUNTEREVIDENCE", classes="panel-title")
                        yield DataTable(id="failures-table", cursor_type="row")
                    with Vertical(id="evidence-detail", classes="panel"):
                        yield Label("BEHAVIORAL TRACE", classes="panel-title")
                        yield VerticalScroll(Static("Select a failed trial to inspect its proof.", id="evidence-body"))
                        with Horizontal(id="evidence-actions"):
                            yield Button("REPLAY  R", id="replay-button")
                            yield Button("SHRINK  S", id="shrink-button")
            with Container(id="runtime", classes="screen"):
                with Vertical(id="runtime-shell", classes="panel"):
                    yield Label("AUTONOMOUS RUNTIME", classes="eyebrow")
                    yield Static("Verify any observed trajectory against a deterministic contract. The target never grades itself.", classes="muted")
                    with Horizontal(classes="step"):
                        yield Label("01", classes="step-number")
                        yield Input(value="examples/model-native-runtime/verified.json", placeholder="Execution bundle (.json)", id="runtime-path", classes="step-field")
                    yield Button("VERIFY TRACE", id="runtime-button", variant="primary")
                    yield VerticalScroll(Static("Load a captured execution to inspect policy, provenance, and integrity evidence.", id="runtime-result"))
        yield Vertical(id="toast-rack")
        yield Static(id="status-rail")

    def on_mount(self) -> None:
        recent = self.query_one("#recent-table", DataTable); recent.add_columns("", "CAMPAIGN", "VERDICT", "TRIALS")
        runs = self.query_one("#runs-table", DataTable); runs.add_columns("ID", "CAMPAIGN", "STATE", "PASS", "FAIL", "FINGERPRINT")
        failures = self.query_one("#failures-table", DataTable); failures.add_columns("", "SCENARIO", "SEED")
        self.render_nav(); self.render_status("CONNECTING TO VERIFIER")
        self.action_refresh()

    def on_resize(self, event) -> None:
        self.screen.set_class(event.size.width < 72, "compact")
        self.screen.set_class(72 <= event.size.width < 91, "medium")

    def render_nav(self) -> None:
        items = [("overview","OVERVIEW"),("runs","RUNS"),("campaign","NEW CAMPAIGN"),("evidence","EVIDENCE"),("runtime","RUNTIME")]
        chunks = []
        for key, label in items:
            chunks.append(f"[{T.background} on {T.primary}] {label} [/]" if key == self.active_view else f"[{T.muted}] {label} [/]")
        self.query_one("#nav", Static).update("   ".join(chunks))

    def render_status(self, message: str | None = None) -> None:
        if message:
            body = message
        else:
            state = f"[{T.success}]{GLYPH['pass']} READY[/]" if self.online else f"[{T.danger}]{GLYPH['fail']} OFFLINE[/]"
            body = f"{state}   {len(self.runs)} RUNS   {len(self.suites)} SUITES   [dim]R REFRESH   : COMMANDS   ? HELP[/]"
        self.query_one("#status-rail", Static).update(body)

    def action_tab(self, tab_id: str) -> None:
        self.active_view = tab_id; self.query_one("#view", ContentSwitcher).current = tab_id; self.render_nav()

    def action_help(self) -> None: self.push_screen(HelpScreen())
    def action_palette(self) -> None: self.push_screen(CommandPalette(), self.run_command)
    def action_refresh(self) -> None: self.load_data()
    def action_replay(self) -> None: self.replay_selected()
    def action_shrink(self) -> None: self.shrink_selected()

    def run_command(self, command: str | None) -> None:
        if not command: return
        if command in {"overview","runs","campaign","evidence","runtime"}: self.action_tab(command)
        elif command == "refresh": self.action_refresh()
        elif command == "replay": self.action_replay()
        elif command == "shrink": self.action_shrink()
        elif command == "capture":
            path = self.save_screenshot()
            self.toast(f"CAPTURED  {path}", "success")

    def toast(self, message: str, tone: str = "info") -> None:
        colour = {"success":T.success,"error":T.danger,"warning":T.warning}.get(tone,T.focus)
        rack = self.query_one("#toast-rack", Vertical)
        note = Static(f"[{colour}]{GLYPH['branch']}[/] {message}", classes="toast")
        rack.mount(note)
        if not FROZEN: self.set_timer(4.2, note.remove)

    @work(exclusive=True, group="load", thread=True)
    def load_data(self) -> None:
        try:
            summary = self.client.request("/api/summary")
            runs = self.client.request("/api/runs")
            agents = self.client.request("/api/agents")
            suites = self.client.request("/api/suites")
            self.call_from_thread(self.render_data, summary, runs, agents, suites)
        except OrdealError as exc: self.call_from_thread(self.render_error, str(exc))

    def render_error(self, message: str) -> None:
        self.online = False; self.query_one("#topbar", ChromeBar).set_data(self); self.render_status(); self.toast(message, "error")

    def render_data(self, s: dict[str,Any], runs: list[dict[str,Any]], agents: list[dict[str,Any]], suites: list[dict[str,Any]]) -> None:
        self.online = True; self.runs = runs; self.agents = agents; self.suites = suites
        self.target_name = self.client.base_url.removeprefix("http://").removeprefix("https://")
        self.query_one("#topbar", ChromeBar).set_data(self); self.render_status()
        self.query_one("#system-body", Static).update(f"API       [{T.success}]ONLINE[/]\nWORKERS   {s.get('online_workers',0):>10}\nQUEUE     {str(s.get('queue_backend','local')).upper():>10}\nWORLDS    {s.get('worlds',0):>10}\nADAPTERS  {s.get('adapter_count',0):>10}")
        self.refresh_run_tables(); self.refresh_selects(); self.render_latest_gate(); self.render_coverage(s)

    def refresh_selects(self) -> None:
        agent = self.query_one("#run-agent", Select); suite = self.query_one("#run-suite", Select)
        agent.set_options([(x["name"],x["name"]) for x in self.agents]); suite.set_options([(x["name"],x["name"]) for x in self.suites])

    def refresh_run_tables(self) -> None:
        recent = self.query_one("#recent-table", DataTable); runs = self.query_one("#runs-table", DataTable); recent.clear(); runs.clear()
        for r in self.runs:
            p=r.get("payload",{}); fail=int(p.get("fail_count",0)); state=r.get("status","—").upper(); glyph=GLYPH["running"] if state in {"RUNNING","QUEUED"} else GLYPH["fail"] if fail else GLYPH["pass"]
            verdict="RUNNING" if state in {"RUNNING","QUEUED"} else "FAIL" if fail else "VERIFIED"
            recent.add_row(glyph,r.get("name","—"),verdict,str(len(p.get("trials",[]))),key=str(r.get("id")))
            runs.add_row(str(r.get("id")),r.get("name","—"),state,str(p.get("pass_count","—")),str(p.get("fail_count","—")),(p.get("run_fingerprint") or "—")[:12],key=str(r.get("id")))

    def render_latest_gate(self) -> None:
        complete=next((r for r in self.runs if r.get("status")=="completed"),None)
        beam=self.query_one("#beam",VerificationBeam)
        if not complete: beam.set_state(); return
        p=complete.get("payload",{}); passed=not p.get("fail_count") and not (p.get("constraint_summary") or {}).get("violations")
        self.current_commit=p.get("commit_sha") or ""
        self.query_one("#gate-verdict",Static).update(f"[bold {T.success if passed else T.danger}]{GLYPH['pass'] if passed else GLYPH['fail']} {'VERIFIED' if passed else 'BLOCKED'}[/]")
        self.query_one("#gate-name",Static).update(f"{complete.get('name')}  /  {p.get('pass_count',0)} passed  /  {p.get('fail_count',0)} failed")
        beam.set_state(passed=passed); self.query_one("#topbar",ChromeBar).set_data(self)

    def render_coverage(self, s: dict[str,Any]) -> None:
        complete=next((r for r in self.runs if r.get("status")=="completed"),None); p=(complete or {}).get("payload",{})
        passed=int(p.get("pass_count",0)); total=len(p.get("trials",[])); constraints=int(s.get("constraints",0)); scenarios=int(s.get("scenarios",0))
        self.query_one("#coverage-body",Static).update(
            f"TRIALS       {passed:>4}/{total:<4} [{T.primary}]{meter(passed,total,24)}[/]\n"
            f"SCENARIOS    {scenarios:>4}      [{T.focus}]{meter(scenarios,max(scenarios,10),24)}[/]\n"
            f"CONSTRAINTS  {constraints:>4}      [{T.success}]{meter(constraints,max(constraints,10),24)}[/]"
        )

    @on(Select.Changed, "#run-agent")
    @on(Select.Changed, "#run-suite")
    @on(Select.Changed, "#run-reps")
    def update_estimate(self) -> None:
        suite_name=self.query_one("#run-suite",Select).value; reps=self.query_one("#run-reps",Select).value
        spec=next((x.get("payload",{}) for x in self.suites if x.get("name")==suite_name),{})
        count=len(spec.get("scenarios",[]))*int(reps if reps is not Select.BLANK else 1)
        self.query_one("#estimate",Static).update(f"ESTIMATE  {count} trials  /  deterministic seeds  /  trusted constraints outside agent control")

    @on(DataTable.RowSelected, "#recent-table")
    @on(DataTable.RowSelected, "#runs-table")
    def select_run(self,event:DataTable.RowSelected)->None:
        run=next((x for x in self.runs if str(x.get("id"))==str(event.row_key.value)),None)
        if run:self.load_evidence(run)

    def load_evidence(self, run: dict[str,Any]) -> None:
        self.selected_run=run; table=self.query_one("#failures-table",DataTable); table.clear()
        failures=[t for t in run.get("payload",{}).get("trials",[]) if not t.get("passed")]
        if not failures:
            for t in run.get("payload",{}).get("trials",[])[:20]: table.add_row(GLYPH["pass"],t.get("scenario","—"),str(t.get("seed","—")),key=t.get("id") or f"{t.get('scenario')}:{t.get('seed')}")
            self.query_one("#evidence-body",Static).update(f"[bold {T.success}]{GLYPH['pass']} NO COUNTEREVIDENCE[/]\n\nAll {len(run.get('payload',{}).get('trials',[]))} trials remained within the declared verification boundary.\n\nFINGERPRINT\n{run.get('payload',{}).get('run_fingerprint','—')}")
        else:
            for t in failures: table.add_row(GLYPH["fail"],t.get("scenario","—"),str(t.get("seed","—")),key=t.get("id") or f"{t.get('scenario')}:{t.get('seed')}")
            self.render_trial(failures[0])
        self.action_tab("evidence")

    @on(DataTable.RowSelected,"#failures-table")
    def select_trial(self,event:DataTable.RowSelected)->None:
        if not self.selected_run:return
        key=str(event.row_key.value); trial=next((t for t in self.selected_run.get("payload",{}).get("trials",[]) if (t.get("id") or f"{t.get('scenario')}:{t.get('seed')}")==key),None)
        if trial:self.render_trial(trial)

    def render_trial(self,t:dict[str,Any])->None:
        self.selected_trial=t; grades=[g for g in t.get("grades",[])+t.get("constraint_grades",[]) if not g.get("passed")]
        lines=[f"[bold {T.danger}]{GLYPH['fail']} {t.get('scenario')}[/]",f"SEED  {t.get('seed')}    STATE  {t.get('initial_hash','—')} → {t.get('final_hash','—')}","",f"[bold {T.focus}]BEHAVIORAL TIMELINE[/]"]
        for event in t.get("events",[])[:40]:
            kind=event.get("type","event"); tool=event.get("tool"); fault=event.get("fault")
            glyph=GLYPH["fail"] if fault else GLYPH["branch"] if tool else GLYPH["pass"]
            detail=f"{tool}({event.get('occurrence','')})" if tool else kind
            if fault: detail+=f"  [{T.danger}]FAULT {fault.get('inject',fault)}[/]"
            lines.append(f"[{T.hairline}]│[/]\n[{T.focus if not fault else T.danger}]{glyph}[/] {detail}")
            if event.get("state_before") and event.get("state_after") and event["state_before"]!=event["state_after"]: lines.append(f"  [dim]state {event['state_before']} → {event['state_after']}[/]")
        if grades:
            lines.extend(["",f"[bold {T.danger}]VIOLATIONS[/]"])
            for g in grades: lines.append(f"{GLYPH['fail']} {g.get('name',g.get('type','constraint'))}  expected={g.get('expected','—')}  actual={g.get('actual','—')}")
        self.query_one("#evidence-body",Static).update("\n".join(lines))

    @on(Button.Pressed,"#run-button")
    def start_run(self)->None:self.submit_run()

    @work(exclusive=True,group="run",thread=True)
    def submit_run(self)->None:
        get=lambda selector:self.call_from_thread(lambda:self.query_one(selector).value)
        try:
            agent,suite=get("#run-agent"),get("#run-suite")
            if agent is Select.BLANK or suite is Select.BLANK: raise ValueError("select an agent and trusted suite")
            body={"name":get("#run-name"),"agent":agent,"suite":suite,"seed":int(get("#run-seed") or "1"),"repetitions":int(get("#run-reps"))}
            self.call_from_thread(self.begin_run); result=self.client.request("/api/runs","POST",body); self.call_from_thread(self.finish_run,result)
        except (OrdealError,ValueError) as exc:self.call_from_thread(self.fail_run,str(exc))

    def begin_run(self)->None:
        self.started_at=time.monotonic();self.query_one("#beam",VerificationBeam).set_state(running=True);self.query_one("#run-button",Button).disabled=True;self.query_one("#run-result",Static).update(f"[{T.focus}]{GLYPH['running']} VERIFYING[/]  exploring deterministic behavior…");self.render_status("VERIFYING  /  evidence stream active")
    def finish_run(self,result:dict[str,Any])->None:
        p=result.get("payload",{});passed=not p.get("fail_count");self.query_one("#beam",VerificationBeam).set_state(passed=passed);self.query_one("#run-button",Button).disabled=False;self.query_one("#run-result",Static).update(f"[bold {T.success if passed else T.danger}]{'VERIFIED' if passed else 'BLOCKED'}[/]  {p.get('pass_count',0)} passed / {p.get('fail_count',0)} failed / fingerprint {p.get('run_fingerprint','—')}");self.toast("VERDICT RECORDED", "success" if passed else "error");self.action_refresh()
    def fail_run(self,message:str)->None:
        self.query_one("#beam",VerificationBeam).set_state(passed=False);self.query_one("#run-button",Button).disabled=False;self.query_one("#run-result",Static).update(f"[{T.danger}]ERROR[/] {message}");self.toast(message,"error");self.render_status()

    @on(Button.Pressed,"#runtime-button")
    def runtime_button(self)->None:self.submit_runtime()

    @work(exclusive=True,group="runtime",thread=True)
    def submit_runtime(self)->None:
        try:
            path=self.call_from_thread(lambda:self.query_one("#runtime-path",Input).value)
            with open(path,encoding="utf-8") as handle: body=json.load(handle)
            self.call_from_thread(self.begin_runtime)
            result=self.client.request("/api/runtime/verify","POST",body)
            self.call_from_thread(self.finish_runtime,result)
        except (OSError,json.JSONDecodeError,OrdealError) as exc:self.call_from_thread(self.fail_runtime,str(exc))

    def begin_runtime(self)->None:
        self.query_one("#runtime-button",Button).disabled=True
        self.query_one("#runtime-result",Static).update(f"[{T.focus}]{GLYPH['running']} VERIFYING[/]  normalizing events and evaluating contract…")

    def finish_runtime(self,result:dict[str,Any])->None:
        self.query_one("#runtime-button",Button).disabled=False
        verdict=result.get("verdict","INCOMPLETE"); colour=T.success if verdict=="PASS" else T.danger if verdict=="FAIL" else T.warning
        summary=result.get("summary",{}); lines=[f"[bold {colour}]{verdict}[/]  {result.get('execution','—')}",f"CONTRACT     {result.get('contract','—')}",f"SCHEMA       {result.get('contract_schema','—')}",f"FINGERPRINT  {result.get('fingerprint','—')}",f"CHAIN HEAD   {result.get('evidence',{}).get('chain_head','—')}","",f"EVENTS {summary.get('events',0)}   POLICIES {summary.get('policies',0)}   PASSED {summary.get('passed',0)}   VIOLATED {summary.get('violated',0)}"]
        for report in result.get("adapter_conformance",[]):
            adapter=report.get("adapter",{}); marker=GLYPH["pass"] if report.get("compatible") else GLYPH["fail"]
            lines.append(f"{marker} ADAPTER {adapter.get('kind','?')}/{adapter.get('name','unselected')}")
        failures=result.get("violations",[])
        if failures:
            lines.extend(["",f"[bold {T.danger}]COUNTEREVIDENCE[/]"])
            for item in failures:lines.append(f"{GLYPH['fail']} {item.get('policy')}  {item.get('reason')}\n  evidence={item.get('evidence',[])}  actual={item.get('actual')}")
        else:lines.extend(["",f"[{T.success}]{GLYPH['pass']} No counterevidence within the declared boundary.[/]"])
        for item in result.get("unresolved",[]):lines.append(f"[{T.warning}]? {item.get('policy','contract')}  {item.get('reason')}[/]")
        self.query_one("#runtime-result",Static).update("\n".join(lines));self.toast(f"RUNTIME VERDICT / {verdict}","success" if verdict=="PASS" else "error" if verdict=="FAIL" else "warning")

    def fail_runtime(self,message:str)->None:
        self.query_one("#runtime-button",Button).disabled=False;self.query_one("#runtime-result",Static).update(f"[{T.danger}]ERROR[/] {message}");self.toast(message,"error")

    @on(Button.Pressed,"#replay-button")
    def replay_button(self)->None:self.replay_selected()
    @work(exclusive=True,group="evidence-action",thread=True)
    def replay_selected(self)->None:
        if not self.selected_run or not self.selected_trial:self.call_from_thread(self.toast,"SELECT COUNTEREVIDENCE FIRST","warning");return
        try:
            out=self.client.request("/api/replay","POST",{"run_id":self.selected_run["id"],"scenario":self.selected_trial["scenario"],"seed":self.selected_trial.get("seed")})
            message="REPLAY MATCHED / deterministic" if out.get("deterministic_match") else "REPLAY DIVERGED / investigate nondeterminism"
            self.call_from_thread(self.toast,message,"success" if out.get("deterministic_match") else "error")
        except OrdealError as exc:self.call_from_thread(self.toast,str(exc),"error")

    @on(Button.Pressed,"#shrink-button")
    def shrink_button(self)->None:self.shrink_selected()
    @work(exclusive=True,group="evidence-action",thread=True)
    def shrink_selected(self)->None:
        if not self.selected_trial:self.call_from_thread(self.toast,"SELECT COUNTEREVIDENCE FIRST","warning");return
        t=self.selected_trial
        try:
            self.call_from_thread(self.query_one("#shrink-button",Button).update,"SHRINKING…")
            out=self.client.request("/api/lab/shrink","POST",{"agent":t.get("agent_snapshot",{}).get("name",""),"scenario":t.get("scenario_snapshot",{}),"world":t.get("world_snapshot",{}),"seed":t.get("seed",1)})
            self.call_from_thread(self.toast,out.get("explanation",out.get("reason","SHRINK COMPLETE")),"success")
        except OrdealError as exc:self.call_from_thread(self.toast,str(exc),"error")
        finally:self.call_from_thread(self.query_one("#shrink-button",Button).update,"SHRINK  S")


def run_tui(api_url: str, api_key: str | None = None) -> None:
    OrdealTUI(api_url, api_key).run()
