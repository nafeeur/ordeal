import test from 'node:test';
import assert from 'node:assert/strict';
import {OrdealClient, OrdealError, redact} from '../dist/index.js';
const opts = {baseUrl:'http://127.0.0.1:8080', token:'private-token', projectId:'project'};

test('redaction preserves structure while removing sensitive values', () => {
  const x = redact({password:'hidden', input: {email:'person@example.com'}, encoded:'{"api_key":"secret-key"}'});
  assert.equal(x.password,'[REDACTED]');
  assert.equal(x.input.email,'[EMAIL]');
  assert.ok(!JSON.stringify(x).includes('secret-key'));
  assert.throws(()=>redact({number:NaN}));
});
test('requests cannot leak credentials to a caller-controlled origin', async () => {
  let calls = 0;
  const client = new OrdealClient({...opts, fetch: async ()=>{calls++; return new Response('{}')}});
  for (const path of ['https://evil.test', '//evil.test', '/\\evil.test']) await assert.rejects(client.request('GET',path));
  assert.equal(calls,0);
});
test('typed API errors retain status but not credentials', async () => {
  const client = new OrdealClient({...opts, fetch:async ()=> new Response('{"detail":"Denied"}',{status:403,headers:{'x-request-id':'id'}})});
  await assert.rejects(client.getResource('x'), e => e instanceof OrdealError && e.status === 403 && e.requestId === 'id' && e.message === 'Denied');
});
test('trace records ordered behavior, unknown cost, and redacts before transport', async () => {
  let sent;
  const client = new OrdealClient({...opts, fetch: async (url, init) => {
    assert.equal(init.redirect,'error'); sent = JSON.parse(init.body);
    return new Response(JSON.stringify({id:'stored',data:sent}));
  }});
  const value = await client.withTrace('support', async trace=> {
    const result = await trace.step('lookup', {email:'person@example.com'}, ()=>({ok:true}));
    trace.status = 'pass'; return result;
  });
  assert.deepEqual(value,{ok:true});
  assert.equal(sent.status,'pass');
  assert.equal(sent.trajectory.events.length,2);
  assert.equal(sent.trajectory.events[0].payload.arguments.email,'[EMAIL]');
  assert.ok(!('cost_usd' in sent.usage));
});
test('export failures are explicit and do not mask application failure', async () => {
  const client = new OrdealClient({...opts, fetch: async ()=>{throw new Error('offline')}});
  const trace = client.trace('t');
  await trace.finish();
  assert.ok(trace.lastError);
  await assert.rejects(client.withTrace('bad',async()=>{throw new Error('original')},{failOpen:false}),/original/);
});
test('strict export failure is not masked by a sealed-trace error', async () => {
  const client = new OrdealClient({...opts, fetch: async ()=>{throw new Error('export-offline')}});
  await assert.rejects(client.withTrace('bad',async()=> 'okay',{failOpen:false}),/export-offline/);
});
test('invalid measurement is rejected before the tool executes', async () => {
  const client = new OrdealClient({...opts, fetch: async ()=>new Response('{}')});
  let executed = false;
  await assert.rejects(client.trace('t').step('read',{},()=>{executed=true;return {};},{usage:{total_tokens:NaN}}));
  assert.equal(executed,false);
});
test('fail-open export records serialization failures', async () => {
  const client = new OrdealClient({...opts, fetch: async ()=>new Response('{}')});
  const trace = client.trace('bad-json'); trace.output = NaN;
  assert.equal(await trace.finish(),null); assert.ok(trace.lastError);
});
