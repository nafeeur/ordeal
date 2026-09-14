"""Run against your local server with scoped ORDEAL_API_KEY/ORDEAL_PROJECT_ID."""
import os
from ordeal_agent import PlatformClient

with PlatformClient(os.getenv('ORDEAL_SERVER_URL','http://127.0.0.1:8080'),os.environ['ORDEAL_API_KEY'],os.environ['ORDEAL_PROJECT_ID']) as client:
    with client.trace('Support / authorized refund',fail_open=False) as trace:
        with trace.span('lookup_order',arguments={'order_id':'A100'}) as span:
            span.set_output({'paid':True,'amount':42})
        with trace.span('authorize',arguments={'order_id':'A100'}) as span:
            span.set_output({'allowed':True})
        with trace.span('refund',arguments={'order_id':'A100'}) as span:
            span.set_output({'ok':True})
        # This fixture knows its result. Production applications should leave
        # status unset until a suitable evaluator has scored the execution.
        trace.status='pass'
        trace.set_output('Refund completed')
    print(trace.response['id'])
