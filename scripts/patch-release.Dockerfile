# syntax=docker/dockerfile:1
# A release built on top of the previous published image, for when the full build can't be
# uploaded (Docker Hub kept dropping the ~165 MB packages layer from umbrel, and GitHub Actions
# was off). Use it ONLY when the Python packages are unchanged since BASE: the Dockerfile,
# backend/uv.lock and backend/pyproject.toml may differ from BASE's commit in the version line only.
# The base layers (system, tzdata, Python, packages) are already in the registry, so the push
# uploads only the app code and the dashboard (a few MB).
#
#   docker buildx build -f scripts/patch-release.Dockerfile --platform linux/amd64,linux/arm64 \
#     --build-arg BASE=kshanxs/gp-sync:<previous>@sha256:<digest> \
#     --build-arg BUILD_COMMIT=<sha> --build-arg BUILD_DATE=<iso date> \
#     -t kshanxs/gp-sync:<version> -t kshanxs/gp-sync:latest --push .

ARG BASE

# The dashboard, exactly as in the Dockerfile.
FROM --platform=$BUILDPLATFORM node:24.22.2-trixie-slim@sha256:54f84b4cf8432b21aaffd5d3ce8d41e12cea0d10d33c09d2f3c9879d49d6b446 AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npx vp build

FROM ${BASE}
WORKDIR /app
# The previous dashboard's files would otherwise stay next to the new ones.
RUN rm -rf /app/static
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
COPY backend/sync.py backend/media.py backend/notify.py backend/cache.py backend/provenance.py backend/snapchat.py backend/pace.py backend/cli.py VERSION ./
COPY --from=web /web/dist ./static

ARG BUILD_COMMIT=""
ARG BUILD_DATE=""
ENV BUILD_COMMIT=$BUILD_COMMIT \
    BUILD_DATE=$BUILD_DATE
