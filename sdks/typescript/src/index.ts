/** No global instrumentation and no external runtime dependencies. */
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Verdict = 'pass' | 'fail' | 'incomplete' | 'error' | 'unset';
export type ResourceKind = 'dataset' | 'world' | 'scenario' | 'evaluator' | 'agent' | 'prompt' |
  'monitor' | 'connector' | 'automation' | 'experiment' | 'bundle' | 'incident' | 'review_queue';
export interface Resource<T = Record<string, Json>> {
  id: string; tenant_id: string; project_id: string; kind: ResourceKind;
  name: string; version: number; head: number; data: T; content_hash: string;
  aliases: Record<string, number>;
}
export interface Page<T> {items: T[]; total: number; offset: number; limit: number; has_more: boolean}
export interface Usage {input_tokens?: number; output_tokens?: number; total_tokens?: number; cost_usd?: number}
export interface TraceEvent {seq: number; kind: string; name: string; time_ns: number; payload: Record<string, Json>}
export interface SpanRecord {span_id: string; parent_span_id: string | null; name: string; kind: string;
  start_ns: number; end_ns: number; input: Json; output: Json; status: string; usage: Usage}
export interface TraceEnvelope {
  trace_id: string; name: string; environment: string; agent_version?: string; status: Verdict;
  duration_ms: number; usage: Usage; spans: SpanRecord[];
  trajectory: {events: TraceEvent[]; initial_state: Json; final_state: Json; final_output: Json};
  metadata: Record<string, Json>;
}
export interface IngestReceipt {id: string; trace_id: string; content_hash: string; created: boolean}
export interface TraceRecord {id: string; trace_id: string; name: string; status: Verdict; data: TraceEnvelope; content_hash: string}
export interface Job {id: string; state: 'queued' | 'leased' | 'succeeded' | 'failed' | 'cancelled';
  kind: 'suite' | 'evaluate'; payload: Record<string, Json>; result: Record<string, Json> | null}
export interface ClientOptions {baseUrl: string; token: string; projectId: string; timeoutMs?: number; fetch?: typeof fetch}
export class OrdealError extends Error {
  constructor(message: string, readonly status: number, readonly requestId: string | null) {
    super(message); this.name = 'OrdealError';
  }
}
export interface RedactionOptions {pii?: boolean; suppressInputs?: boolean; suppressOutputs?: boolean; sensitiveFields?: string[]}
const sensitive = new Set(['password','passwd','authorization','api_key','apikey','secret','access_token',
  'refresh_token','cookie','set-cookie','client_secret']);
