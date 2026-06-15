FROM ubuntu/squid:latest
COPY docker/squid/squid.conf /etc/squid/squid.conf
RUN squid -z -f /etc/squid/squid.conf || true
EXPOSE 3128
