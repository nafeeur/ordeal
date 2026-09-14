"""Run deterministic release checks without auto-loading unrelated pytest plugins."""
from pathlib import Path
import os,subprocess,sys
root=Path(__file__).resolve().parents[1]
(root/'release-evidence').mkdir(exist_ok=True)
env={**os.environ,'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','PYTHONPATH':str(root/'src')}
local_tsc=root/'sdks/typescript/node_modules/typescript/bin/tsc'
compiler=['node',str(local_tsc)] if local_tsc.exists() else ['tsc']
commands=[
 [sys.executable,'-m','compileall','-q','src'],
 [sys.executable,'-m','pytest','-p','pytest_asyncio.plugin','-q','-o','faulthandler_timeout=30','--ignore=tests/platform/test_browser.py','--junitxml=release-evidence/python-junit.xml'],
 [sys.executable,'-m','pytest','-p','pytest_asyncio.plugin','-q','-o','faulthandler_timeout=30','tests/platform/test_browser.py','--junitxml=release-evidence/browser-junit.xml'],
 [*compiler,'-p','sdks/typescript/tsconfig.json'],
 ['node','--test',*map(str,sorted((root/'sdks/typescript/test').glob('*.test.mjs')))],
 [sys.executable,'-m','ordeal_agent.cli','run','examples/enterprise/suite.py','--min-pass-rate','1.0','--json','release-evidence/enterprise-report.json','--junit','release-evidence/enterprise-junit.xml'],
]
for command in commands:
    print('RUN', ' '.join(command),flush=True)
    subprocess.run(command,cwd=root,env=env,check=True,timeout=180)
print('All requested checks exited successfully. See docs/TESTING.md for qualification limits.')
