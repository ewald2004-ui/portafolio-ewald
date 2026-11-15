#!/usr/bin/env python3
"""
Sincroniza el portafolio con el perfil público de Behance.

Fuente principal: feed RSS oficial del perfil.
Fuente secundaria (opcional): página del perfil, para estadísticas, colores y experiencia.

Uso:
    python3 scripts/sync_behance.py

Genera:
    data/behance.json     Datos completos (fuente de verdad).
    data/behance.js       Los mismos datos, cargables con <script> (funciona en file://).
    assets/projects/      Portadas descargadas, nombradas por ID de proyecto.
"""

from __future__ import annotations

import html
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

USERNAME = "ewaldzv"
BEHANCE_URL = f"https://www.behance.net/{USERNAME}"
FEED_URL = f"https://www.behance.net/feeds/user?username={USERNAME}"

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
IMG_DIR = ROOT / "assets" / "projects"
JSON_FILE = DATA_DIR / "behance.json"
JS_FILE = DATA_DIR / "behance.js"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = 60

FALLBACK_PROFILE = {
    "username": USERNAME,
    "name": "Ewald Zamorano Valero",
    "role": "Director de Arte",
    "location": "Santiago, Chile",
    "email": "ewald2004@gmail.com",
    "url": BEHANCE_URL,
    "stats": {"views": 0, "appreciations": 0, "followers": 0},
    "agencies": [],
    "experience": [],
}


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"  {msg}")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        return res.read()


