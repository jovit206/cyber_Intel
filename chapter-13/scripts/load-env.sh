#!/usr/bin/env bash

normalize_chapter_env() {
  local env_file="$1"
  local normalized
  normalized="$(mktemp "${env_file}.XXXXXX")"
  tr -d '\r' < "$env_file" > "$normalized"
  chmod 0600 "$normalized"
  mv -f -- "$normalized" "$env_file"
}
