#!/bin/sh
set -eu
mkdir -p /data/sessions /data/uploads
exec python -m app.main
