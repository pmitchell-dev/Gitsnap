"""
Module for uploading captured images and videos to GitHub content API.
"""
import base64
from datetime import datetime
import io
import uuid

import requests
from config import load_config, get_location, DEBUG_LOG_FILE

def generate_default_filename(file_path=None, word=None):
    ext = "mp4" if file_path else "png"
    prefix = "video" if file_path else "screenshot"
    now_str = datetime.now().strftime('%Y-%m-%d_%I-%M-%S_%p')
    filename_base = f"{prefix}_{now_str}_{uuid.uuid4().hex[:6]}"
    if word:
        return f"{filename_base}_{word}.{ext}"
    return f"{filename_base}.{ext}"

def upload_image(img, word=None, location_name=None, file_path=None, custom_filename=None):
    """
    Uploads an image or video file to the specified GitHub repository.
    """
    config = load_config()
    if not config:
        return (None, "Could not load configuration.")

    loc = get_location(config, location_name)

    token = loc.get("token", "")
    repo = loc.get("repo", "")
    branch = loc.get("branch", "main")
    folder = loc.get("folder", "screenshots").strip("/")

    if not token or token == "YOUR_PERSONAL_ACCESS_TOKEN_HERE":
        return (None, "GitHub token is missing or not configured.")
    if not repo or repo == "username/reponame":
        return (None, "GitHub repository is not configured.")

    try:
        if file_path:
            with open(file_path, "rb") as f:
                img_bytes = f.read()
        else:
            # Convert PIL image to bytes
            img_byte_arr = io.BytesIO()
            img.save(img_byte_arr, format='PNG')
            img_bytes = img_byte_arr.getvalue()

        # Base64 encode for GitHub API
        b64_content = base64.b64encode(img_bytes).decode('utf-8')

        # Determine filename
        if custom_filename:
            filename = custom_filename
        else:
            filename = generate_default_filename(file_path, word)

        path = f"{folder}/{filename}" if folder else filename

        url = f"https://api.github.com/repos/{repo}/contents/{path}"

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json"
        }

        data = {
            "message": f"Upload {filename}",
            "content": b64_content,
            "branch": branch
        }

        response = requests.put(url, headers=headers, json=data, timeout=30)

        if response.status_code >= 400:
            with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"Upload failed - Status: {response.status_code}, "
                        f"Response: {response.text}\n")

            if response.status_code == 404:
                return (None, f"Not Found - check branch '{branch}' or repository name.")
            elif response.status_code == 401:
                return (None, "Unauthorized - check your GitHub Token.")
            return (None, f"Error: {response.status_code}")

        # Return raw URL
        raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
        return (raw_url, None)

    except (requests.RequestException, IOError, OSError) as e:
        with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"Upload Exception: {e}\n")
        return (None, str(e))
