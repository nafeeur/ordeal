#!/usr/bin/env python3
"""Ordeal's terminal entrypoint."""
from __future__ import annotations
import argparse,json,os,sys,urllib.error,urllib.request
from typing import Any

DEFAULT_API=os.getenv("ORDEAL_API_URL","http://localhost:8000")
class OrdealError(RuntimeError): pass

class Client:
    def __init__(self,base_url:str,api_key:str|None=None,timeout:int=180):
        self.base_url=base_url.rstrip('/'); self.api_key=api_key or os.getenv("ORDEAL_API_KEY"); self.timeout=timeout
    def request(self,path:str,method:str="GET",body:Any=None)->Any:
        headers={"Accept":"application/json","Content-Type":"application/json"}
        if self.api_key: headers["X-Ordeal-Key"]=self.api_key
        req=urllib.request.Request(self.base_url+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as response:
                raw=response.read(); return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail=exc.read().decode(errors="replace")
            try: detail=json.loads(detail).get("detail",detail)
            except json.JSONDecodeError: pass
            raise OrdealError(f"API returned {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc: raise OrdealError(f"cannot reach {self.base_url}: {exc.reason}") from exc

def emit(value:Any)->None: print(json.dumps(value,indent=2,sort_keys=True,default=str))
def verdict(run:dict[str,Any])->tuple[bool,dict[str,Any]]:
    p=run.get("payload",run); stability=p.get("stability") or {}; constraints=p.get("constraint_summary") or {}
    failed=int(p.get("fail_count",0)); violations=int(constraints.get("violations",0)); unstable=int(stability.get("in_variance_count",0))
    proof={"verdict":"FAIL" if failed or violations or unstable else "PASS","scope":"verified within tested boundary","trials":len(p.get("trials",[])),"passed":int(p.get("pass_count",0)),"failed":failed,"constraint_violations":violations,"unstable_scenarios":unstable,"fingerprint":p.get("run_fingerprint")}
    return proof["verdict"]=="PASS",proof

def parser()->argparse.ArgumentParser:
    p=argparse.ArgumentParser(prog="ordeal",description="Verify software whose behavior is decided at runtime by models.")
    p.add_argument("--api",default=DEFAULT_API); p.add_argument("--api-key"); p.add_argument("--json",action="store_true")
    s=p.add_subparsers(dest="command"); s.add_parser("tui",help="open the terminal interface"); s.add_parser("status"); s.add_parser("seed-demo"); s.add_parser("runs")
    r=s.add_parser("run",help="execute a verification campaign"); r.add_argument("--name",default="candidate"); r.add_argument("--agent",required=True); r.add_argument("--suite",required=True); r.add_argument("--seed",type=int,default=1); r.add_argument("--repetitions",type=int,default=1); r.add_argument("--concurrency",type=int,default=16); r.add_argument("--baseline",type=int); r.add_argument("--commit"); r.add_argument("--distributed",action="store_true"); r.add_argument("--fail-on-verdict",action="store_true")
    g=s.add_parser("gate",help="use a run as a deployment gate"); g.add_argument("run_id",type=int)
    x=s.add_parser("replay",help="reproduce a stored counterexample"); x.add_argument("run_id",type=int); x.add_argument("scenario"); x.add_argument("--seed",type=int)
    c=s.add_parser("compare",help="attribute candidate regressions"); c.add_argument("baseline",type=int); c.add_argument("candidate",type=int); c.add_argument("--fail-on-regression",action="store_true")
    v=s.add_parser("verify-runtime",help="verify an observed model-native execution"); v.add_argument("spec",help="JSON execution bundle"); v.add_argument("--fail-on-verdict",action="store_true")
    return p

def main(argv:list[str]|None=None)->int:
    args=parser().parse_args(argv)
    if args.command in {None,"tui"}:
        try: from ordeal_tui import run_tui
        except ImportError as exc: raise OrdealError("TUI dependency missing; run: pip install -r backend/requirements.txt") from exc
        run_tui(args.api,args.api_key); return 0
    client=Client(args.api,args.api_key)
    if args.command=="status": out=client.request("/api/summary")
    elif args.command=="seed-demo": out=client.request("/api/demo/seed","POST",{})
    elif args.command=="runs": out=client.request("/api/runs")
    elif args.command=="run":
        out=client.request("/api/runs","POST",{"name":args.name,"agent":args.agent,"suite":args.suite,"seed":args.seed,"repetitions":args.repetitions,"concurrency":args.concurrency,"baseline_run_id":args.baseline,"commit_sha":args.commit,"distributed":args.distributed})
        if not args.distributed:
            passed,proof=verdict(out); emit(out if args.json else proof); return 0 if passed or not args.fail_on_verdict else 2
    elif args.command=="gate":
        out=client.request(f"/api/runs/{args.run_id}/gate"); emit(out); return 2 if out.get("blocking") else 0
    elif args.command=="replay":
        out=client.request("/api/replay","POST",{"run_id":args.run_id,"scenario":args.scenario,"seed":args.seed}); emit(out); return 0 if out.get("deterministic_match") else 2
    elif args.command=="verify-runtime":
        try:
            with open(args.spec,encoding="utf-8") as handle: spec=json.load(handle)
        except (OSError,json.JSONDecodeError) as exc: raise OrdealError(f"cannot read runtime spec: {exc}") from exc
        out=client.request("/api/runtime/verify","POST",spec); emit(out)
        return 2 if args.fail_on_verdict and out.get("blocking") else 0
    else:
        out=client.request("/api/compare","POST",{"baseline_run_id":args.baseline,"candidate_run_id":args.candidate}); emit(out)
        return 2 if args.fail_on_regression and int(out.get("new_regressions",out.get("regressions",0))) else 0
    emit(out); return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except OrdealError as exc: print(f"ordeal: {exc}",file=sys.stderr); raise SystemExit(1)
