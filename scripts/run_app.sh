#!/usr/bin/env bash
# 바로 가기 아이콘에서 실행: 번역 서버(Ollama)가 꺼져 있으면 켠 뒤 앱을 실행한다.
OLLAMA="$HOME/.local/ollama/bin/ollama"
if ! curl -s -o /dev/null http://127.0.0.1:11434/api/version; then
    nohup "$OLLAMA" serve > "$HOME/.local/ollama/serve.log" 2>&1 &
fi
cd "$(dirname "$0")/.." || exit 1
exec "$HOME/anaconda3/envs/diary_en/bin/python" -m diary_english
