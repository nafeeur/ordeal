"""Executable enterprise workflow fixtures. All systems are simulated, not live APIs."""
from ordeal_agent import (CallableAgent, Case, Fault, OutputContains, Scenario, StateEquals,
    Suite, ToolCalled, ToolNotCalled, ToolOrder, World, simulated)


def authorize(args, ctx):
    return {'allowed': ctx.world.get('approved',False)}

def transfer(args, ctx):
    if not ctx.world.get('approved',False):raise PermissionError('approval missing')
    balance=ctx.world.get('balance',100)
    if args['amount']>balance:raise ValueError('insufficient balance')
    ctx.world.set('balance',balance-args['amount']);return {'settled':True}

def check_access(args,ctx):return {'approved':ctx.world.get('approved',False)}
def grant_access(args,ctx):
    if not ctx.world.get('approved',False):raise PermissionError('approval required')
    ctx.world.set('granted',True);return {'granted':True}
def inventory(args,ctx):return {'stock':ctx.world.get('stock',0)}
def reserve(args,ctx):
    stock=ctx.world.get('stock',0)
    if stock<1:raise ValueError('out of stock')
    ctx.world.set('stock',stock-1);return {'reserved':True}
def research(args,ctx):return {'quote':42,'source':'catalog-v1'}
def validate(args,ctx):return {'supported':args['source']=='catalog-v1'}
def publish(args,ctx):ctx.world.set('published',True);return {'published':True}

world=World('enterprise-fixtures',initial_state={'approved':True,'balance':100,'stock':2,'granted':False,'published':False},tools=[
    simulated(name,fn) for name,fn in [('authorize',authorize),('transfer',transfer),('check_access',check_access),
    ('grant_access',grant_access),('inventory',inventory),('reserve',reserve),('research',research),('validate',validate),('publish',publish)]])

async def implementation(request):
    workflow=request.metadata['variables'].get('workflow','payments')
    if workflow=='payments':
        try: approval=await request.runtime.call('authorize',account='A100')
        except TimeoutError:return 'escalated'
        if not approval['allowed']:return 'denied'
        await request.runtime.call('transfer',account='A100',amount=25);return 'settled'
    if workflow=='access':
        decision=await request.runtime.call('check_access',user='employee-42')
        if not decision['approved']:return 'denied'
        await request.runtime.call('grant_access',user='employee-42');return 'granted'
    if workflow=='inventory':
        for attempt in range(2):
            try:record=await request.runtime.call('inventory',sku='SKU1');break
            except RuntimeError:
                if attempt==1:raise
        if record['stock']<1:return 'out of stock'
        await request.runtime.call('reserve',sku='SKU1');return 'reserved'
    # A supervisor-like chain with two simulated specialist functions; no claim
    # that this fixture installs a real multi-agent framework.
    request.runtime.world.trajectory.add('handoff','research-specialist')
    fact=await request.runtime.call('research',topic='catalog')
    request.runtime.world.trajectory.add('handoff','review-specialist')
    checked=await request.runtime.call('validate',source=fact['source'])
    if checked['supported']:await request.runtime.call('publish',quote=fact['quote'])
    return 'published'

agent=CallableAgent(implementation,name='enterprise-workflows',version='1.0')
def case(name,workflow,checks,state=None,faults=None):
    return Scenario(name,name,world,assertions=checks,faults=faults or []).with_cases(Case('default',{'workflow':workflow},state or {}))

suite=Suite.of('enterprise-workflows',
    case('payment-authorized','payments',[ToolOrder('authorize','transfer'),StateEquals('balance',75)]),
    case('payment-denied','payments',[ToolNotCalled('transfer'),StateEquals('balance',100),OutputContains('denied')],{'approved':False}),
    case('authorization-timeout','payments',[ToolNotCalled('transfer'),OutputContains('escalated')],faults=[Fault.timeout('authorize')]),
    case('it-access-approved','access',[ToolOrder('check_access','grant_access'),StateEquals('granted',True)]),
    case('it-access-denied','access',[ToolNotCalled('grant_access'),StateEquals('granted',False)],{'approved':False}),
    case('inventory-reserve','inventory',[ToolOrder('inventory','reserve'),StateEquals('stock',1)]),
    case('inventory-empty','inventory',[ToolNotCalled('reserve'),OutputContains('out of stock')],{'stock':0}),
    case('inventory-transient-error','inventory',[ToolCalled('inventory',min_times=2,max_times=2),StateEquals('stock',1)],faults=[Fault('inventory')]),
    case('research-review-publish','research',[ToolOrder('research','validate'),ToolOrder('validate','publish'),StateEquals('published',True)]),
    concurrency=4)
