"""Turn an exported, version-pinned dataset into local replay regression tests."""
from __future__ import annotations
from collections.abc import Sequence, Mapping
from .recording import Cassette
from .scenario import Scenario, Suite


def replay_suite(name: str, data: Mapping, *, assertions: Sequence, concurrency: int = 1,
                 strict_order: bool = True) -> Suite:
    """Requires explicit assertions; fixtures are observations, not an oracle.

    Cassettes reproduce tool returns, not a full external system. They do not
    reproduce arbitrary remote state, provider randomness, or uncaptured actions.
    No Python from the dataset is ever executed.
    """
    if not assertions:
        raise ValueError('Supply behavioral assertions independently of recorded outcomes')
    cases=data.get('cases')
    if not isinstance(cases,list) or not cases:
        raise ValueError('Dataset must have nonempty cases')
    scenarios=[];seen=set()
    for case in cases:
        if not isinstance(case,dict) or not isinstance(case.get('id'),str) or case['id'] in seen:
            raise ValueError('Each case requires a unique string id')
        seen.add(case['id'])
        if case.get('replay_ready') is not True or case.get('capture_gaps'):
            raise ValueError(f"Case {case['id']} has incomplete/redacted capture; repair it explicitly before replay")
        cassette=Cassette.from_dict(case.get('cassette',{}))
        scenarios.append(Scenario(name=f"{name}/{case['id']}", instruction=str(case.get('instruction','')),
            world=cassette.to_world(strict_order=strict_order),assertions=list(assertions)))
    return Suite.of(name,*scenarios,concurrency=concurrency)
