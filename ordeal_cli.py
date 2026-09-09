#!/usr/bin/env python3
import argparse,json,urllib.request,sys
API='http://localhost:8000'
def req(path,method='GET',body=None):
    data=json.dumps(body).encode() if body is not None else None
    r=urllib.request.Request(API+path,data=data,method=method,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(r) as f:return json.loads(f.read())
def main():
    p=argparse.ArgumentParser(prog='ordeal');p.add_argument('--api',default=API);sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('seed-demo');sub.add_parser('status')
    r=sub.add_parser('run');r.add_argument('--name',default='candidate');r.add_argument('--agent',required=True);r.add_argument('--suite',required=True);r.add_argument('--seed',type=int,default=1)
    c=sub.add_parser('compare');c.add_argument('baseline',type=int);c.add_argument('candidate',type=int);c.add_argument('--fail-on-regression',action='store_true')
    a=p.parse_args()
    globals()['API']=a.api
    if a.cmd=='seed-demo': out=req('/api/demo/seed','POST',{})
    elif a.cmd=='status': out=req('/api/summary')
    elif a.cmd=='run': out=req('/api/runs','POST',{'name':a.name,'agent':a.agent,'suite':a.suite,'seed':a.seed})
    else:
        out=req('/api/compare','POST',{'baseline_run_id':a.baseline,'candidate_run_id':a.candidate})
        if a.fail_on_regression and out.get('regressions',0)>0:
            print(json.dumps(out,indent=2));sys.exit(2)
    print(json.dumps(out,indent=2))
if __name__=='__main__':main()
