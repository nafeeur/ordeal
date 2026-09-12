"""Keyboard-first full-screen interface for Ordeal."""
from __future__ import annotations
from typing import Any
from textual import on,work
from textual.app import App,ComposeResult
from textual.containers import Horizontal,Vertical,VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button,DataTable,Footer,Header,Input,Label,Static,TabbedContent,TabPane
from ordeal_cli import Client,OrdealError

THEME="""
$bg:#061116;$panel:#0a1b21;$panel2:#0d252b;$cyan:#86e7ff;$teal:#31d8c5;$muted:#6e97a0;$danger:#ff6b7a;
Screen{background:$bg;color:#d8f7fa} Header{background:$panel;color:$cyan;text-style:bold} Footer{background:$panel;color:$muted}
TabbedContent{background:$bg} Tabs{background:$panel;color:$muted} Tab.-active{color:$bg;background:$teal;text-style:bold}
.hero{height:5;padding:1 2;background:$panel;border-left:thick $teal}.brand{color:$cyan;text-style:bold}.muted{color:$muted}
.metric{width:1fr;height:5;margin:1;padding:1 2;background:$panel;border-top:solid $teal}.panel{margin:1;padding:1 2;background:$panel;border:round #17424a}
DataTable{margin:1;background:$panel;border:round #17424a} DataTable>.datatable--header{background:#10343b;color:$cyan;text-style:bold} DataTable>.datatable--cursor{background:#164950;color:white}
Button{background:$panel2;color:$cyan;border:tall $teal;min-width:16} Button:hover{background:$teal;color:$bg} Input{background:$panel;border:tall #27616a;color:white}
#connection{dock:bottom;height:1;color:$muted;padding-left:2} #run-form{height:auto} #run-form Input{width:1fr;margin-right:1} #evidence{height:1fr}.failure{color:$danger}.success{color:$teal}
ModalScreen{align:center middle;background:#061116 80%} #help{width:64;height:auto;padding:2 3;background:$panel;border:round $teal}
"""

class Help(ModalScreen):
    def compose(self)->ComposeResult:
        with Vertical(id="help"):
            yield Label("ORDEAL // COMMAND MAP",classes="brand"); yield Static("r refresh   enter inspect   1–4 switch view\n? commands   q quit",classes="muted"); yield Button("CLOSE",id="close-help")
    @on(Button.Pressed,"#close-help")
    def close(self)->None:self.dismiss()

