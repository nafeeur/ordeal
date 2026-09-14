# Deployment and installation

## Supported release boundary

The build was executed on Linux with Python 3.13 and Node 22. Package metadata permits Python 3.10+, and CI contains a version matrix; that matrix has not run from this environment. Private child-process runners require POSIX. Start with a dedicated small-team installation and operator-owned test code. Do not expose the default development server publicly.

## Local source or wheel

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[server,otel]'
ordeal-server init --name 'Example organization'
ordeal-server serve
```

`init` creates the database and a 30-day owner token. It preserves existing data and does not reprint old tokens on subsequent runs. Store the token and `.ordeal/master.key` securely. Do not capture bootstrap output into ordinary CI/shared logs. `ordeal-server issue-token PRINCIPAL_ID --days 1` is an operator recovery operation requiring local database access; its output is a credential.

To install a supplied wheel rather than the source, use `python -m pip install './dist/ordeal_agent-0.3.0-py3-none-any.whl[server,otel]'`. This package has not been published to PyPI/npm as part of this release. Installing by public package name alone may resolve something else.

## Configuration

| Variable | Meaning |
|---|---|
| ORDEAL_DATA_DIR | Local state/artifact directory; default `.ordeal` |
| ORDEAL_DATABASE_URL | SQLAlchemy SQLite or PostgreSQL URL |
| ORDEAL_MASTER_KEY | 32 random bytes encoded URL-safe base64; never lose this |
| ORDEAL_PUBLIC_URL | Exact externally visible origin; controls cookies, redirects and origin checks |
| ORDEAL_PRODUCTION | `1` requires HTTPS and disallows development private-outbound overrides |
| ORDEAL_OUTBOUND_HOSTS | Explicit comma-separated HTTP destinations for judges/IdP/connectors |
| ORDEAL_ALLOW_PRIVATE_OUTBOUND | Development-only escape hatch for local fake-provider tests; never production |
| ORDEAL_OIDC_CONFIG | JSON configuration described in SECURITY.md |
| ORDEAL_MAX_BODY_BYTES | Defaults to 4 MiB per HTTP request |
| ORDEAL_MAX_TRACE_EVENTS | Defaults to 10,000 events/spans |
| ORDEAL_REQUEST_LIMIT | Per-process per-minute request limit, default 300 |
| ORDEAL_LEASE_SECONDS | Job lease, default 90 seconds |
| ORDEAL_S3_BUCKET / ORDEAL_S3_ENDPOINT | Optional S3 artifact adapter; AWS credentials via the provider's standard credential chain |
| ORDEAL_ENCRYPTION_KEY_ID | Metadata label for the active key; changing the label does not rotate old ciphertext |

`ORDEAL_MASTER_KEY_FILE`, `ORDEAL_DATABASE_URL_FILE`, and `ORDEAL_OIDC_CONFIG_FILE` read explicit mounted text files. CLI credentials also support `ORDEAL_API_KEY_FILE`. Setting both a value and its `_FILE` variant is an error. Keep secret mount directories private and files readable only by the necessary service identities. Do not put secrets in resource definitions, Terraform state or source control.

## Local Docker/Compose template

Docker was not available in the build environment: these commands are instructions for validation, not a record that it ran here.

```bash
python scripts/prepare_deployment.py
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml up -d database
docker compose -f deploy/compose.yaml run --rm api ordeal-server init --name 'My organization'
docker compose -f deploy/compose.yaml up -d
```

The template binds only `127.0.0.1:8080`. The database is not published on a host port. It uses PostgreSQL and a shared artifact volume, not a proven HA system. The generated `.secrets` directory is 0700; its files are read-only and readable by the differing container UIDs through explicit secret mounts. Back up credentials through a secure operator process. Docker logs/bootstrap output require restricted access. Review base images and pin their digests before deployment.

## Production prerequisites

Terminate TLS at a reviewed ingress/reverse proxy and configure `ORDEAL_PRODUCTION=1` and the exact HTTPS public URL. Pass the original Host and Origin without accepting spoofed forwarded identity headers. The server intentionally does not trust arbitrary proxy headers as authentication. Encrypt database disks, snapshots and transport; trace metadata is not application-encrypted. Put the DB and artifacts on protected networks/storage. Do not mount a Docker socket into the API.

Run the API, `ordeal-server worker`, and `ordeal-server scheduler` as separate managed processes. The worker runs configured evaluators, not arbitrary uploaded Python. The scheduler performs monitor checks, retention and durable deliveries. A missing scheduler means these functions do not run. The Compose commands use one of each; Kubernetes requires separate evaluator/scheduler workloads derived from the pod template if needed.

`deploy/kubernetes/platform.yaml` is deny-first configuration, not a ready-to-apply production manifest: provide an image digest, database/master-key secret, TLS secret/ingress class, storage class and explicit DNS/DB/ingress/IdP egress policies. Its default network policy blocks communication until configured. Run migrations and bootstrap under operator control before serving. A ReadWriteOnce filesystem and one replica are not multi-node HA; design shared storage/S3 before adding replicas. No cloud deployment was performed here.

## OTLP and private runner

HTTP JSON/protobuf: `POST /v1/traces` with bearer credential and `X-Ordeal-Project`. For HTTPS exporter endpoints use normal CA validation, not insecure flags. Traces-only gRPC is separately started:

```bash
ordeal-server otlp-grpc --address 0.0.0.0:4317 --cert tls/server.crt --key tls/server.key
```

Server TLS is required in production. Application bearer authentication remains required; this is not automatically mutual TLS.

Private runners poll outward using project-scoped runner tokens. `--allow-suite` is repeatable and exact. `trusted-process` has no isolation from the host and is suitable only for trusted operator-owned files. Docker mode requires a digest-pinned image that already contains the SDK and needed dependencies. It uses network-none, a read-only root/workspace, dropped capabilities, CPU/RAM/PID limits and a bounded tmpfs. It cannot call remote LLMs in that default mode. Do not remove these restrictions casually to make a remote provider work; define and validate a customer-owned egress model first.

## Offline transfer

Use `scripts/download_wheelhouse.sh` on a connected build host matching the target OS/Python/architecture. Review and scan the result, transfer it through the approved channel, then install with `--no-index --find-links wheelhouse`. Export reviewed OCI images separately if using containers. The delivered archive does not contain all third-party dependency wheels. The server/local examples need no mandatory cloud service, but remote judges/connectors/providers obviously need connectivity. Disabled external features do not constitute certification of an air-gapped installation.
