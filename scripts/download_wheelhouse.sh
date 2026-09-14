#!/bin/sh
set -eu
# Run on an internet-connected build host matching the target OS/architecture/Python.
# This script was not run here; the supplied release does not include third-party wheels.
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$ROOT/wheelhouse"
python -m pip wheel --wheel-dir "$ROOT/wheelhouse" "$ROOT[server,otel,postgres,s3]"
printf '%s\n' 'Copy the reviewed wheelhouse, source, master-key backup, and image exports through your approved transfer process.'
