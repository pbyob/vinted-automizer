"""KI-Bildanalyse für Kleidungsstücke über die Google Gemini Vision API inkl. JSON-Caching."""

import io
import json
import os
from pathlib import Path
import time

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from PIL import Image, ImageOps
from rich.console import Console

from src.models import ClothingListing

# Umgebungsvariablen beim Modulstart laden
load_dotenv()

console = Console()

SUPPORTED_IMAGE_EXTENSIONS: set[str] = {".jpg", ".jpeg", ".png", ".webp"}
CACHE_FILENAME: str = "listing_data.json"
MODEL_NAME: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
REQUIRED_CLOSING_LINE: str = "Aus tierfreiem Nichtraucherhaushalt. Bei Fragen gerne melden!"

# Maximale Kantenlänge für die KI-Analyse (spart massiv Upload-Zeit & vermeidet 503-Timeouts
# bei 40+ MB Smartphone-PNGs, während Etiketten & Zollstöcke gestochen scharf bleiben)
AI_MAX_IMAGE_DIMENSION: int = 1600
AI_JPEG_QUALITY: int = 85
MAX_API_RETRIES: int = 3

ANALYSIS_PROMPT: str = """Du bist ein erfahrener Second-Hand-Mode-Experte für Vinted.de und Kleinanzeigen.de.
Analysiere alle übergebenen Fotos dieses Kleidungsstücks sorgfältig und erstelle ein verkaufsstarkes, ehrliches Inserat auf Deutsch.

Gehe bei der Bildanalyse systematisch vor:
1. GESAMTANSICHT (Vorder- & Rückseite):
   - Bestimme die genaue Kleidungsart, den Schnitt/Fit (z. B. Regular, Oversized, Slim, Cropped), Kragen-/Ausschnittform, Verschlüsse, Taschen, Prints oder Stickereien sowie die Hauptfarbe.
   - Schlage einen präzisen Kategorie-Pfad für Second-Hand-Plattformen vor (`category_hint`).

2. ZUSTAND & MÄNGELKONTROLLE:
   - Prüfe alle Bilder genau auf Flecken, Löcher, Risse, Pilling, ausgewaschene Farben, Abnutzungen an Bündchen/Kragen oder defekte Reißverschlüsse.
   - Hängt noch ein Original-Preisetikett/Kaufetikett am Teil, wähle "Neu mit Etikett".
   - Falls Mängel oder Gebrauchsspuren sichtbar sind, erwähne diese transparent und fair in der Beschreibung und passe `condition` entsprechend an.

3. NACKENETIKETT & LOGOS:
   - Lies den Markennamen (`brand`) und die angegebene Größe (`size`) präzise vom Etikett oder Logo ab.
   - Ist keine Marke erkennbar, verwende "No Name".
   - Ordne numerische Größen (z. B. EU 36/38/40/42, Kragenweiten oder Jeansweiten) sinnvoll in XS, S, M, L, XL, XXL oder One Size ein und nenne die exakte Etikett-Angabe zusätzlich in der Beschreibung.

4. WASCHETIKETT / MATERIALETIKETT:
   - Falls ein Waschetikett abfotografiert wurde, lies die genaue Materialzusammensetzung auf Deutsch ab (z. B. "100% Baumwolle" oder "80% Baumwolle, 20% Polyester") und trage sie in `material` ein.
   - Ist kein Waschetikett sichtbar oder unleserlich, setze `material` zwingend auf `null`.

5. MASSBAND / ZOLLSTOCK:
   - Prüfe genau, ob auf Fotos ein Zollstock oder Maßband am Kleidungsstück anliegt.
   - Lies die sichtbaren Maße in Zentimetern sorgfältig ab (z. B. Brustweite von Achsel zu Achsel, Gesamtlänge ab Schulter/Kragen, Ärmellänge, Bundweite) und trage sie in `measurements` ein.
   - WICHTIG: Erfinde niemals Maße! Wenn kein Messwerkzeug im Bild liegt, setze `measurements` zwingend auf `null`.

6. TITEL, BESCHREIBUNG, PREIS & TAGS:
   - `title`: Verkaufsstark, präzise, maximal 60 Zeichen lang, inkl. Marke (falls bekannt), Artikelart, Farbe und Größe.
   - `description`: Freundlicher, authentischer Second-Hand-Stil auf Deutsch mit kurzem Einleitungssatz und übersichtlichen Bulletpoints ("- ") zu Details, Passform, Zustand sowie (falls vorhanden) Material und Maßen. Beende die Beschreibung IMMER exakt mit dem Satz:
     "Aus tierfreiem Nichtraucherhaushalt. Bei Fragen gerne melden!"
   - `suggested_price_eur`: Realistischer Second-Hand-Preis in Euro (ganze Zahl), passend zu Marke, Artikeltyp und Zustand auf Vinted/Kleinanzeigen.
   - `tags`: Exakt 5 passende deutsche/internationale Such-Hashtags (beginnend mit `#`).
"""


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


