import subprocess
import os
import json
import uuid
import shutil
from urllib.parse import quote
from flask import Flask, request, Response, send_from_directory, jsonify, stream_with_context, make_response

app = Flask(__name__, static_folder=".")
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")

LOCAL_BIN = os.path.expanduser("~/.local/bin")
os.environ["PATH"] = LOCAL_BIN + os.pathsep + os.environ.get("PATH", "")
YTDLP = os.path.join(LOCAL_BIN, "yt-dlp")


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/robots.txt")
def robots():
    r = make_response("User-agent: *\nAllow: /\nSitemap: https://yt.sevis.store/sitemap.xml\n", 200)
    r.headers["Content-Type"] = "text/plain"
    return r


@app.route("/sitemap.xml")
def sitemap():
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           '<url><loc>https://yt.sevis.store/</loc><changefreq>monthly</changefreq><priority>1.0</priority></url>'
           '</urlset>')
    r = make_response(xml, 200)
    r.headers["Content-Type"] = "application/xml"
    return r



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
            ext = f.get("ext", "")
            height = f.get("height")
            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")
            note = f.get("format_note", "")
            fid = f.get("format_id", "")

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

            key = (height, ext, vcodec == "none", acodec == "none")
            if key not in seen:
                seen.add(key)
                formats.append({"id": fid, "label": " | ".join(label_parts), "height": height or 0})

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

    # Isolated temp dir per request so we can reliably find the output file
    tmpdir = os.path.join(DOWNLOAD_DIR, str(uuid.uuid4()))
    os.makedirs(tmpdir, exist_ok=True)

    cmd = [YTDLP, "--no-playlist", "-o", os.path.join(tmpdir, "%(title)s.%(ext)s")]

    if audio_only:
        cmd += ["-x", "--audio-format", audio_format]
    else:
        quality_map = {
            "best": "bestvideo+bestaudio/best",
            "1080": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "720":  "bestvideo[height<=720]+bestaudio/best[height<=720]",
            "480":  "bestvideo[height<=480]+bestaudio/best[height<=480]",
            "360":  "bestvideo[height<=360]+bestaudio/best[height<=360]",
        }
        cmd += ["-f", quality_map.get(quality, "bestvideo+bestaudio/best")]

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
            files = [f for f in os.listdir(tmpdir) if os.path.isfile(os.path.join(tmpdir, f))]
            if files:
                filename = files[0]
                dest = os.path.join(DOWNLOAD_DIR, filename)
                shutil.move(os.path.join(tmpdir, filename), dest)
                shutil.rmtree(tmpdir, ignore_errors=True)
                yield f"data: DONE:{filename}\n\n"
            else:
                shutil.rmtree(tmpdir, ignore_errors=True)
                yield "data: ERROR: No output file found\n\n"
        else:
            shutil.rmtree(tmpdir, ignore_errors=True)
            yield "data: ERROR: Download failed\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/stream/<path:filename>")
def stream_file(filename):
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.isfile(filepath):
        return "File not found", 404

    filesize = os.path.getsize(filepath)

    def generate_file():
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        os.remove(filepath)

    encoded_name = quote(filename.encode("utf-8"))
    return Response(
        stream_with_context(generate_file()),
        mimetype="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}",
            "Content-Length": str(filesize),
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=False)
