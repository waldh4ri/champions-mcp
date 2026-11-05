# champions-sim: Pokémon Showdown's battle simulator (Champions mod) as a
# battle-simulation sidecar for champions-mcp's simulate_* tools.
#
# Showdown is cloned at a pinned commit: its `champions` mod carries the real
# Champions mechanics (Stat Points, Megas/Z-Megas, 20 PP cap, no Tera, the new
# abilities) and the Reg M-C VGC/BSS formats + validator. The npm package lags
# (0.11.11 stops at Reg M-B), so build from source. Bump SHOWDOWN_SHA
# deliberately; the self-test below gates every build.
#
#   docker build -f docker/sim/Dockerfile -t champions-sim:latest .
FROM node:22-alpine AS build
ARG SHOWDOWN_SHA=16db150abae0829180b8721da3bff0e51896a223
RUN apk add --no-cache git \
 && git init -q /ps && cd /ps \
 && git remote add origin https://github.com/smogon/pokemon-showdown.git \
 && git fetch -q --depth=1 origin "$SHOWDOWN_SHA" && git checkout -q FETCH_HEAD
WORKDIR /ps
RUN npm ci --no-audit --no-fund --ignore-scripts && node build \
 && rm -rf node_modules/.cache .git

FROM node:22-alpine
ARG SHOWDOWN_SHA=16db150abae0829180b8721da3bff0e51896a223
ENV SHOWDOWN_SHA=$SHOWDOWN_SHA PS_SIM=/ps/dist/sim NODE_ENV=production
# The compiled sim requires a few runtime deps from Showdown's node_modules.
COPY --from=build /ps/dist /ps/dist
COPY --from=build /ps/node_modules /ps/node_modules
COPY --from=build /ps/package.json /ps/package.json
COPY --from=build /ps/config /ps/config
WORKDIR /app
COPY docker/sim/*.js docker/sim/selftest-teams.json /app/
# Gate the image on the self-test (legality, robustness, determinism,
# policy ladder, tactical scenarios).
RUN node /app/selftest.js --quick
EXPOSE 3000
ENTRYPOINT ["node"]
CMD ["/app/server.js"]
