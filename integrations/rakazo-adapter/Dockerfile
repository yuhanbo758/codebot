FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json tsconfig.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY src ./src
RUN npm run build

FROM node:22-alpine
ENV NODE_ENV=production
WORKDIR /app
USER node
COPY --from=build --chown=node:node /app/dist ./dist
EXPOSE 8787
CMD ["node", "dist/index.js"]
