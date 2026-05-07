# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# GSF Next.js frontend image
# ---------------------------------------------------------------------------
# Build (from /frontend):  docker build -t gsf-frontend:latest .
# Run:                     docker run --rm -p 3000:3000 \
#                              -e PYTHON_API_URL=http://gsf-backend:3001 \
#                              gsf-frontend:latest
#
# Uses Next.js standalone output (NEXT_OUTPUT=standalone) for a small image.
# The runtime image only contains the standalone server, static assets and
# public files — node_modules from the build stage are intentionally excluded.
# ---------------------------------------------------------------------------

ARG NODE_VERSION=22-bookworm-slim
ARG PNPM_VERSION=10.10.0

############################
# Stage 1: deps
############################
FROM node:${NODE_VERSION} AS deps

ARG PNPM_VERSION
ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    CI=1

RUN npm install -g --silent corepack@latest \
 && corepack enable \
 && corepack prepare pnpm@${PNPM_VERSION} --activate

WORKDIR /app

# Install only what's needed to resolve the dependency graph deterministically.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
COPY prisma ./prisma

RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm install --frozen-lockfile

############################
# Stage 2: builder
############################
FROM node:${NODE_VERSION} AS builder

# Backend URL baked into the Next.js rewrite manifest at build time
# (Next.js evaluates rewrites() during `next build`, not at server startup).
# Defaults to the in-cluster Service name used by the helm chart.
ARG PYTHON_API_URL=http://gsf-backend:3001

ARG PNPM_VERSION
ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    CI=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_OUTPUT=standalone \
    PYTHON_API_URL=${PYTHON_API_URL}

RUN npm install -g --silent corepack@latest \
 && corepack enable \
 && corepack prepare pnpm@${PNPM_VERSION} --activate

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN pnpm build

############################
# Stage 3: runner
############################
FROM node:${NODE_VERSION} AS runner

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN apt-get update \
 && apt-get install -y --no-install-recommends tini ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 1001 nodejs \
 && useradd  --system --uid 1001 --gid nodejs --home /app --shell /usr/sbin/nologin nextjs

WORKDIR /app

# Standalone output places the project under <traceRoot-relative path>/.
# In this build the project is copied to /app, and outputFileTracingRoot
# is the parent dir, so the bundle structure ends up rooted at /app/app/.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone/ ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./app/.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./app/public

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "require('http').get('http://127.0.0.1:'+(process.env.PORT||3000)+'/',(r)=>process.exit(r.statusCode<500?0:1)).on('error',()=>process.exit(1))"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["node", "app/server.js"]
