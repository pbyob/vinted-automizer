"""KI-Bildanalyse für Kleidungsstücke wahlweise über lokales Ollama (qwen3-vl:8b) oder Google Gemini Cloud."""

import base64
import io
import json
import os
from pathlib import Path
import re
import time
from typing import Literal

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
import httpx
from PIL import Image, ImageOps
from rich.console import Console

from src.models import ClothingListing

# Umgebungsvariablen beim Modulstart laden
load_dotenv()

console = Console()

AIProvider = Literal["ollama", "gemini"]

SUPPORTED_IMAGE_EXTENSIONS: set[str] = {".jpg", ".jpeg", ".png", ".webp"}
CACHE_FILENAME: str = "listing_data.json"

# Cloud-Konfiguration (Google Gemini)
GEMINI_MODEL_NAME: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
MODEL_NAME: str = GEMINI_MODEL_NAME  # Abwärtskompatibler Alias

# Lokale Konfiguration (Ollama + Qwen3-VL)
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL_NAME: str = os.getenv("OLLAMA_MODEL", "qwen3-vl:8b")

REQUIRED_CLOSING_LINE: str = "Aus tierfreiem Nichtraucherhaushalt. Bei Fragen gerne melden!"

# Bild-Komprimierung im Arbeitsspeicher (Originaldateien bleiben unberührt)
GEMINI_MAX_IMAGE_DIMENSION: int = 1600
OLLAMA_MAX_IMAGE_DIMENSION: int = 1280  # Optimal für VRAM & Geschwindigkeit bei 8B-Modellen
AI_JPEG_QUALITY: int = 85

RETRY_WAIT_SECONDS: tuple[int, ...] = (4, 9, 16)

ANALYSIS_PROMPT: str = """Du bist ein erfahrener Second-Hand-Mode-Experte für Vinted.de und Kleinanzeigen.de.
Analysiere alle übergebenen Fotos dieses Kleidungsstücks sorgfältig und erstelle ein verkaufsstarkes, ehrliches Inserat auf Deutsch.

Gehe bei der Bildanalyse systematisch vor:
1. GESAMTANSICHT (Vorder- & Rückseite):
   - Bestimme die genaue Kleidungsart, den Schnitt/Fit (z. B. Regular, Oversized, Slim, Cropped), Kragen-/Ausschnittform, Verschlüsse, Taschen, Prints oder Stickereien sowie die Hauptfarbe (`color` auf Deutsch).
   - Schlage einen präzisen Kategorie-Pfad für Second-Hand-Plattformen vor (`category_hint`, z. B. "Herren > Kleidung > Sweatshirts & Hoodies").

2. ZUSTAND & MÄNGELKONTROLLE (`condition`):
   - Prüfe alle Bilder genau auf Flecken, Löcher, Risse, Pilling, ausgewaschene Farben, Abnutzungen an Bündchen/Kragen oder defekte Reißverschlüsse.
   - Wähle für `condition` EXAKT einen dieser Werte:
     "Neu mit Etikett" | "Neu" | "Sehr gut" | "Gut" | "Zufriedenstellend"
   - Falls Mängel oder Gebrauchsspuren sichtbar sind, erwähne diese transparent und fair in `description`.

3. NACKENETIKETT & LOGOS (`brand`, `size`):
   - Lies den Markennamen (`brand`) und die angegebene Größe (`size`) präzise vom Etikett oder Logo ab.
   - Ist keine Marke erkennbar, setze `brand` exakt auf "No Name".
   - Wähle für `size` EXAKT einen dieser Werte:
     "XS" | "S" | "M" | "L" | "XL" | "XXL" | "One Size"
   - Ordne numerische Größen (z. B. EU 36/38/40/42, Kragenweiten oder Jeansweiten) passend in XS–XXL ein und nenne die genaue Etikett-Angabe zusätzlich in `description`.

4. WASCHETIKETT / MATERIALETIKETT (`material`):
   - Falls ein Waschetikett abfotografiert wurde, lies die genaue Materialzusammensetzung auf Deutsch ab (z. B. "100% Baumwolle" oder "80% Baumwolle, 20% Polyester") und trage sie in `material` ein.
   - Ist kein Waschetikett sichtbar oder unleserlich, setze `material` zwingend auf `null`.

5. MASSBAND / ZOLLSTOCK (`measurements`):
   - Prüfe genau, ob auf Fotos ein Zollstock oder Maßband am Kleidungsstück anliegt.
   - Lies die sichtbaren Maße in Zentimetern sorgfältig ab (z. B. "Brustweite (Achsel zu Achsel): ca. 56 cm, Gesamtlänge: ca. 70 cm") und trage sie in `measurements` ein.
   - WICHTIG: Erfinde niemals Maße! Wenn kein Messwerkzeug im Bild liegt, setze `measurements` zwingend auf `null`.

6. TITEL, BESCHREIBUNG, PREIS & TAGS:
   - `title`: Verkaufsstark, präzise, auf Deutsch, maximal 60 Zeichen lang, inkl. Marke (falls bekannt), Artikelart, Farbe und Größe.
   - `description`: Freundlicher, authentischer Second-Hand-Stil auf Deutsch mit kurzem Einleitungssatz und übersichtlichen Bulletpoints ("- ") zu Details, Passform, Zustand sowie (falls vorhanden) Material und Maßen. Beende die Beschreibung IMMER exakt mit dem Satz:
     "Aus tierfreiem Nichtraucherhaushalt. Bei Fragen gerne melden!"
   - `suggested_price_eur`: Realistischer Second-Hand-Preis in Euro als ganze Zahl (z. B. 15, 25, 45), passend zu Marke, Artikeltyp und Zustand auf Vinted/Kleinanzeigen.
   - `tags`: Exakt 5 passende Suchbegriffe als Hashtags in Kleinschreibung (z. B. ["#streetwear", "#vintage", "#hoodie", "#oversized", "#herrenmode"]).

Antworte AUSSCHLIESSLICH mit einem validen JSON-Objekt gemäß Schema.
"""


