FROM node:24-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
COPY packages/contracts/package.json ./packages/contracts/package.json
RUN npm ci

COPY apps/web ./apps/web
COPY packages/contracts ./packages/contracts
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_WORKSPACE_ID
ARG NEXT_PUBLIC_USER_ID
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_WORKSPACE_ID=$NEXT_PUBLIC_WORKSPACE_ID
ENV NEXT_PUBLIC_USER_ID=$NEXT_PUBLIC_USER_ID
RUN npm run build --workspace @code-genome/web

FROM node:24-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/apps/web/.next/standalone ./
COPY --from=build /app/apps/web/.next/static ./apps/web/.next/static
EXPOSE 3000
CMD ["node", "apps/web/server.js"]

