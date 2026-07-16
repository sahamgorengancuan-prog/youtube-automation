# Multi-stage build: build the wheel in one stage, install only the wheel in
# the runtime stage. Credentials are NEVER baked into layers — supply
# OPENAI_API_KEY / BFL_API_KEY at run time (docker run -e / secrets manager).

FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md /src/
COPY scistudio_v10 /src/scistudio_v10
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /dist

FROM python:3.12-slim AS runtime
# FFmpeg for audio/video compositing; Node/npm for the Remotion renderer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg nodejs npm \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl[api,render] && rm -f /tmp/*.whl

# Non-root runtime user with a writable workspace.
RUN useradd --create-home --uid 10001 studio \
    && mkdir -p /work \
    && chown -R studio:studio /work
USER studio
WORKDIR /work
ENV SCISTUDIO_WORKSPACE=/work

# Smoke/health command: the CLI must import and respond.
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD ["scistudio-v10", "--version"]

ENTRYPOINT ["scistudio-v10"]
CMD ["--help"]
