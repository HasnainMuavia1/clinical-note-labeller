FROM node:22-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# NGINX_ENVSUBST_FILTER keeps envsubst away from nginx's own $variables.
ENV APP_API_KEY=dev-key \
    NGINX_ENVSUBST_FILTER=APP_
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
EXPOSE 80