def fetch_text(url: str) -> str:
    return fetch(url).decode("utf-8", errors="replace")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = parsedate_to_datetime(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def gallery_id(url: str) -> str:
    match = re.search(r"/gallery/(\d+)", url)
    return match.group(1) if match else re.sub(r"\W+", "-", url).strip("-")


def short_title(name: str) -> str:
    """Acorta solo los títulos inmobiliarios, que son muy largos para la grilla."""
    return re.sub(r"^Proyecto Inmobiliario Exxacon\s+", "Exxacon ", name, flags=re.I)


def category_for(name: str) -> str:
    n = name.lower()
    if "video" in n:
        return "Videos"
    if "stand" in n:
        return "Stand"
    if "inmobiliari" in n:
        return "Inmobiliario"
    if "campa" in n or "campaign" in n:
        return "Campañas"
    return "Proyecto"


# Orden en que aparecen las categorías en el sitio (y en los filtros).
CATEGORY_ORDER = ["Campañas", "Videos", "Inmobiliario", "Stand", "Proyecto"]

# Proyecto que siempre aparece primero en la grilla, sin importar la fecha.
# Corresponde a "Campaña Aniversario Clínica Santa María".
FEATURED_ID = "257692161"


def sort_key(project: dict) -> tuple[int, int, int]:
    """Primero el proyecto destacado, luego por categoría y por fecha descendente."""
    is_featured = 0 if str(project.get("id")) == FEATURED_ID else 1
    category = project.get("category", "Proyecto")
    rank = (
        CATEGORY_ORDER.index(category)
        if category in CATEGORY_ORDER
        else len(CATEGORY_ORDER)
    )
    return (is_featured, rank, -int(project.get("publishedOn") or 0))


def color_hex(color: dict | None) -> str:
    if not color:
        return "#1a1a1e"
    try:
        return "#{:02x}{:02x}{:02x}".format(
            int(color["r"]), int(color["g"]), int(color["b"])
        )
    except (KeyError, TypeError, ValueError):
        return "#1a1a1e"


def detect_extension(content: bytes) -> str:
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    if content[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return ".webp"


# ---------------------------------------------------------------------------
# Lectura de fuentes
# ---------------------------------------------------------------------------

def load_previous() -> dict:
    if not JSON_FILE.exists():
        return {}
    try:
        return json.loads(JSON_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def fetch_feed_projects() -> list[dict]:
    """Lee el RSS público. Devuelve los proyectos con portada en 808 px."""
    xml = fetch_text(FEED_URL)
    root = ET.fromstring(xml)
    items = root.findall("./channel/item")
    if not items:
        raise RuntimeError("El feed de Behance no devolvió proyectos.")

    projects = []
    for item in items:
        title = clean_text(item.findtext("title"))
        link = clean_text(item.findtext("link")).split("?")[0]
        if not title or not link:
            continue

        description = item.findtext("description") or ""
        img_match = re.search(r"<img\s+src=['\"]([^'\"]+)['\"]", description, re.I)
        cover = img_match.group(1) if img_match else ""
        # El feed entrega 404 px; se pide la versión de 808 px en WebP.
        cover = cover.replace("/projects/404/", "/projects/max_808_webp/")

        published = parse_date(item.findtext("pubDate"))
        projects.append(
            {
                "id": gallery_id(link),
                "title": short_title(title),
                "full": title,
                "url": link,
                "cover": cover,
                "publishedOn": int(published.timestamp()),
                "year": published.year,
                "category": category_for(title),
            }
        )

    projects.sort(key=sort_key)
    return projects


def fetch_profile_meta() -> dict:
    """Intenta leer estadísticas, colores y experiencia desde la página del perfil."""
    page = fetch_text(BEHANCE_URL)
    match = re.search(
        r'<script type="application/json" id="beconfig-store_state">(.*?)</script>',
        page,
        re.S,
    )
    if not match:
        raise RuntimeError("No se encontró el estado del perfil en la página.")

    state = json.loads(match.group(1))
    profile_state = state.get("profile", {})
    user = profile_state.get("user", {})
    work = profile_state.get("activeSection", {}).get("work", {})

    stats = user.get("stats") or {}
    meta = {
        "name": clean_text(user.get("displayName")) or FALLBACK_PROFILE["name"],
        "role": clean_text(user.get("occupation")) or FALLBACK_PROFILE["role"],
        "location": clean_text(user.get("location")) or FALLBACK_PROFILE["location"],
        "stats": {
            "views": int(stats.get("views") or 0),
            "appreciations": int(stats.get("appreciations") or 0),
            "followers": int(stats.get("followers") or 0),
        },
        "experience": [
            {
                "position": clean_text(x.get("position")),
                "organization": clean_text(x.get("organization")),
                "location": clean_text(x.get("location")),
            }
            for x in (user.get("workExperiences") or [])
            if x.get("organization")
        ],
        "colors": {},
        "covers": {},
    }

    meta["agencies"] = [
        e["organization"] for e in meta["experience"] if e.get("organization")
    ]

    for project in work.get("profileProjects") or []:
        pid = str(project.get("id"))
        meta["colors"][pid] = color_hex(project.get("colors"))
        covers = (project.get("covers") or {}).get("allAvailable") or []
        best = next(
            (
                c["url"]
                for c in covers
                if c.get("width") == 808 and "webp" in str(c.get("url", "")).lower()
            ),
            None,
        )
        if best:
            meta["covers"][pid] = best

    return meta


# ---------------------------------------------------------------------------
# Descarga de portadas
# ---------------------------------------------------------------------------

def download_cover(project: dict, preferred_url: str | None) -> str:
    url = preferred_url or project.get("cover")
    if not url:
        return ""

    content = fetch(url)
    ext = detect_extension(content)
    filename = f"{project['id']}{ext}"
    dest = IMG_DIR / filename

    if dest.exists() and dest.read_bytes() == content:
        log(f"sin cambios · {filename}")
    else:
        dest.write_bytes(content)
        log(f"descargada · {filename} ({len(content) // 1024} KB)")

    # Si antes tenía otra extensión, se elimina.
    for old in IMG_DIR.glob(f"{project['id']}.*"):
        if old != dest:
            old.unlink()

    return f"assets/projects/{filename}"


def prune_covers(valid_names: set[str]) -> None:
    for file in IMG_DIR.glob("*"):
        if file.is_file() and file.name not in valid_names:
            file.unlink()
            log(f"eliminada · {file.name}")


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------

def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMG_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nSincronizando {BEHANCE_URL}\n")

    previous = load_previous()
    previous_profile = previous.get("profile", {})
    profile = {**FALLBACK_PROFILE, **previous_profile}
    profile["username"] = USERNAME
    profile["url"] = BEHANCE_URL

    print("1/3 · Leyendo proyectos del feed de Behance")
    projects = fetch_feed_projects()
    log(f"{len(projects)} proyectos encontrados")

    print("\n2/3 · Leyendo estadísticas del perfil")
    try:
        meta = fetch_profile_meta()
        profile.update(
            {
                "name": meta["name"],
                "role": meta["role"],
                "location": meta["location"],
                "stats": meta["stats"],
                "experience": meta["experience"],
                "agencies": meta["agencies"],
            }
        )
        log(f"vistas: {meta['stats']['views']} · apreciaciones: {meta['stats']['appreciations']}")
    except (urllib.error.URLError, RuntimeError, json.JSONDecodeError, ValueError) as exc:
        meta = {}
        log(f"no disponible ({exc}); se conservan las estadísticas anteriores")

    print("\n3/3 · Descargando portadas")
    for project in projects:
        preferred = (meta.get("covers") or {}).get(project["id"])
        project["img"] = download_cover(project, preferred)
        project["color"] = (meta.get("colors") or {}).get(project["id"], "#1a1a1e")
        project.pop("cover", None)

    prune_covers({Path(p["img"]).name for p in projects if p.get("img")})

    data = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": FEED_URL,
        "profile": profile,
        "projects": projects,
    }

    previous_comparable = {
        "source": previous.get("source"),
        "profile": previous.get("profile"),
        "projects": previous.get("projects"),
    }
    current_comparable = {
        "source": data["source"],
        "profile": data["profile"],
        "projects": data["projects"],
    }

    if previous and previous_comparable == current_comparable:
        print(f"\nSin cambios: {len(projects)} proyectos ya estaban al día.")
        print(f"Última actualización: {previous.get('generatedAt')}\n")
        return 0

    JSON_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    JS_FILE.write_text(
        "/* Archivo generado por scripts/sync_behance.py. No editar a mano. */\n"
        "window.BEHANCE_DATA = "
        + json.dumps(data, ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )

    print(f"\nListo: {len(projects)} proyectos en data/behance.json")
    print(f"Actualizado: {data['generatedAt']}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (urllib.error.URLError, ET.ParseError, TimeoutError) as exc:
        print(f"\nError de red al sincronizar: {exc}\n", file=sys.stderr)
        sys.exit(1)
