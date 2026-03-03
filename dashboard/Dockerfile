FROM node:20-alpine
WORKDIR /app
COPY dashboard/package.json /app/package.json
RUN npm install
COPY dashboard /app
RUN npm run build
CMD ["npm", "run", "start"]
