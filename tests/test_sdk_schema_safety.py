from __future__ import annotations
import pytest
from jsonschema import ValidationError
from ordeal_agent import Tool,World,Fault,function_tool,CallableAgent,Scenario
from ordeal_agent.world import RealToolBlockedError


def test_future_annotations_produce_real_argument_schemas():
    def lookup(customer_id: str, retries: int = 3, active: bool = True):return customer_id
    tool=function_tool(lookup)
    assert tool.input_schema['properties']['customer_id']=={'type':'string'}
    assert tool.input_schema['properties']['retries']=={'type':'integer'}
    tool.validate_input({'customer_id':'a'})
    with pytest.raises(ValidationError):tool.validate_input({'customer_id':1})
    with pytest.raises(ValidationError):tool.validate_input({'customer_id':'a','retries':True})


@pytest.mark.asyncio
async def test_string_passthrough_mode_does_not_bypass_safety():
    tool=Tool('real',mode='passthrough',handler=lambda a,c:'real')
    with pytest.raises(RealToolBlockedError):await World('w',tools=[tool]).spawn('s').call_tool('real',{})


def test_duplicate_tools_and_bad_faults_rejected():
    with pytest.raises(ValueError,match='unique'):World('w',tools=[Tool('x'),Tool('x')])
    for kwargs in [{'on_call':0},{'on_call':True},{'kind':'bogus'},{'delay_ms':float('nan')},{'delay_ms':-1}]:
        with pytest.raises(ValueError):Fault('x',**kwargs)


def test_variadic_function_requires_explicit_schema():
    def fn(*args):return args
    with pytest.raises(ValueError,match='named parameters'):function_tool(fn)