def get_provider_display_name(provider: AIProvider) -> str:
    """Gibt eine lesbare Bezeichnung für das gewählte KI-Backend zurück."""
    if provider == "ollama":
        model = os.getenv("OLLAMA_MODEL", OLLAMA_MODEL_NAME)
        return f"Lokal via Ollama ({model})"
    model = os.getenv("GEMINI_MODEL", GEMINI_MODEL_NAME)
    return f"Google Cloud ({model})"


def get_default_provider() -> AIProvider:
    """Ermittelt den konfigurierten Standard-Provider aus der `.env` (Fallback: `ollama`)."""
    raw = os.getenv("AI_PROVIDER", "ollama").strip().lower()
    if raw == "gemini":
        return "gemini"
    return "ollama"


def get_image_files(folder_path: Path) -> list[Path]:
    """Sucht alle unterstützten Bilddateien im angegebenen Ordner und sortiert sie alphabetisch."""
    if not folder_path.exists() or not folder_path.is_dir():
        return []

    image_files = [
        file_path
        for file_path in folder_path.iterdir()
        if file_path.is_file() and file_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    ]
    return sorted(image_files, key=lambda p: p.name.lower())


def normalize_to_rgb(img: Image.Image) -> Image.Image:
    """Korrigiert EXIF-Rotation und konvertiert beliebige Farbmodi (z. B. RGBA) sauber nach RGB."""
    transposed = ImageOps.exif_transpose(img)
    if transposed.mode in ("RGBA", "LA") or (
        transposed.mode == "P" and "transparency" in transposed.info
    ):
        rgba = transposed.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    if transposed.mode != "RGB":
        return transposed.convert("RGB")
    return transposed


def _compress_image_to_jpeg_bytes(img_path: Path, max_dimension: int) -> bytes:
    """Öffnet ein Bild, korrigiert Orientierung/Farbmodus und gibt komprimierte JPEG-Bytes zurück."""
    with Image.open(img_path) as raw_img:
        rgb_img = normalize_to_rgb(raw_img)
        rgb_img.thumbnail(
            (max_dimension, max_dimension),
            Image.Resampling.LANCZOS,
        )
        buffer = io.BytesIO()
        rgb_img.save(buffer, format="JPEG", quality=AI_JPEG_QUALITY, optimize=True)
        return buffer.getvalue()


