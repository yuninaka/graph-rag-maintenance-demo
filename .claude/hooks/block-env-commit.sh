#!/bin/bash
input=$(cat)
command=$(echo "$input" | jq -r '.tool_input.command')

# .env.example はダミー値のみのテンプレートファイルでコミット対象のため、判定から除外する
sanitized=$(echo "$command" | sed -E 's/\.env\.example/__ENV_EXAMPLE__/g')

if echo "$sanitized" | grep -qE '(^|[[:space:]])\.env([[:space:].]|$)' || echo "$sanitized" | grep -qE '(^|[[:space:]])git add \.([[:space:]]|$)'; then
  echo "Error: .env を add しようとしています。秘匿情報のコミットを防ぐためブロックしました。" >&2
  exit 2
fi

exit 0
