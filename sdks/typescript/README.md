# @ordeal/agent 0.3.0

Dependency-free TypeScript/JavaScript telemetry and control-plane SDK, with strict declarations. Node 20+ is supported by the source; the tested runtime is recorded in the release report. This package is not published to npm. Install the included `.tgz` or `npm install /path/to/sdks/typescript`.

```typescript
import { OrdealClient } from '@ordeal/agent';
const client = new OrdealClient({
  baseUrl: 'http://127.0.0.1:8080',
  token: process.env.ORDEAL_API_KEY!,
  projectId: process.env.ORDEAL_PROJECT_ID!,
});
await client.withTrace('support', async trace => {
  const order = await trace.step('lookup_order', {id:'A100'}, async () => ({paid:true}));
  return order;
});
const dataset = await client.create('dataset', 'Incidents', {cases:[]});
```

`trace.finish()` and `client.ingest()` return an ingestion receipt. Fetch the full record using `getTrace(receipt.id)`. Unknown cost/token fields are omitted, not invented as zeros. Scope parent spans explicitly with `parentSpanId`; concurrent callbacks do not rely on a global parent. The default production status is `unset`. Failed telemetry export records `lastError` and returns null by default; pass `failOpen:false` for strict export failure.

Methods include versioned resources, pagination, promotion, traces, evaluation, job submission, and report upload. Credentials are not sent to arbitrary URLs or redirects. This is **not yet a TypeScript port of the Python simulation engine**. Real framework behavior must be routed through instrumented tools; the client does not automatically intercept every operation in an application.

Build with `npm run build`; test with `npm test`. Prebuilt `dist/` is included so consumption does not require a compiler.