def _prepare_image_part_for_gemini(img_path: Path) -> types.Part:
    """Verpackt ein Bild als kompaktes JPEG-Part für die Google Gemini API."""
    jpeg_bytes = _compress_image_to_jpeg_bytes(img_path, GEMINI_MAX_IMAGE_DIMENSION)
    return types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg")


def _prepare_image_base64_for_ollama(img_path: Path) -> str:
    """Verpackt ein Bild als Base64-kodierten JPEG-String für die lokale Ollama API."""
    jpeg_bytes = _compress_image_to_jpeg_bytes(img_path, OLLAMA_MAX_IMAGE_DIMENSION)
    return base64.b64encode(jpeg_bytes).decode("ascii")


def _clean_json_text(raw_text: str) -> str:
    """Bereinigt eventuelle Denk-Blöcke oder Markdown-Codeblöcke vor der JSON-Validierung."""
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return cleaned[first_brace : last_brace + 1]
    return cleaned


def _postprocess_listing(listing: ClothingListing) -> ClothingListing:
    """Stellt sicher, dass Schlusssatz und Hashtag-Formatierung konsistent vorhanden sind."""
    if REQUIRED_CLOSING_LINE not in listing.description:
        cleaned_desc = listing.description.rstrip()
        listing.description = f"{cleaned_desc}\n\n{REQUIRED_CLOSING_LINE}"

    normalized_tags: list[str] = []
    for tag in listing.tags:
        clean_tag = tag.strip().lower().replace(" ", "")
        if not clean_tag:
            continue
        if not clean_tag.startswith("#"):
            clean_tag = f"#{clean_tag}"
        normalized_tags.append(clean_tag)
    if normalized_tags:
        listing.tags = normalized_tags[:5]

    return listing


def _discover_fallback_models(client: genai.Client, primary_model: str) -> list[str]:
    """
    Ermittelt bei Google-Server-Überlastung (503) automatisch alternative verfügbare Gemini-Modelle.
    """
    excluded_keywords = (
        "embedding",
        "imagen",
        "veo",
        "tts",
        "audio",
        "live",
        "aqa",
        "gemma",
        "learnlm",
        "robotics",
        "computer-use",
        "1.5",
        "2.0",
        "2.5-flash",
    )
    candidates: list[str] = []
    primary_clean = primary_model.removeprefix("models/")

    try:
        for model_info in client.models.list():
            raw_name = model_info.name or ""
            short_name = raw_name.removeprefix("models/")
            lower_name = short_name.lower()

            if not lower_name.startswith("gemini-") or short_name == primary_clean:
                continue
            if any(bad in lower_name for bad in excluded_keywords):
                continue

            actions = model_info.supported_actions or []
            if actions and "generateContent" not in actions:
                continue

            candidates.append(short_name)
    except Exception:
        return []

    def _tier(name: str) -> int:
        if "flash-lite" in name:
            return 0
        if "flash" in name:
            return 1
        if "pro" in name:
            return 2
        return 3

    candidates.sort(reverse=True)
    candidates.sort(key=_tier)
    return candidates[:4]


