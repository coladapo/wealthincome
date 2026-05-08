#!/bin/bash
# Shell-side reader for wealthincome.toml.
# Source this from any shell script: `source "$(dirname "$0")/_config.sh"`
#
# Exports: WI_API_PORT, WI_DASH_PORT, WI_DASH_PREVIEW_PORT,
#          WI_PROJECT, WI_LOG_DIR, WI_DATA_DIR, WI_DB_PATH,
#          WI_API_HEALTH_PATH, WI_API_TIMEOUT, WI_RETRY_COUNT, WI_RETRY_DELAY

WI_PROJECT="${WI_PROJECT:-/Users/wivak/puo-jects/____active/wealthincome-unified}"
WI_CONFIG="$WI_PROJECT/wealthincome.toml"

if [ ! -f "$WI_CONFIG" ]; then
    echo "ERROR: missing config at $WI_CONFIG" >&2
    return 1 2>/dev/null || exit 1
fi

# Tiny TOML reader — handles "key = value" lines under [section] headers.
# Strips inline comments and whitespace. No nested tables or arrays needed.
_wi_read() {
    local section="$1" key="$2"
    awk -v s="[$section]" -v k="$key" '
        /^\[/ { in_s = ($0 == s); next }
        in_s && $0 ~ "^[[:space:]]*"k"[[:space:]]*=" {
            sub(/^[^=]*=[[:space:]]*/, "")
            sub(/[[:space:]]*#.*$/, "")
            gsub(/^["[:space:]]+|["[:space:]]+$/, "")
            print
            exit
        }
    ' "$WI_CONFIG"
}

export WI_API_PORT="$(_wi_read ports api)"
export WI_DASH_PORT="$(_wi_read ports dashboard)"
export WI_DASH_PREVIEW_PORT="$(_wi_read ports dashboard_preview)"
export WI_LOG_DIR="$WI_PROJECT/$(_wi_read paths log_dir)"
export WI_DATA_DIR="$WI_PROJECT/$(_wi_read paths data_dir)"
export WI_DB_PATH="$WI_PROJECT/$(_wi_read paths db)"
export WI_API_HEALTH_PATH="$(_wi_read health api_path)"
export WI_API_TIMEOUT="$(_wi_read health api_timeout_sec)"
export WI_RETRY_COUNT="$(_wi_read health retry_count)"
export WI_RETRY_DELAY="$(_wi_read health retry_delay_sec)"

export WI_API_HEALTH_URL="http://127.0.0.1:${WI_API_PORT}${WI_API_HEALTH_PATH}"
export WI_DASH_URL="http://127.0.0.1:${WI_DASH_PORT}"
export WI_DASH_PREVIEW_URL="http://127.0.0.1:${WI_DASH_PREVIEW_PORT}"
