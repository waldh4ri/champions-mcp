# Builds @smogon/calc by cloning smogon/damage-calc from GitHub at build time.
# Build context: the champions-mcp/ directory (no sibling damage-calc/ needed).
#
# Built automatically by `docker compose build` / `docker compose up --build`.
#
# Standalone:
#   docker build -f docker/calc/Dockerfile -t champions-calc:latest .
FROM node:20-alpine AS build
WORKDIR /repo
# Clone the latest damage-calc; calc/tsconfig.json extends ../tsconfig.json
# so we need the repo root present (--depth=1 keeps the layer small).
RUN apk add --no-cache git \
 && git clone --depth=1 https://github.com/smogon/damage-calc .
WORKDIR /repo/calc
# --ignore-scripts: skip the browser bundler; we only need `tsc`.
RUN npm ci --ignore-scripts && npx tsc -p .

FROM node:20-alpine
WORKDIR /app
# Only the compiled library is needed at runtime (data is self-contained).
COPY --from=build /repo/calc/dist /app/calc/dist
COPY docker/calc/bridge.js /app/bridge.js
COPY docker/calc/server.js /app/server.js
# Default: one-shot stdin→stdout bridge (standalone use).
# Override CMD to /app/server.js for the docker-compose HTTP sidecar.
ENTRYPOINT ["node"]
CMD ["/app/bridge.js"]
