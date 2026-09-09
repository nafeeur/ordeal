'use client';
import {useEffect,useMemo,useState} from 'react';
import {API} from '../lib';

type AnyRow=Record<string,any>;
async function get(path:string){try{const r=await fetch(`${API}${path}`,{cache:'no-store'});return r.ok?await r.json():null}catch{return null}}
export default function Enterprise(){
 const [summary,setSummary]=useState<AnyRow>({}),[workers,setWorkers]=useState<any[]>([]),[jobs,setJobs]=useState<any[]>([]),[runs,setRuns]=useState<any[]>([]);
 useEffect(()=>{Promise.all([get('/api/summary'),get('/api/enterprise/workers'),get('/api/enterprise/jobs'),get('/api/runs')]).then(([s,w,j,r])=>{setSummary(s||{});setWorkers(w||[]);setJobs(j||[]);setRuns(r||[])})},[]);
 const capacity=useMemo(()=>workers.reduce((n,w)=>n+(w.capacity||0),0),[workers]);
 const active=useMemo(()=>workers.reduce((n,w)=>n+(w.active_jobs||0),0),[workers]);
 const queued=jobs.filter(j=>j.status==='queued').length;
 const latest=runs[0]?.payload||{};
 return <>
  <div className="top"><div><div className="eyebrow">ENTERPRISE EXECUTION FABRIC / V1</div><h1>MISSION CONTROL</h1></div><div className="labSignal"><i></i>{workers.length?'FABRIC ONLINE':'NO WORKERS'}</div></div>
  <section className="enterpriseHero">
   <div><span className="eyebrow">CONTROL PLANE</span><h2>ONE WORLD.<br/>MILLIONS OF ORDEALS.</h2><p>Schedule agent versions, models, scenarios and seeds across horizontally scalable worker pools. Canonical state remains deterministic while optional model inference remains an external service.</p></div>
   <div className="fabricMap"><b>CONTROL</b><span>APACHE KAFKA</span><i>WORKERS</i><i>MODELS</i><em>LEDGER</em><small>RESULT STREAM</small></div>
  </section>
  <div className="verdictBand"><div><small>WORKERS</small><strong>{workers.length}</strong></div><div><small>CAPACITY</small><strong>{capacity}</strong></div><div><small>ACTIVE JOBS</small><strong>{active}</strong></div><div><small>QUEUED</small><strong>{queued}</strong></div></div>
  <section className="section enterpriseGrid">
   <div className="opsPanel"><div className="sectionTitle"><h2>EXECUTION FABRIC</h2><span>Kafka consumer groups / heartbeat / retry</span></div>{workers.length?workers.map(w=><div className="workerLine" key={w.worker_id}><b>{w.worker_id}</b><span>{(w.capabilities||[]).join(' + ')}</span><span>{w.labels?.pool||'CPU'}</span><span>{w.active_jobs}/{w.capacity}</span></div>):<div className="emptyInstrument">REGISTER A WORKER TO ARM THE FABRIC</div>}</div>
   <div className="opsPanel"><div className="sectionTitle"><h2>LAST EXPERIMENT</h2><span>{runs[0]?.name||'—'}</span></div><div className="dial"><div><small>PASS</small><b>{((latest.pass_rate||0)*100).toFixed(1)}%</b></div><div><small>TRIALS</small><b>{latest.trials?.length||0}</b></div><div><small>FINGERPRINT</small><code>{latest.run_fingerprint||'unarmed'}</code></div></div></div>
  </section>
  <section className="section"><div className="sectionTitle"><h2>JOB STREAM</h2><span>Kafka-backed execution ledger · latest 500 jobs</span></div><div className="tableWrap"><table className="table"><thead><tr><th>ID</th><th>KIND</th><th>CAPABILITY</th><th>STATUS</th><th>WORKER</th><th>ATTEMPT</th></tr></thead><tbody>{jobs.slice(0,20).map(j=><tr key={j.id}><td>#{j.id}</td><td>{j.kind}</td><td>{j.required_capability}</td><td className={j.status==='failed'?'bad':j.status==='completed'?'good':''}>{j.status}</td><td>{j.worker_id||'—'}</td><td>{j.attempts}/{j.max_attempts}</td></tr>)}</tbody></table></div></section>
  <section className="section capabilityMatrix"><div><span>WORLD COMPILER</span><b>OPENAPI + MCP + TRACES</b></div><div><span>SIMULATOR</span><b>EMPIRICAL CONTRACTS + FIDELITY</b></div><div><span>SEARCH</span><b>COVERAGE-GUIDED ADVERSARIAL</b></div><div><span>DEBUG</span><b>SHRINK + CAUSAL + REPLAY</b></div><div><span>SECURITY</span><b>RBAC + API KEYS + AUDIT</b></div><div><span>STREAM</span><b>APACHE KAFKA + TRANSACTIONAL OUTBOX</b></div></section>
 </>
}
