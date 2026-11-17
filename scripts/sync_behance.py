#!/usr/bin/env python3
"""
Sincroniza el portafolio con el perfil público de Behance.

Fuentes:
    1. GraphQL público de Behance (todos los proyectos, paginado).
    2. Feed RSS del perfil (respaldo si GraphQL falla).
    3. Página del perfil, para estadísticas y experiencia.
    Las imágenes y videos de cada proyecto se descargan completos al repositorio.

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
import shutil
import sys
import urllib.error
import urllib.request
import uuid
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
GRAPHQL_URL = "https://www.behance.net/v3/graphql"

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
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://www.behance.net/",
        },
    )
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
# Corresponde a "Campaña Aerochamber Laboratorios ABBOTT".
FEATURED_ID = "257691301"

# Correcciones de títulos de Behance (errata original → título mostrado).
# Se aplican por ID en cada sincronización, así que no se pierden.
TITLE_FIXES = {
    # Erratas evidentes.
    "257689665": "Campaña Endometriosis Laboratorios ABBOTT",
    "57730621": "Universidad Diego Portales. Admisión",
    "117714431": "CAMPAÑA BLACK FRIDAY CHILI'S RESTAURANT",
    "117229075": "POSTEO PROMOCIÓN GIMNASIO EN PROVIDENCIA",
    "57494915": "Atton Hoteles Expansión",
    "75724679": "Campaña Bomberos de Chile. Hazte Socio",
    # Nombres de marca con su escritura oficial.
    "257691027": "Campaña RRSS BioGaia Far Laboratorios ABBOTT",
    "57729889": "DirecTV Campaña Malos. Navidad",
    "57497373": "Packaging DirecTV Prepago",
    "58453921": "Post Halloween Chili's",
    "117717263": "CAMPAÑA CHILI'S COSTANERA CENTER",
    # Proyectos distintos que se llamaban igual: se distinguen por su portada.
    "117225939": "Campaña Clínica NCA 2020 — Hidratación Profunda",
    "117225541": "Campaña Clínica NCA 2020 — Tu Belleza es Natural",
    "117225851": "Campaña Clínica NCA 2021 — Depilación Láser",
    "117225655": "Campaña Clínica NCA 2021 — Lipoescultura",
    "57729827": "BBVA Hipotecario 120% — 1 de 2",
    "57800611": "BBVA Hipotecario 120% — 2 de 2",
}


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


def best_cover(node: dict) -> str:
    """Elige la portada de 808 px en WebP; si no existe, la de mayor ancho."""
    covers = (node.get("covers") or {}).get("allAvailable") or []
    for cover in covers:
        url = str(cover.get("url") or "")
        if cover.get("width") == 808 and "webp" in url.lower():
            return url
    usable = [c for c in covers if c.get("url")]
    if not usable:
        return ""
    return str(max(usable, key=lambda c: int(c.get("width") or 0))["url"])


def graphql_call(query: str, variables: dict, token: str) -> dict:
    """Llama al GraphQL de Behance con la cookie `bcp` que exige su API."""
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "x-bcp": token,
            "x-requested-with": "XMLHttpRequest",
            "Origin": "https://www.behance.net",
            "Referer": BEHANCE_URL,
            "Cookie": f"bcp={token}",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        data = json.loads(response.read().decode("utf-8", errors="replace"))
    if data.get("errors"):
        raise RuntimeError(
            "GraphQL devolvió un error: "
            + json.dumps(data["errors"], ensure_ascii=False)[:300]
        )
    return data


def fetch_graphql_projects(token: str) -> list[dict]:
    """Pide todos los proyectos del perfil por GraphQL, paginando de 12 en 12.

    Behance exige una cookie `bcp` y la misma cabecera `x-bcp`; cualquier UUID
    sirve, así que se genera uno nuevo en cada sincronización.
    """
    query = (
        "query ProfileProjects($u: String!, $a: String) {"
        " user(username: $u) {"
        " profileProjects(first: 12, after: $a) {"
        " nodes { id name url publishedOn"
        " covers { allAvailable { url width height } }"
        " colors { r g b } }"
        " pageInfo { endCursor hasNextPage } } } }"
    )
    after: str | None = None
    nodes: list[dict] = []
    seen: set[str] = set()

    for page in range(1, 51):
        data = graphql_call(query, {"u": USERNAME, "a": after}, token)
        page_data = (
            ((data.get("data") or {}).get("user") or {}).get("profileProjects") or {}
        )
        page_nodes = page_data.get("nodes") or []
        for node in page_nodes:
            pid = str(node.get("id") or "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            nodes.append(node)

        info = page_data.get("pageInfo") or {}
        log(f"página {page}: {len(page_nodes)} proyectos · acumulado {len(nodes)}")
        if not info.get("hasNextPage"):
            break
        after = info.get("endCursor")
        if not after:
            break

    if not nodes:
        raise RuntimeError("GraphQL no devolvió proyectos.")

    projects = []
    for node in nodes:
        name = clean_text(node.get("name"))
        url = clean_text(node.get("url")).split("?")[0]
        if not name or not url:
            continue
        published_on = int(node.get("publishedOn") or 0)
        published = (
            datetime.fromtimestamp(published_on, tz=timezone.utc)
            if published_on
            else datetime.now(timezone.utc)
        )
        projects.append(
            {
                "id": str(node.get("id")),
                "title": short_title(name),
                "full": name,
                "url": url,
                "cover": best_cover(node),
                "publishedOn": int(published.timestamp()),
                "year": published.year,
                "category": category_for(name),
                "color": color_hex(node.get("colors")),
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
# Descarga del contenido completo de cada proyecto
# ---------------------------------------------------------------------------

MODULES_QUERY = (
    "query ProjectMedia($id: ProjectId!) {"
    " project(id: $id) { modules { nodes {"
    " __typename"
    " ... on ImageModule { id src }"
    " ... on VideoModule { id src thumbnailUrl }"
    " } } } }"
)


def local_media(pid: str, stem: str, extensions: tuple[str, ...]) -> str:
    """Devuelve la ruta local de un archivo ya descargado, si existe."""
    folder = IMG_DIR / str(pid)
    for ext in extensions:
        file = folder / f"{stem}{ext}"
        if file.exists():
            return f"assets/projects/{pid}/{file.name}"
    return ""


def save_media(pid: str, stem: str, content: bytes, ext: str | None = None) -> str:
    folder = IMG_DIR / str(pid)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{stem}{ext or detect_extension(content)}"
    if not (dest.exists() and dest.read_bytes() == content):
        dest.write_bytes(content)
        log(f"descargado · {pid}/{dest.name} ({len(content) // 1024} KB)")
    for old in folder.glob(f"{stem}.*"):
        if old != dest:
            old.unlink()
    return f"assets/projects/{pid}/{dest.name}"


def fetch_image(url: str) -> bytes:
    """Pide la mejor versión disponible: 1200 px y, si no existe, la original."""
    candidates = (
        url.replace("/disp/", "/max_1200/"),
        url.replace("/disp/", "/source/"),
        url,
    )
    last: Exception | None = None
    for candidate in candidates:
        try:
            return fetch(candidate)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in (400, 403, 404):
                raise
    raise RuntimeError(f"sin imagen disponible ({last})")


def fetch_video(url: str) -> bytes:
    """El reproductor de Behance expone un MP4 firmado; se extrae y se guarda."""
    page = fetch_text(url)
    match = re.search(r'https?://[^"\'<> ]+\.mp4[^"\'<> ]*', page, re.I)
    if not match:
        raise RuntimeError("el reproductor no entregó un MP4")
    return fetch(match.group(0).replace("&amp;", "&"))


def fetch_project_media(
    project: dict, token: str, previous_media: list[dict] | None = None
) -> list[dict]:
    """Descarga todas las imágenes y videos de un proyecto."""
    pid = str(project["id"])
    try:
        data = graphql_call(MODULES_QUERY, {"id": pid}, token)
        nodes = (
            (((data.get("data") or {}).get("project") or {}).get("modules") or {}).get(
                "nodes"
            )
            or []
        )
    except (
        urllib.error.URLError,
        RuntimeError,
        json.JSONDecodeError,
        ValueError,
        TimeoutError,
    ) as exc:
        log(f"contenido no disponible ({exc}); se conserva el anterior")
        return previous_media or []

    media: list[dict] = []
    for node in nodes:
        kind = node.get("__typename")
        mid = str(node.get("id") or "")
        if not mid:
            continue
        stem = f"m{mid}"

        if kind == "ImageModule":
            existing = local_media(pid, stem, (".webp", ".jpg", ".png"))
            if existing:
                media.append({"type": "image", "src": existing})
                continue
            src = node.get("src") or ""
            if not src:
                continue
            try:
                media.append({"type": "image", "src": save_media(pid, stem, fetch_image(src))})
            except (urllib.error.URLError, RuntimeError, TimeoutError, ValueError) as exc:
                log(f"sin imagen {stem} ({exc})")

        elif kind == "VideoModule":
            video = local_media(pid, stem, (".mp4",))
            if not video:
                src = node.get("src") or ""
                if not src:
                    continue
                try:
                    video = save_media(pid, stem, fetch_video(src), ext=".mp4")
                except (
                    urllib.error.URLError,
                    RuntimeError,
                    TimeoutError,
                    ValueError,
                ) as exc:
                    log(f"sin video {stem} ({exc})")
                    continue
            poster = local_media(pid, f"{stem}-poster", (".webp", ".jpg", ".png"))
            thumb = node.get("thumbnailUrl") or ""
            if not poster and thumb:
                try:
                    poster = save_media(pid, f"{stem}-poster", fetch(thumb))
                except (urllib.error.URLError, TimeoutError, ValueError):
                    poster = ""
            item = {"type": "video", "src": video}
            if poster:
                item["poster"] = poster
            media.append(item)

    if not media and project.get("img"):
        media = [{"type": "image", "src": project["img"]}]
    return media or previous_media or []


def prune_media(valid_files: dict[str, set[str]]) -> None:
    """Elimina carpetas y archivos de proyectos que ya no existen."""
    for folder in IMG_DIR.iterdir():
        if not folder.is_dir():
            continue
        names = valid_files.get(folder.name)
        if names is None:
            shutil.rmtree(folder)
            log(f"eliminada carpeta · {folder.name}")
            continue
        for file in folder.iterdir():
            if file.is_file() and file.name not in names:
                file.unlink()
                log(f"eliminada · {folder.name}/{file.name}")


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

    token = str(uuid.uuid4())
    print("1/4 · Leyendo proyectos de Behance")
    source = GRAPHQL_URL
    try:
        projects = fetch_graphql_projects(token)
        log(f"{len(projects)} proyectos encontrados (GraphQL)")
    except (
        urllib.error.URLError,
        RuntimeError,
        json.JSONDecodeError,
        ValueError,
        TimeoutError,
    ) as exc:
        source = FEED_URL
        log(f"GraphQL no disponible ({exc})")
        log("usando el feed RSS como respaldo")
        fresh = fetch_feed_projects()
        previous_projects = previous.get("projects") or []
        if previous_projects:
            # El RSS solo trae los últimos 12: se conserva todo lo anterior y se
            # actualiza/agrega lo que venga en el feed, para no perder proyectos.
            merged = {str(p.get("id")): p for p in previous_projects}
            for item in fresh:
                base = merged.get(str(item.get("id")), {})
                merged[str(item.get("id"))] = {**base, **item}
            projects = list(merged.values())
            projects.sort(key=sort_key)
            log(f"{len(projects)} proyectos conservados (feed: {len(fresh)})")
        else:
            projects = fresh
            log(f"{len(projects)} proyectos encontrados (RSS)")

    # Correcciones de títulos definidas a mano (erratas y nombres repetidos).
    fixed_count = 0
    for project in projects:
        fixed = TITLE_FIXES.get(str(project.get("id")))
        if fixed:
            project["full"] = fixed
            project["title"] = short_title(fixed)
            project["category"] = category_for(fixed)
            fixed_count += 1
    if fixed_count:
        log(f"{fixed_count} títulos corregidos")
    projects.sort(key=sort_key)

    print("\n2/4 · Leyendo estadísticas del perfil")
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

    print("\n3/4 · Descargando portadas")
    for project in projects:
        preferred = (meta.get("covers") or {}).get(project["id"]) or project.get("cover")
        if preferred:
            project["img"] = download_cover(project, preferred)
        elif project.get("img"):
            # Sin portada nueva disponible (p. ej. respaldo RSS): se conserva la actual.
            pass
        else:
            project["img"] = ""
        project["color"] = project.get("color") or (
            meta.get("colors") or {}
        ).get(project["id"], "#1a1a1e")
        project.pop("cover", None)

    prune_covers({Path(p["img"]).name for p in projects if p.get("img")})

    print("\n4/4 · Descargando contenido de cada proyecto")
    previous_media = {
        str(p.get("id")): p.get("media") or []
        for p in (previous.get("projects") or [])
    }
    valid_files: dict[str, set[str]] = {}
    for project in projects:
        pid = str(project["id"])
        if source == GRAPHQL_URL:
            media = fetch_project_media(project, token, previous_media.get(pid, []))
        else:
            media = previous_media.get(pid, [])
        project["media"] = media
        names: set[str] = set()
        for item in media:
            for key in ("src", "poster"):
                if item.get(key):
                    names.add(Path(item[key]).name)
        valid_files[pid] = names
        log(f"{pid}: {len(media)} archivos")
    prune_media(valid_files)

    data = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
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
