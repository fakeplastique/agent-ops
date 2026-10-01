FROM node:22-slim
RUN corepack enable
WORKDIR /srv
# next.config.ts bakes the rewrite in at build time, so this is a build arg, not runtime env.
ARG API_ORIGIN
ENV API_ORIGIN=$API_ORIGIN
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY next.config.ts tsconfig.json ./
COPY src ./src
RUN pnpm build && chown -R node:node /srv
USER node
CMD ["sh", "-c", "pnpm start -p ${PORT:-3000}"]