class OrdealTUI(App):
    CSS=THEME; TITLE="ORDEAL"; SUB_TITLE="deterministic adversarial verification"
    BINDINGS=[("q","quit","Quit"),("r","refresh","Refresh"),("?","help","Commands"),("1","tab('overview')","Overview"),("2","tab('runs')","Runs"),("3","tab('campaign')","Campaign"),("4","tab('evidence')","Evidence")]
    def __init__(self,api_url:str,api_key:str|None): super().__init__(); self.client=Client(api_url,api_key); self.runs:list[dict[str,Any]]=[]
    def compose(self)->ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="overview"):
            with TabPane("OVERVIEW",id="overview"):
                yield Static("[bold #86e7ff]ORDEAL[/]  /  THE AGENT SAYS DONE. ORDEAL ASKS FOR PROOF.\n[dim]Deterministic evidence between autonomous code and production.[/]",classes="hero")
                with Horizontal():
                    yield Static("RUNS\n[bold #86e7ff]—[/]",id="m-runs",classes="metric"); yield Static("PASS RATE\n[bold #86e7ff]—[/]",id="m-rate",classes="metric"); yield Static("REGRESSIONS\n[bold #86e7ff]—[/]",id="m-regressions",classes="metric"); yield Static("WORKERS\n[bold #86e7ff]—[/]",id="m-workers",classes="metric")
                yield Static("Waiting for verifier telemetry…",id="overview-detail",classes="panel")
            with TabPane("RUNS",id="runs"): yield DataTable(id="runs-table",cursor_type="row",zebra_stripes=True)
            with TabPane("NEW CAMPAIGN",id="campaign"):
                yield Static("RUN A CANDIDATE AGAINST A TRUSTED SUITE",classes="hero")
                with Vertical(id="run-form",classes="panel"):
                    yield Input(placeholder="campaign name",value="candidate",id="run-name"); yield Input(placeholder="agent name",id="run-agent"); yield Input(placeholder="suite name",id="run-suite"); yield Input(placeholder="seed",value="1",id="run-seed"); yield Button("RUN VERIFICATION",id="run-button"); yield Static("",id="run-result")
            with TabPane("EVIDENCE",id="evidence"): yield VerticalScroll(Static("Select a run and press Enter.",id="evidence-body"),id="evidence-scroll")
        yield Static("CONNECTING",id="connection"); yield Footer()
    def on_mount(self)->None:
        self.query_one("#runs-table",DataTable).add_columns("ID","CAMPAIGN","STATE","PASS","FAIL","FINGERPRINT"); self.action_refresh()
    def action_tab(self,tab_id:str)->None:self.query_one(TabbedContent).active=tab_id
    def action_help(self)->None:self.push_screen(Help())
    def action_refresh(self)->None:self.load_data()
    @work(exclusive=True,thread=True)
    def load_data(self)->None:
        try:self.call_from_thread(self.render_data,self.client.request("/api/summary"),self.client.request("/api/runs"))
        except OrdealError as exc:self.call_from_thread(self.render_error,str(exc))
    def render_error(self,message:str)->None:
        x=self.query_one("#connection",Static);x.update(f"OFFLINE / {message}");x.add_class("failure")
    def render_data(self,s:dict[str,Any],runs:list[dict[str,Any]])->None:
        self.runs=runs; completed=[r for r in runs if r.get("status")=="completed"]; rates=[float(r.get("payload",{}).get("pass_rate",0)) for r in completed]; rate=sum(rates)/len(rates) if rates else 0
        for sel,label,value in [("#m-runs","RUNS",s.get("runs",0)),("#m-rate","PASS RATE",f"{rate:.0%}"),("#m-regressions","REGRESSIONS",s.get("recent_regressions",0)),("#m-workers","WORKERS",s.get("online_workers",0))]: self.query_one(sel,Static).update(f"{label}\n[bold #86e7ff]{value}[/]")
        self.query_one("#overview-detail",Static).update(f"QUEUE  {s.get('queue_backend','local').upper()}    SCENARIOS  {s.get('scenarios',0)}    SUITES  {s.get('suites',0)}    CONSTRAINTS  {s.get('constraints',0)}\n\n[bold #31d8c5]TRUST MODEL[/]  agent output is untrusted; deterministic constraints own the verdict")
        table=self.query_one("#runs-table",DataTable);table.clear()
        for r in runs:
            p=r.get("payload",{});table.add_row(str(r.get("id")),r.get("name","—"),r.get("status","—").upper(),str(p.get("pass_count","—")),str(p.get("fail_count","—")),(p.get("run_fingerprint") or "—")[:12],key=str(r.get("id")))
        x=self.query_one("#connection",Static);x.update(f"ONLINE / {self.client.base_url}");x.remove_class("failure")
    @on(DataTable.RowSelected,"#runs-table")
    def inspect(self,event:DataTable.RowSelected)->None:
        r=next((x for x in self.runs if str(x.get("id"))==str(event.row_key.value)),None)
        if not r:return
        p=r.get("payload",{}); failures=[t for t in p.get("trials",[]) if not t.get("passed")]
        lines=[f"[bold #86e7ff]CAMPAIGN {r.get('id')} // {r.get('name')}[/]",f"STATUS {r.get('status','').upper()}   PASS {p.get('pass_count',0)}   FAIL {p.get('fail_count',0)}",f"FINGERPRINT {p.get('run_fingerprint','—')}","","[bold #31d8c5]VERIFIED WITHIN TESTED BOUNDARY[/]" if not failures else "[bold #ff6b7a]COUNTEREVIDENCE FOUND[/]"]
        for t in failures[:20]:
            lines.append(f"\n[bold]× {t.get('scenario')}[/] seed={t.get('seed')} hash={t.get('final_hash','')[:12]}")
            for g in [g for g in t.get("grades",[])+t.get("constraint_grades",[]) if not g.get("passed")]: lines.append(f"  {g.get('type',g.get('name','constraint'))}: expected={g.get('expected')} actual={g.get('actual')}")
        self.query_one("#evidence-body",Static).update("\n".join(lines));self.action_tab("evidence")
    @on(Button.Pressed,"#run-button")
    def start(self)->None:self.submit_run()
    @work(exclusive=True,group="run",thread=True)
    def submit_run(self)->None:
        value=lambda selector:self.call_from_thread(lambda:self.query_one(selector,Input).value)
        try:
            body={"name":value("#run-name"),"agent":value("#run-agent"),"suite":value("#run-suite"),"seed":int(value("#run-seed") or "1")};self.call_from_thread(self.query_one("#run-result",Static).update,"RUNNING / collecting deterministic evidence…");result=self.client.request("/api/runs","POST",body);p=result.get("payload",{});v="PASS" if not p.get("fail_count") else "FAIL";self.call_from_thread(self.query_one("#run-result",Static).update,f"{v} / {p.get('pass_count',0)} passed / {p.get('fail_count',0)} failed");self.call_from_thread(self.action_refresh)
        except (OrdealError,ValueError) as exc:self.call_from_thread(self.query_one("#run-result",Static).update,f"ERROR / {exc}")

def run_tui(api_url:str,api_key:str|None=None)->None:OrdealTUI(api_url,api_key).run()
