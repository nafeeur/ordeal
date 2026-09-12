# Contributing to Ordeal

Thanks for helping improve Ordeal. The project favors small, testable changes and deterministic behavior over hidden magic.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend && PYTHONPATH=. pytest -q
```

For terminal interface work:

```bash
make api
# in another terminal
make tui
```

## Pull requests

- Add or update tests for backend behavior.
- Keep canonical world mutations deterministic.
- Treat model-based generation/judgment as optional evidence, not source-of-truth state.
- Never commit API keys, traces containing customer secrets, runtime databases, or generated build output.
- Explain user-visible behavior changes in the PR description.

## Good first contributions

Repository sandboxes, adapters, deterministic constraints, scenario generators, trace importers, TUI accessibility, documentation, and reproducible examples are all welcome.