def _prepare_image_part_for_gemini(img_path: Path) -> types.Part:
    """
    Öffnet ein Bild mit PIL, skaliert es im Arbeitsspeicher auf eine optimale Auflösung
    herunter und verpackt es als kompaktes JPEG-Part für die Gemini API (Originaldatei bleibt unberührt).
    """
    with Image.open(img_path) as raw_img:
        rgb_img = normalize_to_rgb(raw_img)
        rgb_img.thumbnail(
            (AI_MAX_IMAGE_DIMENSION, AI_MAX_IMAGE_DIMENSION),
            Image.Resampling.LANCZOS,
        )
        buffer = io.BytesIO()
        rgb_img.save(buffer, format="JPEG", quality=AI_JPEG_QUALITY, optimize=True)
        return types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg")


def _ensure_api_key() -> str:
    """Prüft, ob GEMINI_API_KEY korrekt konfiguriert ist, und wirft sonst eine klare Fehlermeldung."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "dein_api_key_hier":
        # Falls der Nutzer die .env-Datei erst während der laufenden Session angepasst hat:
        load_dotenv(override=True)
        api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key or api_key == "dein_api_key_hier":
        error_msg = (
            "Kein gültiger GEMINI_API_KEY gefunden! "
            "Bitte erstelle eine Datei '.env' im Projektverzeichnis (Vorlage: '.env.example') "
            "und trage dort deinen echten Google Gemini API-Key ein: GEMINI_API_KEY=..."
        )
        console.print(f"\n[bold red]Konfigurationsfehler:[/bold red] {error_msg}\n")
        raise RuntimeError(error_msg)

    return api_key


def analyze_item_folder(folder_path: Path, force_reanalyze: bool = False) -> ClothingListing:
    """
    Analysiert alle Kleidungsfotos in `folder_path` mit Gemini Vision (`gemini-3.8-flash`).

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

    # 2. Bilder suchen, mit PIL öffnen und für schnellen API-Upload komprimieren
    image_paths = get_image_files(folder_path)
    if not image_paths:
        raise ValueError(
            f"Keine Bilddateien (.jpg, .jpeg, .png, .webp) im Ordner '{folder_path}' gefunden."
        )

    # API-Key vor dem API-Aufruf validieren
    _ensure_api_key()

    image_parts: list[types.Part] = [
        _prepare_image_part_for_gemini(img_path) for img_path in image_paths
    ]

    # 3. & 4. & 5. Gemini Vision API-Call mit Structured Outputs + Retry bei 503/429
    active_model = os.getenv("GEMINI_MODEL", MODEL_NAME)
    client = genai.Client(http_options=types.HttpOptions(timeout=120_000))
    contents: list[object] = [*image_parts, ANALYSIS_PROMPT]

    response = None
    last_error: Exception | None = None

    for attempt in range(1, MAX_API_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=active_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ClothingListing,
                    temperature=0.2,
                ),
            )
            break
        except genai_errors.APIError as err:
            last_error = err
            if err.code in {429, 500, 503, 504} and attempt < MAX_API_RETRIES:
                wait_seconds = 2**attempt
                console.print(
                    f"[yellow]Gemini-Server kurzzeitig ausgelastet (Code {err.code}) – "
                    f"neuer Versuch {attempt + 1}/{MAX_API_RETRIES} in {wait_seconds}s...[/yellow]"
                )
                time.sleep(wait_seconds)
            else:
                raise

    if response is None or not response.text:
        if last_error is not None:
            raise last_error
        raise RuntimeError(
            "Die Gemini API hat eine leere Antwort zurückgegeben. Bitte erneut versuchen."
        )

    # 6. Antwort validieren, Schlusssatz sicherstellen & als JSON speichern
    listing = ClothingListing.model_validate_json(response.text)

    if REQUIRED_CLOSING_LINE not in listing.description:
        cleaned_desc = listing.description.rstrip()
        listing.description = f"{cleaned_desc}\n\n{REQUIRED_CLOSING_LINE}"

    formatted_json = json.dumps(
        listing.model_dump(),
        indent=2,
        ensure_ascii=False,
    )
    cache_path.write_text(formatted_json + "\n", encoding="utf-8")

    return listing
