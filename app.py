import subprocess
import os
import json
from flask import Flask, request, Response, send_from_directory, jsonify, stream_with_context

app = Flask(__name__, static_folder=".")
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")

LOCAL_BIN = os.path.expanduser("~/.local/bin")
os.environ["PATH"] = LOCAL_BIN + os.pathsep + os.environ.get("PATH", "")
YTDLP = os.path.join(LOCAL_BIN, "yt-dlp")


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/downloads/<path:filename>")
def serve_download(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)


@app.route("/list-downloads")
def list_downloads():
    files = []
    for f in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, f)
        if os.path.isfile(path):
            files.append({"name": f, "size": os.path.getsize(path)})
    files.sort(key=lambda x: x["name"])
    return jsonify(files)


@app.route("/formats", methods=["POST"])
def get_formats():
    data = request.get_json()
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    try:
        result = subprocess.run(
            [YTDLP, "--dump-json", "--no-playlist", url],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            return jsonify({"error": result.stderr or "Failed to fetch video info"}), 400

        info = json.loads(result.stdout)
        formats = []
        seen = set()
        for f in info.get("formats", []):
            fid = f.get("format_id", "")
            ext = f.get("ext", "")
            height = f.get("height")
            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")
            note = f.get("format_note", "")

            label_parts = []
            if height:
                label_parts.append(f"{height}p")
            if note:
                label_parts.append(note)
            label_parts.append(ext)
            if vcodec == "none":
                label_parts.append("audio only")
            elif acodec == "none":
                label_parts.append("video only")

            label = " | ".join(label_parts)
            key = (height, ext, vcodec == "none", acodec == "none")
            if key not in seen:
                seen.add(key)
                formats.append({"id": fid, "label": label, "height": height or 0})

        formats.sort(key=lambda x: x["height"], reverse=True)
        return jsonify({
            "title": info.get("title", "Unknown"),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration_string", ""),
            "formats": formats
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching video info"}), 408
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/download", methods=["POST"])
def download():
    data = request.get_json()
    url = data.get("url", "").strip()
    audio_only = data.get("audio_only", False)
    audio_format = data.get("audio_format", "mp3")
    quality = data.get("quality", "best")
    subtitles = data.get("subtitles", False)
    sub_lang = data.get("sub_lang", "en")

    if not url:
        return Response("data: ERROR: No URL provided\n\n", mimetype="text/event-stream")

    cmd = [YTDLP, "--no-playlist", "-o", os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s")]

    if audio_only:
        cmd += ["-x", "--audio-format", audio_format]
    else:
        if quality == "best":
            cmd += ["-f", "bestvideo+bestaudio/best"]
        elif quality == "1080":
            cmd += ["-f", "bestvideo[height<=1080]+bestaudio/best[height<=1080]"]
        elif quality == "720":
            cmd += ["-f", "bestvideo[height<=720]+bestaudio/best[height<=720]"]
        elif quality == "480":
            cmd += ["-f", "bestvideo[height<=480]+bestaudio/best[height<=480]"]
        elif quality == "360":
            cmd += ["-f", "bestvideo[height<=360]+bestaudio/best[height<=360]"]

    if subtitles:
        cmd += ["--write-subs", "--sub-lang", sub_lang, "--embed-subs"]

    cmd += ["--merge-output-format", "mp4", "--progress", url]

    def generate():
        yield "data: Starting download...\n\n"
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        for line in proc.stdout:
            line = line.strip()
            if line:
                yield f"data: {line}\n\n"
        proc.wait()
        if proc.returncode == 0:
            yield "data: DONE\n\n"
        else:
            yield "data: ERROR: Download failed\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)
