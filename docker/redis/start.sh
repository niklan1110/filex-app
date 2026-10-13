#!/bin/sh
# Redis (Valkey) entry point (compose `configs`): render users.acl with the password hashes from
# the Docker secret files, then exec valkey-server. Passwords never reach argv or the environment;
# the rendered file holds SHA-256 hashes only and lives on the container's tmpfs.
set -eu
umask 077

hash_of() {
  f="/run/secrets/$1"
  [ -r "$f" ] || { echo "start.sh: missing secret $f (run scripts/make_secrets.py)" >&2; exit 1; }
  n=$(tr -d '\r\n' < "$f" | wc -c)
  [ "$n" -ge 24 ] || { echo "start.sh: $f is shorter than 24 characters" >&2; exit 1; }
  tr -d '\r\n' < "$f" | sha256sum | cut -d' ' -f1
}

api=$(hash_of redis_api_password)
worker=$(hash_of redis_worker_password)
sed -e '/^#/d' -e '/^[[:space:]]*$/d' -e "s/@API_HASH@/$api/" -e "s/@WORKER_HASH@/$worker/" \
  /etc/filex-redis/users.acl > /tmp/users.acl

# No persistence (ADR-0001). Valkey 8 (like Redis 7) keeps protected configs, DEBUG and MODULE
# off by default. The redis-server fallback keeps a not-yet-recreated Redis container starting.
server=$(command -v valkey-server || echo redis-server)
exec "$server" --aclfile /tmp/users.acl --save "" --appendonly no --maxclients 512