export function redact(value: unknown, options: RedactionOptions = {}, depth = 0): Json {
  if (depth > 40) return '[DEPTH_LIMIT]';
  if (value === null || value === undefined) return null;
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new TypeError('Non-finite telemetry number');
    return value;
  }
  if (Array.isArray(value)) return value.map(v => redact(v, options, depth + 1));
  if (typeof value === 'object') {
    const result: Record<string, Json> = Object.create(null) as Record<string, Json>;
    for (const [key, val] of Object.entries(value)) {
      const lower = key.toLowerCase();
      const secret = sensitive.has(lower) || sensitive.has(lower.replaceAll('-','_')) ||
        (options.sensitiveFields || []).some(k => k.toLowerCase() === lower);
      const input = options.suppressInputs && (['input','inputs','arguments','instruction','prompt','input.value'].includes(lower) || lower.startsWith('gen_ai.prompt') || lower.startsWith('llm.input_messages'));
      const output = options.suppressOutputs && (['output','outputs','result','final_output','output.value'].includes(lower) || lower.startsWith('gen_ai.completion') || lower.startsWith('llm.output_messages'));
      result[key] = secret || input || output ? '[REDACTED]' : redact(val, options, depth + 1);
    }
    return result;
  }
  if (typeof value !== 'string') throw new TypeError('Telemetry must be JSON serializable');
  let text = value;
  if (/^\s*[\[{]/.test(text) && text.length <= 1048576) {
    try {
      const decoded: unknown = JSON.parse(text);
      if (decoded && typeof decoded === 'object') return JSON.stringify(redact(decoded, options, depth + 1));
    } catch { /* A plain string need not be JSON. */ }
  }
  text = text.replace(/\b(?:sk-[A-Za-z0-9_-]{12,}|ord_[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})\b/g, '[SECRET]');
  if (options.pii !== false) text = text.replace(/\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g, '[EMAIL]')
    .replace(/\b\d{3}-\d{2}-\d{4}\b/g, '[SSN]');
  return text;
}
const id = (): string => crypto.randomUUID().replaceAll('-', '');
export class OrdealClient {
  readonly projectId: string;
  private readonly base: string;
  private readonly token: string;
  private readonly timeout: number;
  private readonly transport: typeof fetch;
  constructor(options: ClientOptions) {
    const url = new URL(options.baseUrl);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || (url.pathname !== '/' && url.pathname !== ''))
      throw new TypeError('baseUrl must be an HTTP(S) origin without credentials, paths, or query');
    if (!options.token || !options.projectId) throw new TypeError('A token and projectId are required');
    this.base = url.origin; this.token = options.token; this.projectId = options.projectId;
    this.timeout = options.timeoutMs ?? 20000;
    if (!Number.isFinite(this.timeout) || this.timeout <= 0) throw new TypeError('timeoutMs must be positive');
    this.transport = options.fetch ?? fetch;
  }
  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    if (!path.startsWith('/') || path.startsWith('//') || /[\r\n\\]/.test(path))
      throw new TypeError('Use a relative API path; credentials must never leave the configured origin');
    const response = await this.transport(this.base + path, {method, redirect: 'error', credentials: 'omit',
      headers: {'Authorization': `Bearer ${this.token}`, ...(body === undefined ? {} : {'Content-Type':'application/json'})},
      ...(body === undefined ? {} : {body: JSON.stringify(body)}), signal: AbortSignal.timeout(this.timeout)});
    if (!response.ok) {
      let message = `Ordeal HTTP ${response.status}`;
      try {const error = await response.json() as {detail?: unknown}; if (typeof error.detail === 'string') message = error.detail.slice(0, 2000);} catch { /* retain status */ }
      throw new OrdealError(message, response.status, response.headers.get('x-request-id'));
    }
    return (response.status === 204 ? undefined : await response.json()) as T;
  }
  private projectPath(): string {return `/api/v1/projects/${encodeURIComponent(this.projectId)}`}
  create<T extends Record<string, Json>>(kind: ResourceKind, name: string, data: T): Promise<Resource<T>> {
    return this.request('POST', this.projectPath() + '/resources', {kind, name, data});
  }
  getResource<T = Record<string, Json>>(resourceId: string, version?: number): Promise<Resource<T>> {
    return this.request('GET', `/api/v1/resources/${encodeURIComponent(resourceId)}` + (version ? `?version=${version}` : ''));
  }
  update<T extends Record<string, Json>>(resourceId: string, baseVersion: number, data: T): Promise<Resource<T>> {
    return this.request('POST', `/api/v1/resources/${encodeURIComponent(resourceId)}/versions`, {base_version: baseVersion, data});
  }
  promote(resourceId: string, version: number, environment: 'development'|'staging'|'production'): Promise<unknown> {
    return this.request('POST', `/api/v1/resources/${encodeURIComponent(resourceId)}/promote`, {environment, version});
  }
  async *resources<T = Record<string, Json>>(kind?: ResourceKind): AsyncGenerator<Resource<T>> {
    let offset = 0;
    for (let pages = 0; pages < 10000; pages++) {
      const page = await this.request<Page<Resource<T>>>('GET', this.projectPath() + `/resources?offset=${offset}&limit=100` + (kind ? `&kind=${kind}` : ''));
      for (const item of page.items) yield item;
      if (!page.has_more) return;
      if (!page.items.length) throw new Error('Server pagination made no progress');
      offset += page.items.length;
    }
    throw new Error('Pagination safety limit reached');
  }
  ingest(envelope: TraceEnvelope): Promise<IngestReceipt> {return this.request('POST', this.projectPath() + '/traces', envelope)}
  getTrace(traceId: string): Promise<TraceRecord> {return this.request('GET', `/api/v1/traces/${encodeURIComponent(traceId)}`)}
  evaluate(traceId: string, evaluatorId: string, version: number): Promise<unknown> {
    return this.request('POST', `/api/v1/traces/${encodeURIComponent(traceId)}/evaluate`, {evaluator_id: evaluatorId, version});
  }
  enqueue(suitePath: string, options: {labels?: string[]; estimatedCostUsd?: number; idempotencyKey?: string} = {}): Promise<Job> {
    return this.request('POST', this.projectPath() + '/jobs', {kind:'suite', payload:{suite_path:suitePath},
      labels:options.labels ?? [], estimated_cost_usd: options.estimatedCostUsd ?? 0, idempotency_key:options.idempotencyKey ?? id()});
  }
  uploadReport(report: Record<string, Json>, name = `report-${id().slice(0, 8)}`): Promise<Resource> {
    return this.create('experiment', name, {report});
  }
  trace(name: string, options: TraceOptions = {}): TraceSession {return new TraceSession(this, name, options)}
  async withTrace<T>(name: string, callback: (trace: TraceSession) => Promise<T>, options: TraceOptions = {}): Promise<T> {
    const trace = this.trace(name, options);
    let output: T;
    try {output = await callback(trace);}
    catch (error) {
      trace.status = 'error'; trace.event('error', 'agent', {type:error instanceof Error ? error.name : 'Error'});
      try {await trace.finish()} catch { /* Preserve the application's original error. */ }
      throw error;
    }
    trace.output = output as Json;
    await trace.finish();
    return output;
  }
}
export interface TraceOptions extends RedactionOptions {environment?: string; agentVersion?: string; failOpen?: boolean}
export interface StepOptions {kind?: 'tool'|'model'|'handoff'; parentSpanId?: string; usage?: Usage}
export class TraceSession {
  readonly traceId = id();
  status: Verdict = 'unset';
  output: Json = null;
  initialState: Json = {};
  finalState: Json = {};
  metadata: Record<string, Json> = {};
  lastError: unknown;
  response: IngestReceipt | null = null;
  private readonly started = performance.now();
  private readonly events: TraceEvent[] = [];
  private readonly spans: SpanRecord[] = [];
  private readonly usage: Usage = {};
  private active = 0;
  private sealed: TraceEnvelope | null = null;
  constructor(private client: OrdealClient, readonly name: string, private options: TraceOptions) {}
  event(kind: string, name: string, payload: Record<string, Json>, time = Date.now() * 1e6): void {
    if (this.sealed) throw new Error('Trace is already sealed');
    this.events.push({seq:this.events.length, kind, name, time_ns:time, payload:structuredClone(payload)});
  }
  async step<T extends Json>(name: string, args: Json, handler: (spanId: string) => T | Promise<T>, options: StepOptions = {}): Promise<T> {
    if (this.sealed) throw new Error('Trace is already sealed');
    const spanId = id().slice(0,16), start = Date.now() * 1e6, kind = options.kind ?? 'tool';
    const usage = {...(options.usage ?? {})};
    for (const [metric, value] of Object.entries(usage)) {
      if (!Number.isFinite(value) || value < 0 || (metric !== 'cost_usd' && !Number.isSafeInteger(value)))
        throw new TypeError('Invalid usage measurement');
    }
    this.active++;
    this.event(kind === 'tool' ? 'tool_call' : kind === 'model' ? 'model_call' : 'handoff', name, {arguments:args,call_id:spanId}, start);
    let output: Json = null, status = 'ok';
    try {
      output = structuredClone(await handler(spanId));
      this.event(kind === 'tool' ? 'tool_result' : 'model_result', name, {result:output, call_id:spanId});
      return output as T;
    } catch (error) {
      status = 'error'; this.status = 'error';
      this.event('error', name, {type:error instanceof Error ? error.name : 'Error', call_id:spanId}); throw error;
    } finally {
      this.active--;
      for (const metric of ['input_tokens','output_tokens','total_tokens','cost_usd'] as const) {
        const value = usage[metric];
        if (value !== undefined) {
          this.usage[metric] = (this.usage[metric] ?? 0) + value;
        }
      }
      this.spans.push({span_id:spanId,parent_span_id:options.parentSpanId ?? null,name,kind,
        start_ns:start,end_ns:Date.now()*1e6,input:structuredClone(args),output,status,usage:{...usage}});
    }
  }
  async finish(): Promise<IngestReceipt | null> {
    if (this.response) return this.response;
    if (this.active) throw new Error('Await all trace steps before finishing');
    try {
    if (!this.sealed) {
      const envelope: TraceEnvelope = {trace_id:this.traceId,name:this.name,environment:this.options.environment ?? 'production',
        ...(this.options.agentVersion ? {agent_version:this.options.agentVersion} : {}),status:this.status,
        duration_ms:performance.now()-this.started,usage:this.usage,spans:this.spans,
        trajectory:{events:this.events,initial_state:this.initialState,final_state:this.finalState,final_output:this.output},
        metadata:{...this.metadata,source:'ordeal-typescript',completeness:'instrumented_scope_only'}};
      this.sealed = redact(envelope,this.options) as unknown as TraceEnvelope;
    }
    this.response = await this.client.ingest(this.sealed); return this.response;}
    catch (error) {this.lastError = error; if (this.options.failOpen === false) throw error; return null;}
  }
}
