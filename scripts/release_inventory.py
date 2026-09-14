"""Record the tested dependency profile and generate the server OpenAPI schema.

This is an observed inventory, not a vulnerability scan, signed SBOM or universal
lockfile. Run in the release test environment after installing runtime extras.
"""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import sys
import tempfile
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from ordeal_platform.config import Settings
from ordeal_platform.server import create_app

ROOT=Path(__file__).resolve().parents[1]
out=ROOT/'release-evidence';out.mkdir(exist_ok=True)
# Core plus the actual server/OTLP/S3 profile installed in this environment.
root_meta=metadata.distribution('ordeal-agent')
selected={'server','otel','s3'}
extra_sets={'ordeal-agent': selected}
queue=['ordeal-agent'];processed={};components={};edges={};missing=[]
while queue:
    name=canonicalize_name(queue.pop(0));extras=extra_sets.get(name,set())
    if processed.get(name)==extras:continue
    processed[name]=set(extras)
    try:dist=metadata.distribution(name)
    except metadata.PackageNotFoundError:
        missing.append(name);continue
    components[name]={'name':dist.metadata['Name'],'version':dist.version,'selected_extras':sorted(extras)}
    deps=[]
    for raw in dist.requires or []:
        req=Requirement(raw)
        if req.marker and not any(req.marker.evaluate({'extra':extra}) for extra in {'',*extras}):continue
        child=canonicalize_name(req.name);deps.append(child)
        extra_sets.setdefault(child,set()).update(req.extras)
        if processed.get(child)!=extra_sets[child]:queue.append(child)
    edges[name]=sorted(set(deps))
record={'schema':'ordeal.tested-dependencies/v1','generated_at':datetime.now(timezone.utc).isoformat(),
        'python':sys.version.split()[0],'platform':sys.platform,'selected_profile':sorted(selected),
        'components':[components[k] for k in sorted(components)],'dependencies':edges,'missing':sorted(set(missing)),
        'limitations':['Observed Python 3.13 environment only; not a resolver lock or cryptographic dependency lock.',
                      'No PostgreSQL client or native agent framework extras were qualified.',
                      'Installed S3 client does not imply live S3 service validation.',
                      'No CVE/SAST/license-compatibility assessment is implied.']}
(out/'dependency-inventory.json').write_text(json.dumps(record,indent=2)+'\n')
constraints=ROOT/'constraints';constraints.mkdir(exist_ok=True)
(constraints/'python313-tested.txt').write_text('# Observed tested profile, not a cross-platform lockfile.\n# Install extras explicitly; this file constrains versions only.\n'+''.join(f'{name}=={item["version"]}\n' for name,item in sorted(components.items()) if name!='ordeal-agent'))
with tempfile.TemporaryDirectory() as tmp:
    settings=Settings(data_dir=Path(tmp),database_url=f'sqlite:///{tmp}/platform.db',
                      master_key=base64.urlsafe_b64encode(os.urandom(32)).decode())
    app=create_app(settings)
    schema=app.openapi()
    (out/'openapi.json').write_text(json.dumps(schema,indent=2)+'\n')
    app.state.database.engine.dispose() if hasattr(app.state,'database') else None
print(json.dumps({'components':len(components),'missing':missing,'api_paths':len(schema['paths'])}))
