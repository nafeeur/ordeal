# Deployment template; the Docker runtime was not available for this release test.
# Override PYTHON_IMAGE with your reviewed digest before a production build.
ARG PYTHON_IMAGE=python:3.13-slim
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /opt/ordeal
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
COPY examples ./examples
RUN python -m pip install --no-cache-dir '.[server,otel,postgres,s3]'     && groupadd --gid 65532 ordeal     && useradd --uid 65532 --gid 65532 --no-log-init --create-home ordeal     && mkdir -p /var/lib/ordeal     && chown 65532:65532 /var/lib/ordeal
ENV ORDEAL_DATA_DIR=/var/lib/ordeal
USER 65532:65532
EXPOSE 8080
# CMD, not ENTRYPOINT: the private runner can override this with python -m ...
CMD ["ordeal-server","serve","--host","0.0.0.0","--port","8080"]
