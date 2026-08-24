"""Download missing restaurant menu photos from Wikimedia Commons.

The generated CREDITS.md records the source and license for every downloaded
asset so the images remain traceable and reusable.
"""

from __future__ import annotations

import html
import io
import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "media" / "items"
API_URL = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "NuvanaPOS/1.0 (restaurant menu image importer)"

ITEMS = {
    "Veg Thali": "Indian vegetarian thali",
    "Mixed Veg Curry": "Indian mixed vegetable curry",
    "Non-Veg Thali": "Indian non vegetarian thali",
    "Paneer Tikka": "paneer tikka",
    "Veg Spring Roll": "vegetable spring rolls",
    "Dal Tadka": "dal tadka",
    "Paneer Butter Masala": "paneer butter masala",
    "Aloo Tikki Burger": "File:Aloo tikki burger (homemade).jpg",
    "Chicken Burger": "chicken burger",
    "Veg Pizza": "vegetable pizza",
    "French Fries": "french fries",
    "Masala Chai": "masala chai",
    "Cold Coffee": "File:Homemade Cold Coffee(Indian Style)- Kolkata.jpg",
    "Fresh Lime Soda": "File:Lemon lime soda.jpg",
    "Gulab Jamun": "gulab jamun",
    "Ice Cream": "File:Ice cream sundae.jpg",
    "Chocolate Brownie": "chocolate brownie",
}


def open_url(request: Request, *, timeout: int):
    for attempt in range(5):
        try:
            return urlopen(request, timeout=timeout)
        except HTTPError as exc:
            if exc.code != 429 or attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))


def get_json(params: dict[str, str | int]) -> dict:
    request = Request(f"{API_URL}?{urlencode(params)}", headers={"User-Agent": USER_AGENT})
    with open_url(request, timeout=30) as response:
        return json.load(response)


def select_image(query: str) -> dict:
    if query.startswith("File:"):
        data = get_json({
            "action": "query",
            "titles": query,
            "prop": "imageinfo",
            "iiprop": "url|mime|extmetadata",
            "iiurlwidth": 1200,
            "format": "json",
            "formatversion": 2,
        })
        candidate = data.get("query", {}).get("pages", [{}])[0]
        info = (candidate.get("imageinfo") or [{}])[0]
        if info.get("thumburl"):
            return {"title": candidate["title"], **info}
        raise RuntimeError(f"Wikimedia Commons file not found: {query!r}")

    data = get_json({
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": 10,
        "prop": "imageinfo",
        "iiprop": "url|mime|extmetadata",
        "iiurlwidth": 1200,
        "format": "json",
        "formatversion": 2,
    })
    candidates = data.get("query", {}).get("pages", [])
    for candidate in candidates:
        info = (candidate.get("imageinfo") or [{}])[0]
        if info.get("mime", "").startswith("image/") and info.get("thumburl"):
            return {"title": candidate["title"], **info}
    raise RuntimeError(f"No usable Wikimedia Commons image found for {query!r}")


def clean(value: str) -> str:
    return " ".join(html.unescape(value or "").replace("<br>", " ").split())


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    credits = ["# Restaurant menu image credits", "", "Downloaded from Wikimedia Commons.", ""]
    for item_name, query in ITEMS.items():
        info = select_image(query)
        request = Request(info["thumburl"], headers={"User-Agent": USER_AGENT})
        with open_url(request, timeout=60) as response:
            image = Image.open(io.BytesIO(response.read())).convert("RGB")
        image = ImageOps.fit(image, (1000, 750), method=Image.Resampling.LANCZOS)
        output = OUTPUT_DIR / f"{item_name}.webp"
        image.save(output, "WEBP", quality=84, method=6)

        metadata = info.get("extmetadata", {})
        license_name = clean(metadata.get("LicenseShortName", {}).get("value", "Unknown license"))
        artist = clean(metadata.get("Artist", {}).get("value", "Unknown author"))
        credits.extend([
            f"- **{item_name}** — [{info['title']}]({info['descriptionurl']})",
            f"  Author: {artist}; license: {license_name}.",
        ])
        print(f"Downloaded {item_name}: {info['title']}")
        time.sleep(2)

    (OUTPUT_DIR / "CREDITS.md").write_text("\n".join(credits) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
