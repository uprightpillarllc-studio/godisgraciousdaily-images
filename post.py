"""Publish today's God Is Gracious Daily post to Instagram.

Each day's post lives in its own folder named with the date (Pacific time):
    posts/2026-10-21/card.png         <- one image = a single post
    posts/2026-10-21/caption.txt      <- the caption (optional)

For a carousel, put 2-10 images in the folder. They post in name order:
    posts/2026-10-25/slide1.png, slide2.png, slide3.png ...

PNGs are fine: "python post.py prepare" converts them to JPG (Instagram's API
only accepts JPEG). After posting, posted.txt is written in the folder so the
same day never posts twice.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

API = "https://graph.instagram.com"
TZ = ZoneInfo("America/Los_Angeles")
POST_HOUR = 6  # 6 AM Pacific


def today_folder():
    now = datetime.now(TZ)
    return now, Path("posts") / now.strftime("%Y-%m-%d")


def should_run(now, folder):
    force = os.environ.get("FORCE", "false").lower() == "true"
    if not force and now.hour < POST_HOUR:
        print(f"It's {now:%H:%M} Pacific - too early. The {POST_HOUR}:00 AM run will post.")
        return False
    if (folder / "posted.txt").exists():
        print(f"{folder.name} has already been posted. Nothing to do.")
        return False
    if not folder.is_dir():
        print(f"No post scheduled for {folder.name} (there is no posts/{folder.name} folder).")
        return False
    return True


def set_output(name, value):
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"{name}={value}\n")


# ---------- step 1: convert PNGs to JPG ----------

def prepare():
    now, folder = today_folder()
    if not should_run(now, folder):
        return
    from PIL import Image

    converted = 0
    for png in sorted(folder.glob("*.png")) + sorted(folder.glob("*.PNG")):
        jpg = png.with_suffix(".jpg")
        img = Image.open(png)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
        else:
            img = img.convert("RGB")
        img.save(jpg, "JPEG", quality=95)
        png.unlink()
        converted += 1
        print(f"Converted {png.name} -> {jpg.name}")
    if converted:
        set_output("converted", "true")


# ---------- step 2: publish ----------

def call(method, path, params):
    query = urllib.parse.urlencode(params)
    if method == "GET":
        req = urllib.request.Request(f"{API}/{path}?{query}")
    else:
        req = urllib.request.Request(f"{API}/{path}", data=query.encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        sys.exit(f"Instagram API error ({e.code}) on '{path.split('/')[-1]}': {body}")


def wait_until_public(url):
    for _ in range(12):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30):
                return
        except urllib.error.URLError:
            time.sleep(5)
    sys.exit(f"The image isn't reachable yet at {url}. Is the repository public?")


def wait_until_ready(container_id, token):
    for _ in range(30):
        status = call("GET", container_id, {"fields": "status_code", "access_token": token}).get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            sys.exit(f"Instagram could not process the image (status: {status}).")
        time.sleep(5)
    sys.exit("Timed out waiting for Instagram to process the image.")


def publish():
    token = os.environ.get("IG_ACCESS_TOKEN")
    if not token:
        sys.exit("The IG_ACCESS_TOKEN secret is missing. Add it under Settings > Secrets and variables > Actions.")

    now, folder = today_folder()
    if not should_run(now, folder):
        return

    images = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".jpg", ".jpeg"))
    if not images:
        sys.exit(f"posts/{folder.name} has no images. Add a .png or .jpg card.")
    if len(images) > 10:
        sys.exit(f"posts/{folder.name} has {len(images)} images; a carousel can have at most 10.")

    caption_file = folder / "caption.txt"
    caption = caption_file.read_text(encoding="utf-8").strip() if caption_file.exists() else ""
    if len(caption) > 2200:
        sys.exit(f"The caption is {len(caption)} characters; Instagram's limit is 2,200.")

    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.environ.get("GITHUB_REF_NAME", "main")

    def url_for(p):
        return f"https://raw.githubusercontent.com/{repo}/{branch}/{urllib.parse.quote(p.as_posix())}"

    for p in images:
        wait_until_public(url_for(p))

    me = call("GET", "me", {"fields": "user_id,username", "access_token": token})
    ig_id = me.get("user_id") or me["id"]
    kind = "single image" if len(images) == 1 else f"{len(images)}-slide carousel"
    print(f"Posting a {kind} to @{me.get('username')} ...")

    if len(images) == 1:
        container = call("POST", f"{ig_id}/media",
                         {"image_url": url_for(images[0]), "caption": caption, "access_token": token})["id"]
    else:
        children = []
        for p in images:
            child = call("POST", f"{ig_id}/media",
                         {"image_url": url_for(p), "is_carousel_item": "true", "access_token": token})["id"]
            wait_until_ready(child, token)
            children.append(child)
        container = call("POST", f"{ig_id}/media",
                         {"media_type": "CAROUSEL", "children": ",".join(children),
                          "caption": caption, "access_token": token})["id"]

    wait_until_ready(container, token)
    media = call("POST", f"{ig_id}/media_publish", {"creation_id": container, "access_token": token})
    (folder / "posted.txt").write_text(
        f"Posted {datetime.now(TZ):%Y-%m-%d %H:%M %Z}\nMedia ID: {media['id']}\n", encoding="utf-8")
    set_output("posted", "true")
    print(f"Published! Media ID: {media['id']}")


if __name__ == "__main__":
    prepare() if sys.argv[1:] == ["prepare"] else publish()
