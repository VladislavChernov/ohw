#!/bin/sh
# Tiny internal HTTP endpoint for restoring the SQLite volume from the UI.
# The service is not published to the host; the UI proxies POST /api/db/restore.
set -eu

WEBROOT=/tmp/db-admin
mkdir -p "$WEBROOT"

cat >"$WEBROOT/handler.sh" <<'EOF'
#!/bin/sh
set -u

respond() {
  code=$1
  reason=$2
  body=$3
  length=$(printf '%s' "$body" | wc -c)
  printf 'HTTP/1.1 %s %s\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: %s\r\nConnection: close\r\n\r\n%s' \
    "$code" "$reason" "$length" "$body"
}

IFS= read -r request || exit 0
request=$(printf '%s' "$request" | tr -d '\r')
set -- $request
method=${1:-}
path=${2:-}

authorized=0
while IFS= read -r line; do
  line=$(printf '%s' "$line" | tr -d '\r')
  [ -z "$line" ] && break
  lower=$(printf '%s' "$line" | tr '[:upper:]' '[:lower:]')
  case "$lower" in
    x-db-restore:*)
      value=${line#*:}
      value=$(printf '%s' "$value" | tr -d ' ')
      [ "$value" = "ui" ] && authorized=1
      ;;
  esac
done

if [ "$path" = "/healthz" ]; then
  if [ "$method" = "GET" ]; then
    respond 200 OK "ok
"
  else
    respond 405 "Method Not Allowed" "method not allowed
"
  fi
  exit 0
fi

if [ "$path" != "/restore" ]; then
  respond 404 "Not Found" "not found
"
  exit 0
fi

if [ "$method" != "POST" ]; then
  respond 405 "Method Not Allowed" "method not allowed
"
  exit 0
fi

if [ "$authorized" -ne 1 ]; then
  respond 403 Forbidden "forbidden
"
  exit 0
fi

if output=$(sh /db/restore.sh 2>&1); then
  respond 200 OK "$output
"
else
  respond 500 "Internal Server Error" "restore failed:
$output
"
fi
EOF

chmod 755 "$WEBROOT/handler.sh"
exec nc -lk -p 8091 -e "$WEBROOT/handler.sh"