def _ensure_api_key() -> str:
    """Prüft, ob GEMINI_API_KEY korrekt konfiguriert ist, und wirft sonst eine klare Fehlermeldung."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "dein_api_key_hier":
        load_dotenv(override=True)
        api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key or api_key == "dein_api_key_hier":
        error_msg = (
            "Kein gültiger GEMINI_API_KEY gefunden! "
            "Bitte trage in '.env' deinen Google Gemini API-Key ein (GEMINI_API_KEY=...) "
            "oder wechsle im Menü auf das lokale Ollama-Modell (qwen3-vl:8b)."
        )
        console.print(f"\n[bold red]Konfigurationsfehler:[/bold red] {error_msg}\n")
        raise RuntimeError(error_msg)

    return api_key


def _check_ollama_ready(base_url: str, desired_model: str) -> str:
    """
    Prüft, ob der lokale Ollama-Server läuft und ob das gewünschte Modell (z. B. `qwen3-vl:8b`)
    bereits vollständig heruntergeladen ist. Gibt den exakten Modellnamen zurück.
    """
    try:
        resp = httpx.get(f"{base_url}/api/tags", timeout=5.0)
        resp.raise_for_status()
        payload = resp.json()
    except httpx.RequestError as exc:
        error_msg = (
            f"Lokaler Ollama-Server ist unter '{base_url}' nicht erreichbar ({exc}). "
            "Bitte stelle sicher, dass Ollama gestartet ist (Ollama-App öffnen oder 'ollama serve' ausführen)."
        )
        console.print(f"\n[bold red]Ollama nicht erreichbar:[/bold red] {error_msg}\n")
        raise RuntimeError(error_msg) from exc

    installed_models: list[str] = [
        str(m.get("name", "")) for m in payload.get("models", []) if m.get("name")
    ]

    # Exakter Treffer (z. B. "qwen3-vl:8b")
    for name in installed_models:
        if name.lower() == desired_model.lower():
            return name

    # Falls ohne Tag angegeben (z. B. "qwen3-vl" -> "qwen3-vl:latest")
    if ":" not in desired_model:
        for name in installed_models:
            if name.lower() == f"{desired_model.lower()}:latest":
                return name

    available_str = ", ".join(installed_models) if installed_models else "noch keine Modelle fertig geladen"
    error_msg = (
        f"Das lokale Modell '{desired_model}' ist in Ollama noch nicht verfügbar "
        f"(aktuell installierte Modelle: {available_str}).\n"
        f"Bitte warte kurz, bis 'ollama pull {desired_model}' abgeschlossen ist, und versuche es erneut."
    )
    console.print(f"\n[bold yellow]Modell noch nicht bereit:[/bold yellow] {error_msg}\n")
    raise RuntimeError(error_msg)


def _analyze_with_ollama(image_paths: list[Path]) -> ClothingListing:
    """Führt die Bildanalyse komplett lokal über Ollama (`qwen3-vl:8b`) mit JSON-Schema aus."""
    load_dotenv(override=True)
    base_url = os.getenv("OLLAMA_BASE_URL", OLLAMA_BASE_URL).rstrip("/")
    desired_model = os.getenv("OLLAMA_MODEL", OLLAMA_MODEL_NAME).strip()

    resolved_model = _check_ollama_ready(base_url, desired_model)
    encoded_images = [_prepare_image_base64_for_ollama(p) for p in image_paths]
    json_schema = ClothingListing.model_json_schema()

    request_body: dict[str, object] = {
        "model": resolved_model,
        "messages": [
            {
                "role": "user",
                "content": ANALYSIS_PROMPT,
                "images": encoded_images,
            }
        ],
        "format": json_schema,
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.2,
            "num_ctx": 16384,
        },
    }

    timeout = httpx.Timeout(connect=10.0, read=600.0, write=60.0, pool=10.0)
    with httpx.Client(timeout=timeout) as http_client:
        resp = http_client.post(f"{base_url}/api/chat", json=request_body)
        # Fallback für ältere Ollama-Versionen ohne "think"-Parameter
        if resp.status_code == 400 and "think" in resp.text.lower():
            request_body.pop("think", None)
            resp = http_client.post(f"{base_url}/api/chat", json=request_body)

        if resp.status_code >= 400:
            raise RuntimeError(
                f"Ollama API-Fehler (HTTP {resp.status_code}): {resp.text}"
            )

        data = resp.json()

    raw_content = str(data.get("message", {}).get("content", "")).strip()
    if not raw_content:
        raise RuntimeError(
            f"Das lokale Modell '{resolved_model}' hat eine leere Antwort geliefert. Bitte erneut versuchen."
        )

    cleaned_json = _clean_json_text(raw_content)
    return ClothingListing.model_validate_json(cleaned_json)


def _analyze_with_gemini(image_paths: list[Path]) -> ClothingListing:
    """Führt die Bildanalyse in der Cloud über die Google Gemini Vision API aus."""
    _ensure_api_key()
    image_parts: list[types.Part] = [
        _prepare_image_part_for_gemini(img_path) for img_path in image_paths
    ]

    active_model = os.getenv("GEMINI_MODEL", GEMINI_MODEL_NAME)
    client = genai.Client(http_options=types.HttpOptions(timeout=120_000))
    contents: list[object] = [*image_parts, ANALYSIS_PROMPT]
    gen_config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=ClothingListing,
        temperature=0.2,
    )

    response = None
    last_error: Exception | None = None
    total_attempts = len(RETRY_WAIT_SECONDS) + 1

    for attempt in range(1, total_attempts + 1):
        try:
            response = client.models.generate_content(
                model=active_model,
                contents=contents,
                config=gen_config,
            )
            break
        except genai_errors.APIError as err:
            last_error = err
            if err.code in {429, 500, 503, 504} and attempt < total_attempts:
                wait_seconds = RETRY_WAIT_SECONDS[attempt - 1]
                console.print(
                    f"[yellow]Google-Server für '{active_model}' sind gerade stark ausgelastet "
                    f"(Code {err.code}) – warte {wait_seconds}s vor Versuch {attempt + 1}/{total_attempts}...[/yellow]"
                )
                time.sleep(wait_seconds)
            elif err.code in {429, 503}:
                break
            else:
                raise

    if response is None:
        fallback_models = _discover_fallback_models(client, active_model)
        for fb_model in fallback_models:
            console.print(
                f"[cyan]Wechsle wegen Server-Überlastung automatisch auf Ausweichmodell: "
                f"[bold]{fb_model}[/bold]...[/cyan]"
            )
            try:
                response = client.models.generate_content(
                    model=fb_model,
                    contents=contents,
                    config=gen_config,
                )
                if response and response.text:
                    break
            except genai_errors.APIError as fb_err:
                last_error = fb_err
                continue

    if response is None or not response.text:
        if last_error is not None:
            raise last_error
        raise RuntimeError(
            "Die Gemini API hat eine leere Antwort zurückgegeben. Bitte erneut versuchen."
        )

    cleaned_json = _clean_json_text(response.text)
    return ClothingListing.model_validate_json(cleaned_json)


def analyze_item_folder(
    folder_path: Path,
    force_reanalyze: bool = False,
    provider: AIProvider | None = None,
) -> ClothingListing:
    """
    Analysiert alle Kleidungsfotos in `folder_path` wahlweise lokal über Ollama (`qwen3-vl:8b`)
    oder in der Cloud über Google Gemini Vision (`gemini-3.8-flash`).

    Nutzt `listing_data.json` als lokalen Cache, sofern vorhanden und `force_reanalyze=False`.
    """
    folder_path = Path(folder_path)
    if not folder_path.exists() or not folder_path.is_dir():
        raise FileNotFoundError(f"Artikel-Ordner existiert nicht: {folder_path}")

    cache_path = folder_path / CACHE_FILENAME

    # 1. Caching-Check
    if cache_path.exists() and not force_reanalyze:
        console.print(
            f"[dim]Lade vorhandene Analyse aus Cache: [cyan]{cache_path.name}[/cyan][/dim]"
        )
        cached_json = cache_path.read_text(encoding="utf-8")
        return ClothingListing.model_validate_json(cached_json)

    # 2. Bilder prüfen
    image_paths = get_image_files(folder_path)
    if not image_paths:
        raise ValueError(
            f"Keine Bilddateien (.jpg, .jpeg, .png, .webp) im Ordner '{folder_path}' gefunden."
        )

    selected_provider: AIProvider = provider or get_default_provider()

    # 3. KI-Analyse je nach gewähltem Backend ausführen
    if selected_provider == "ollama":
        listing = _analyze_with_ollama(image_paths)
    elif selected_provider == "gemini":
        listing = _analyze_with_gemini(image_paths)
    else:
        raise ValueError(f"Unbekannter KI-Provider: {selected_provider}")

    # 4. Nachbearbeitung & Speichern im Ordner-Cache
    listing = _postprocess_listing(listing)

    formatted_json = json.dumps(
        listing.model_dump(),
        indent=2,
        ensure_ascii=False,
    )
    cache_path.write_text(formatted_json + "\n", encoding="utf-8")

    return listing
