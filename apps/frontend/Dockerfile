# Stage 1: Build
FROM node:20-alpine AS build

WORKDIR /app

# Enable pnpm via corepack
RUN corepack enable && corepack prepare pnpm@9.15.0 --activate

# Copy workspace root config
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml turbo.json ./

# Copy package.json files for workspace packages the frontend depends on
COPY packages/shared-types/package.json packages/shared-types/
COPY packages/sdk/package.json packages/sdk/
COPY apps/frontend/package.json apps/frontend/

# Install all dependencies
RUN pnpm install --frozen-lockfile

# Copy source for workspace packages and frontend
COPY packages/shared-types/ packages/shared-types/
COPY packages/sdk/ packages/sdk/
COPY apps/frontend/ apps/frontend/

# Build (turbo handles dependency ordering: shared-types -> sdk -> frontend)
RUN pnpm turbo run build --filter=@copilot/frontend

# Stage 2: Serve with Nginx
FROM nginx:alpine

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy built assets from build stage
COPY --from=build /app/apps/frontend/dist /usr/share/nginx/html

# Copy nginx config
COPY apps/frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
