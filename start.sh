#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

UV="$HOME/.local/bin/uv"

# Check yt-dlp
if ! command -v yt-dlp &>/dev/null; then
  echo "yt-dlp not found. Installing..."
  sudo curl -sL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp
  sudo chmod a+rx /usr/local/bin/yt-dlp
fi

# Check ffmpeg
if ! command -v ffmpeg &>/dev/null; then
  echo "ffmpeg not found. Run: sudo apt install ffmpeg"
  exit 1
fi

# Setup venv if not present
if [ ! -d ".venv" ]; then
  echo "Setting up Python virtual environment..."
  "$UV" venv .venv
  "$UV" pip install flask --python .venv/bin/python
fi

echo ""
echo "  YT-DLP Downloader running at http://localhost:5050"
echo "  Downloaded files saved to: $DIR/downloads/"
echo "  Press Ctrl+C to stop"
echo ""

.venv/bin/python app.py
