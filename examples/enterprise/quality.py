"""An example allowlisted custom evaluator for an opted-in private runner."""
def evaluate(trace, options):
    output=trace.get('trajectory',{}).get('final_output')
    if output is None:return {'verdict':'incomplete','message':'No output captured'}
    ok=len(str(output).strip()) >= int(options.get('minimum_length',1))
    return {'verdict':'pass' if ok else 'fail','score':float(ok),'message':'Nonempty response check; not factual accuracy'}
