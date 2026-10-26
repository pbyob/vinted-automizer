"""Playwright Browser-Automatisierung mit persistentem Profil für Kleinanzeigen.de & Vinted.de."""

from pathlib import Path
from typing import Literal

from PIL import Image
from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from src.analyzer import get_image_files, normalize_to_rgb
from src.models import ClothingListing

console = Console()

BROWSER_PROFILE_DIR = "./browser_profile"
FIELD_TIMEOUT_MS = 5000
MAX_RAW_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB Schwelle für Web-Komprimierung
WEB_UPLOAD_MAX_DIMENSION = 2048
WEB_UPLOAD_JPEG_QUALITY = 88

KLEINANZEIGEN_BASE_URL = "https://www.kleinanzeigen.de"
KLEINANZEIGEN_NEW_AD_URL = "https://www.kleinanzeigen.de/p-anzeige-aufgeben-schritt2.html"

VINTED_BASE_URL = "https://www.vinted.de"
VINTED_NEW_ITEM_URL = "https://www.vinted.de/items/new"


def _launch_persistent_browser(playwright: Playwright) -> BrowserContext:
    """
    Startet Chromium immer mit persistentem Nutzerprofil und Anti-Bot-Konfiguration,
    um Login-Sessions (Cookies/LocalStorage) zu erhalten und Bot-Erkennung zu minimieren.
    """
    Path(BROWSER_PROFILE_DIR).mkdir(parents=True, exist_ok=True)
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=BROWSER_PROFILE_DIR,
        headless=False,
        viewport={"width": 1366, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    return context


def _prepare_upload_image_paths(folder_path: Path) -> list[str]:
    """
    Liefert absolute Bildpfade für den Browser-Upload.
    Sehr große Dateien (> 5 MB, z. B. 40 MB Smartphone-PNGs) werden automatisch als scharfe,
    weboptimierte JPEGs im Unterordner `_web_upload/` abgelegt, damit Kleinanzeigen & Vinted
    den Upload nicht wegen Größenlimits (max. ~10 MB) ablehnen.
    """
    source_images = get_image_files(folder_path)
    upload_paths: list[str] = []
    web_dir: Path | None = None

    for img_path in source_images:
        if img_path.stat().st_size <= MAX_RAW_UPLOAD_BYTES:
            upload_paths.append(str(img_path.resolve()))
            continue

        if web_dir is None:
            web_dir = folder_path / "_web_upload"
            web_dir.mkdir(parents=True, exist_ok=True)

        target_jpg = web_dir / f"{img_path.stem}.jpg"
        if not target_jpg.exists():
            with Image.open(img_path) as raw_img:
                rgb_img = normalize_to_rgb(raw_img)
                rgb_img.thumbnail(
                    (WEB_UPLOAD_MAX_DIMENSION, WEB_UPLOAD_MAX_DIMENSION),
                    Image.Resampling.LANCZOS,
                )
                rgb_img.save(
                    target_jpg,
                    format="JPEG",
                    quality=WEB_UPLOAD_JPEG_QUALITY,
                    optimize=True,
                )
        upload_paths.append(str(target_jpg.resolve()))

    return upload_paths


def _print_copy_paste_helper(data: ClothingListing) -> None:
    """
    Gibt Titel, Beschreibung und Tags ohne seitliche Rahmenzeichen im Terminal aus,
    damit der Nutzer Textblöcke bei Bedarf direkt fehlerfrei per Maus kopieren kann.
    """
    tags_line = " ".join(data.tags)
    console.print(Rule("[bold cyan]Copy & Paste Vorlage (ohne Rahmenzeichen)[/bold cyan]"))
    console.print("[bold yellow]Titel:[/bold yellow]")
    console.print(data.title)
    console.print("\n[bold yellow]Beschreibung:[/bold yellow]")
    console.print(data.description)
    if tags_line:
        console.print(f"\n[bold yellow]Hashtags:[/bold yellow]\n{tags_line}")
    console.print(Rule(style="cyan"))


def setup_login_session() -> None:
    """
    Öffnet das persistente Browser-Profil mit Kleinanzeigen.de und Vinted.de in zwei Tabs,
    damit sich der Nutzer einmalig einloggen und Cookie-Banner bestätigen kann.
    """
    console.print(
        Panel(
            "[bold cyan]Einmalige Login-Einrichtung wird gestartet...[/bold cyan]\n\n"
            "Es öffnet sich gleich ein Chromium-Browserfenster mit zwei Tabs:\n"
            "  1. [bold]Kleinanzeigen.de[/bold]\n"
            "  2. [bold]Vinted.de[/bold]\n\n"
            "[yellow]Bitte führe jetzt im Browser folgende Schritte aus:[/yellow]\n"
            "  • Akzeptiere ggf. die Cookie-Banner auf beiden Seiten.\n"
            "  • Logge dich in deinen Kleinanzeigen-Account ein.\n"
            "  • Logge dich in deinen Vinted-Account ein (inkl. evtl. 2FA-Bestätigung).\n"
            "  • Schließe das Browserfenster noch NICHT selbst, sondern kehre hierher zurück.",
            title="[bold green]Login-Session Setup[/bold green]",
            border_style="green",
        )
    )

    with sync_playwright() as p:
        context = _launch_persistent_browser(p)
        try:
            page_ka = context.pages[0] if context.pages else context.new_page()
            page_vinted = context.new_page()

            try:
                page_ka.goto(KLEINANZEIGEN_BASE_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                console.print(
                    f"[yellow]Hinweis beim Laden von Kleinanzeigen.de:[/yellow] {e}"
                )

            try:
                page_vinted.goto(VINTED_BASE_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                console.print(f"[yellow]Hinweis beim Laden von Vinted.de:[/yellow] {e}")

            page_ka.bring_to_front()

            console.print()
            input(
                "Drücke [ENTER], sobald du auf beiden Seiten eingeloggt bist, um das Profil zu speichern..."
            )
        finally:
            try:
                context.close()
            except Exception:
                pass

    console.print(
        "[bold green]✓ Browser-Profil wurde erfolgreich unter './browser_profile' gespeichert![/bold green]\n"
    )


def create_draft_kleinanzeigen(page: Page, folder_path: Path, data: ClothingListing) -> None:
    """
    Öffnet das Inserats-Formular auf Kleinanzeigen.de, lädt alle Artikelbilder hoch
    und füllt Titel, Beschreibung, Preis und Preis-Typ (VB) fehlertolerant aus.
    """
    console.print("\n[bold cyan]→ Bereite Kleinanzeigen.de Entwurf vor...[/bold cyan]")

    image_paths = _prepare_upload_image_paths(folder_path)

    try:
        page.goto(KLEINANZEIGEN_NEW_AD_URL, wait_until="domcontentloaded", timeout=30000)
        # Kurze Wartezeit auf dynamisch nachgeladene Formular-Elemente
        page.wait_for_timeout(1500)
    except Exception as e:
        console.print(
            f"[yellow]Warnung beim Navigieren zu Kleinanzeigen ({KLEINANZEIGEN_NEW_AD_URL}):[/yellow] {e}"
        )

    # 1. Bilder-Upload
    try:
        if image_paths:
            file_input = page.locator("input[type='file']").first
            file_input.set_input_files(image_paths, timeout=FIELD_TIMEOUT_MS)
            console.print(f"  [green]✓[/green] {len(image_paths)} Bild(er) an Kleinanzeigen übergeben.")
        else:
            console.print("  [yellow]![/yellow] Keine Bilder zum Hochladen gefunden.")
    except Exception as e:
        console.print(f"  [yellow]! Bilder-Upload fehlgeschlagen (bitte manuell ziehen):[/yellow] {e}")

    # 2. Titel (#postad-title)
    try:
        page.locator("#postad-title, input[name='title']").first.fill(
            data.title, timeout=FIELD_TIMEOUT_MS
        )
        console.print("  [green]✓[/green] Titel eingetragen.")
    except Exception as e:
        console.print(f"  [yellow]! Titel konnte nicht automatisch gesetzt werden:[/yellow] {e}")

    # 3. Beschreibung (#pstad-descrptn)
    try:
        page.locator("#pstad-descrptn, textarea[name='description']").first.fill(
            data.description, timeout=FIELD_TIMEOUT_MS
        )
        console.print("  [green]✓[/green] Beschreibung eingetragen.")
    except Exception as e:
        console.print(
            f"  [yellow]! Beschreibung konnte nicht automatisch gesetzt werden:[/yellow] {e}"
        )

    # 4. Preis (#pstad-price + Fallback für Micro-Frontend)
    try:
        price_str = str(data.suggested_price_eur)
        page.locator(
            "#pstad-price, #micro-frontend-price, #postad-price, input[name*='price']"
        ).first.fill(price_str, timeout=FIELD_TIMEOUT_MS)
        console.print(f"  [green]✓[/green] Preis ({price_str} €) eingetragen.")
    except Exception as e:
        console.print(f"  [yellow]! Preis konnte nicht automatisch gesetzt werden:[/yellow] {e}")

    # 5. Preis-Typ optional auf VB (Verhandlungsbasis) setzen
    try:
        try:
            page.locator(
                "#priceType, #micro-frontend-price-type, select[name='priceType']"
            ).first.select_option("NEGOTIABLE", timeout=3000)
            console.print("  [green]✓[/green] Preis-Typ auf 'VB' gesetzt.")
        except Exception:
            page.locator("input[value='NEGOTIABLE']").first.check(timeout=2000)
            console.print("  [green]✓[/green] Preis-Typ auf 'VB' gesetzt.")
    except Exception as e:
        console.print(f"  [dim]Hinweis: Preis-Typ (VB) nicht automatisch gesetzt ({e}).[/dim]")

    # Übersichtlicher Spickzettel für Dropdown-Felder auf Kleinanzeigen
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column("Feld", style="bold cyan", width=20)
    table.add_column("Wert", style="white")
    table.add_row("Kategorie-Hinweis:", data.category_hint)
    table.add_row("Marke:", data.brand)
    table.add_row("Größe:", data.size)
    table.add_row("Zustand:", data.condition)
    table.add_row("Farbe:", data.color)
    table.add_row("Material:", data.material or "– (kein Etikett erkannt)")
    table.add_row("Maße:", data.measurements or "– (kein Maßband im Bild)")
    table.add_row("Empfohlener Preis:", f"{data.suggested_price_eur} € (VB)")

    console.print(
        Panel(
            table,
            title="[bold green]Spickzettel für Kleinanzeigen-Dropdowns[/bold green]",
            subtitle="[dim]Bitte Kategorie, Größe, Zustand & Versand im Browser prüfen[/dim]",
            border_style="green",
        )
    )


def create_draft_vinted(page: Page, folder_path: Path, data: ClothingListing) -> None:
    """
    Öffnet das Inserats-Formular auf Vinted.de, lädt alle Artikelbilder hoch
    und füllt Titel, Beschreibung und Preis fehlertolerant aus.
    """
    console.print("\n[bold magenta]→ Bereite Vinted.de Entwurf vor...[/bold magenta]")

    image_paths = _prepare_upload_image_paths(folder_path)

    try:
        page.goto(VINTED_NEW_ITEM_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
    except Exception as e:
        console.print(
            f"[yellow]Warnung beim Navigieren zu Vinted ({VINTED_NEW_ITEM_URL}):[/yellow] {e}"
        )

    # 1. Bilder-Upload
    try:
        if image_paths:
            page.locator("input[type='file']").first.set_input_files(
                image_paths, timeout=FIELD_TIMEOUT_MS
            )
            console.print(f"  [green]✓[/green] {len(image_paths)} Bild(er) an Vinted übergeben.")
        else:
            console.print("  [yellow]![/yellow] Keine Bilder zum Hochladen gefunden.")
    except Exception as e:
        console.print(f"  [yellow]! Bilder-Upload bei Vinted fehlgeschlagen:[/yellow] {e}")

    # 2. Titel (input[name='title'] oder Fallback über ID/Label/Placeholder)
    try:
        title_locator = (
            page.locator("input[name='title'], input#title")
            .or_(page.get_by_label("Titel", exact=False))
            .or_(page.get_by_placeholder("z. B.", exact=False))
        )
        title_locator.first.fill(data.title, timeout=FIELD_TIMEOUT_MS)
        console.print("  [green]✓[/green] Titel eingetragen.")
    except Exception as e:
        console.print(f"  [yellow]! Vinted-Titel konnte nicht automatisch gesetzt werden:[/yellow] {e}")

    # 3. Beschreibung (textarea[name='description'] oder Fallback über ID/Label/Placeholder)
    try:
        vinted_description = data.description
        if data.tags:
            tags_str = " ".join(data.tags)
            if tags_str not in vinted_description:
                vinted_description = f"{vinted_description}\n\n{tags_str}"

        desc_locator = (
            page.locator("textarea[name='description'], textarea#description")
            .or_(page.get_by_label("Beschreib", exact=False))
            .or_(page.get_by_placeholder("Beschreib", exact=False))
        )
        desc_locator.first.fill(vinted_description, timeout=FIELD_TIMEOUT_MS)
        console.print("  [green]✓[/green] Beschreibung (inkl. Hashtags) eingetragen.")
    except Exception as e:
        console.print(
            f"  [yellow]! Vinted-Beschreibung konnte nicht automatisch gesetzt werden:[/yellow] {e}"
        )

    # 4. Preis (input[name='price'] oder Fallback über ID/Label/Placeholder)
    try:
        price_str = str(data.suggested_price_eur)
        price_locator = (
            page.locator("input[name='price'], input#price")
            .or_(page.get_by_label("Preis", exact=False))
            .or_(page.get_by_placeholder("0,00", exact=False))
        )
        price_locator.first.fill(price_str, timeout=FIELD_TIMEOUT_MS)
        console.print(f"  [green]✓[/green] Preis ({price_str} €) eingetragen.")
    except Exception as e:
        console.print(f"  [yellow]! Vinted-Preis konnte nicht automatisch gesetzt werden:[/yellow] {e}")

    # Übersichtlicher Spickzettel für Vinted-Dropdowns
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column("Feld", style="bold magenta", width=20)
    table.add_column("Wert", style="white")
    table.add_row("Kategorie-Hinweis:", data.category_hint)
    table.add_row("Marke:", data.brand)
    table.add_row("Größe:", data.size)
    table.add_row("Zustand:", data.condition)
    table.add_row("Farbe:", data.color)
    table.add_row("Material:", data.material or "– (kein Etikett erkannt)")
    table.add_row("Maße:", data.measurements or "– (kein Maßband im Bild)")
    table.add_row("Preis:", f"{data.suggested_price_eur} €")
    table.add_row("Hashtags:", " ".join(data.tags))

    console.print(
        Panel(
            table,
            title="[bold magenta]Spickzettel für Vinted-Dropdowns[/bold magenta]",
            subtitle="[dim]Bitte Kategorie, Marke, Größe, Zustand, Farbe & Paketgröße wählen[/dim]",
            border_style="magenta",
        )
    )


def process_upload_for_item(
    folder_path: Path,
    data: ClothingListing,
    target: Literal["kleinanzeigen", "vinted", "both"],
) -> None:
    """
    Öffnet den persistenten Browser-Context, befüllt je nach `target` die Formulare
    auf Kleinanzeigen.de und/oder Vinted.de und wartet auf die manuelle Veröffentlichung.

    WICHTIG: Klickt niemals automatisch auf den finalen 'Veröffentlichen'-Button!
    """
    if target not in {"kleinanzeigen", "vinted", "both"}:
        raise ValueError(f"Ungültiges Upload-Ziel: {target}")

    _print_copy_paste_helper(data)

    with sync_playwright() as p:
        context = _launch_persistent_browser(p)
        try:
            first_page = context.pages[0] if context.pages else context.new_page()

            if target == "kleinanzeigen":
                create_draft_kleinanzeigen(first_page, folder_path, data)
                first_page.bring_to_front()
            elif target == "vinted":
                create_draft_vinted(first_page, folder_path, data)
                first_page.bring_to_front()
            elif target == "both":
                create_draft_kleinanzeigen(first_page, folder_path, data)
                vinted_page = context.new_page()
                create_draft_vinted(vinted_page, folder_path, data)
                first_page.bring_to_front()

            console.print(
                Panel(
                    "[bold yellow]Manuelle Endkontrolle erforderlich![/bold yellow]\n"
                    "Bitte ergänze jetzt im offenen Browserfenster die restlichen Dropdowns "
                    "(Kategorie, Marke, Größe, Zustand, Versand) anhand des Spickzettels oben "
                    "und klicke anschließend selbst auf [bold green]Veröffentlichen / Hochladen[/bold green].",
                    border_style="yellow",
                )
            )
            input(
                "Prüfe die Angaben im Browser und klicke selbst auf Veröffentlichen. "
                "Drücke danach [ENTER] hier im Terminal..."
            )
        finally:
            try:
                context.close()
            except Exception:
                pass
