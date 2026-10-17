FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1 \
    API_URL=http://api:8080
RUN npm run build
EXPOSE 3000
CMD ["npm", "start"]
