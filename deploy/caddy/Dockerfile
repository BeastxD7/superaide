# syntax=docker/dockerfile:1
# Caddy (automatic HTTPS) with the rate-limit plugin, for docker-compose.prod.yml.
# Stock Caddy has no rate limiting; this adds github.com/mholt/caddy-ratelimit,
# pinned to a commit so a rebuild gets the same code.
ARG CADDY_VERSION=2.11.7

FROM caddy:${CADDY_VERSION}-builder AS build
ARG RATELIMIT_REF=5625512f24f6f59d6f64fb3aafe5eecff0b286db
# No file capability on the binary: it runs unprivileged (see below).
ENV XCADDY_SETCAP=0
RUN xcaddy build --with github.com/mholt/caddy-ratelimit@${RATELIMIT_REF}

FROM caddy:${CADDY_VERSION}-alpine
COPY --from=build /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
# Run unprivileged. Docker lets containers bind ports below 1024 without root
# (net.ipv4.ip_unprivileged_port_start=0), so no capability is needed either.
RUN addgroup -S -g 10001 caddy && adduser -S -u 10001 -G caddy -H caddy \
    && mkdir -p /data/caddy /config/caddy && chown -R caddy:caddy /data /config
USER caddy
