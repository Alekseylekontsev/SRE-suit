#!/bin/sh
# Entrypoint: if a config file is mounted at SRE_CONFIG_PATH and the caller did
# not pass --config/-c, inject it so `docker run <img> health` just works.
set -eu

CONFIG="${SRE_CONFIG_PATH:-/config/config.yaml}"

if [ -f "$CONFIG" ]; then
    case " $* " in
        *" --config "* | *" -c "*) : ;;                 # caller already set it
        *) set -- --config "$CONFIG" "$@" ;;
    esac
fi

exec sre-agent "$@"
