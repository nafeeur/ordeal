import asyncio, json, os, httpx

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

async def _post_with_retry(client, url, headers, payload, max_attempts=10, max_delay=60.0):
    for attempt in range(max_attempts):
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code not in RETRYABLE_STATUS or attempt == max_attempts - 1:
            r.raise_for_status()
            return r
        retry_after = r.headers.get("retry-after")
        delay = float(retry_after) if retry_after else min(2 ** attempt, max_delay)
        await asyncio.sleep(delay)
    return r

async def run_openai_compatible(agent: dict, scenario: dict, trial, simulator, seed: int):
    endpoint=(agent.get("endpoint") or "").rstrip('/')
    if not endpoint: raise RuntimeError("openai_compatible agent requires endpoint")
    url=endpoint if endpoint.endswith('/chat/completions') else endpoint+'/chat/completions'
    key=os.getenv(agent.get("api_key_env") or "") if agent.get("api_key_env") else None
    headers={"Content-Type":"application/json"}
    if key: headers["Authorization"]=f"Bearer {key}"
    messages=[]
    if agent.get("system_prompt"): messages.append({"role":"system","content":agent["system_prompt"]})
    instruction=scenario.get("instruction","")
    variables=scenario.get("variables")
    if variables: instruction=f"{instruction}\n\nContext: {json.dumps(variables, default=str)}"
    messages.append({"role":"user","content":instruction})
    tools=[]
    tool_map={}
    for t in agent.get("tools",[]):
        tool_map[t["name"]]=t
        tools.append({"type":"function","function":{"name":t["name"],"description":t.get("description","") or "","parameters":t.get("input_schema") or {"type":"object","properties":{}}}})
    max_steps=int(agent.get("behavior",{}).get("max_steps",32))
    async with httpx.AsyncClient(timeout=float(scenario.get("timeout_seconds",120))) as client:
        for step in range(max_steps):
            payload={"model":agent.get("model"),"messages":messages,"tools":tools,"tool_choice":"auto","temperature":0,"seed":seed}
            r=await _post_with_retry(client,url,headers,payload); body=r.json()
            msg=body["choices"][0]["message"]
            calls=msg.get("tool_calls") or []
            if not calls: return msg.get("content") or ""
            messages.append({k:v for k,v in msg.items() if k in {"role","content","tool_calls"}})
            for call in calls:
                fn=call.get("function",{}); name=fn.get("name")
                try: args=json.loads(fn.get("arguments") or "{}")
                except Exception: args={}
                if name not in tool_map:
                    result={"error":"unknown_tool","tool":name}
                else:
                    import random
                    result=await simulator.tool_call(trial,tool_map[name],args,scenario.get("faults",[]),random.Random(seed+step))
                messages.append({"role":"tool","tool_call_id":call.get("id"),"content":json.dumps(result,default=str)})
    return "agent step limit reached"
