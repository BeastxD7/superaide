# syntax=docker/dockerfile:1
# Superaide frontend: the built React app served by nginx on port 8080.
#   docker build -t superaide-web frontend/
# The app calls the API at <same host>:8000 unless VITE_API_URL is set at build time:
#   docker build --build-arg VITE_API_URL=https://api.example.com -t superaide-web frontend/

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY . .
ARG VITE_API_URL=
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD ["wget", "-qO-", "http://127.0.0.1:8080/healthz"]
