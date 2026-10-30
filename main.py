"""Interaktives CLI-Hauptprogramm für den Second-Hand Listing-Automator (Kleinanzeigen & Vinted)."""

from datetime import datetime
from pathlib import Path
import shutil
import sys

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from src.analyzer import (
    GEMINI_MODEL_NAME,
    OLLAMA_MODEL_NAME,
    AIProvider,
    analyze_item_folder,
    get_default_provider,
    get_image_files,
    get_provider_display_name,
)
from src.models import ClothingListing
from src.uploader import process_upload_for_item, setup_login_session

# UTF-8 Ausgabe für Windows-Terminals sicherstellen
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console()

ITEMS_TO_SELL_DIR = Path("items/to_sell")
ITEMS_PROCESSED_DIR = Path("items/processed")


def ensure_directories() -> None:
    """Stellt sicher, dass die Arbeitsverzeichnisse existieren."""
    ITEMS_TO_SELL_DIR.mkdir(parents=True, exist_ok=True)
    ITEMS_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def find_item_folders() -> tuple[list[Path], list[Path]]:
    """
    Sucht alle Unterordner in `items/to_sell/`.

    Gibt ein Tupel `(ordner_mit_bildern, leere_ordner)` zurück, alphabetisch sortiert.
    """
    ensure_directories()
    subfolders = sorted(
        [p for p in ITEMS_TO_SELL_DIR.iterdir() if p.is_dir()],
        key=lambda p: p.name.lower(),
    )

    folders_with_images: list[Path] = []
    folders_without_images: list[Path] = []

    for folder in subfolders:
        if get_image_files(folder):
            folders_with_images.append(folder)
        else:
            folders_without_images.append(folder)

    return folders_with_images, folders_without_images


def prompt_ai_provider(current_provider: AIProvider | None = None) -> AIProvider:
    """Fragt den Nutzer interaktiv nach dem gewünschten KI-Modell (Lokal Ollama vs. Google Cloud)."""
    default_provider = current_provider or get_default_provider()
    default_choice = "1" if default_provider == "ollama" else "2"

    console.print("\n[bold white]Welches KI-Modell möchtest du für die Bildanalyse nutzen?[/bold white]")
    console.print(
        f"  [bold green][1][/bold green] Lokal: Ollama ([bold]{OLLAMA_MODEL_NAME}[/bold]) "
        "[dim]– 100% lokal auf deinem PC, kostenlos & ohne Server-Limits[/dim]"
    )
    console.print(
        f"  [bold cyan][2][/bold cyan] Cloud: Google Gemini ([bold]{GEMINI_MODEL_NAME}[/bold]) "
        "[dim]– über Google AI Studio API-Key[/dim]"
    )

    choice = Prompt.ask(
        "\nModell-Auswahl",
        choices=["1", "2"],
        default=default_choice,
    )
    selected: AIProvider = "ollama" if choice == "1" else "gemini"
    badge_color = "green" if selected == "ollama" else "cyan"
    console.print(
        f"[{badge_color}]✓ Aktives KI-Modell:[/{badge_color}] "
        f"[bold]{get_provider_display_name(selected)}[/bold]"
    )
    return selected


def render_listing_preview(
    folder_path: Path,
    listing: ClothingListing,
    provider: AIProvider,
) -> None:
    """Zeigt das generierte Inserat übersichtlich formatiert im Terminal an."""
    image_count = len(get_image_files(folder_path))

    meta_table = Table(show_header=False, box=None, pad_edge=False)
    meta_table.add_column("Attribut", style="bold cyan", width=20)
    meta_table.add_column("Wert", style="white")

    meta_table.add_row("Ordner:", f"[bold]{folder_path.name}[/bold] ({image_count} Foto(s))")
    meta_table.add_row("KI-Backend:", get_provider_display_name(provider))
    meta_table.add_row(
        "Titel:",
        f"[bold yellow]{listing.title}[/bold yellow] ({len(listing.title)}/60 Zeichen)",
    )
    meta_table.add_row("Empfohlener Preis:", f"[bold green]{listing.suggested_price_eur} €[/bold green]")
    meta_table.add_row("Marke:", listing.brand)
    meta_table.add_row("Kategorie-Pfad:", listing.category_hint)
    meta_table.add_row("Größe:", listing.size)
    meta_table.add_row("Zustand:", listing.condition)
    meta_table.add_row("Farbe:", listing.color)
    meta_table.add_row("Material:", listing.material or "[dim]Nicht erkannt / kein Waschetikett[/dim]")
    meta_table.add_row("Maße:", listing.measurements or "[dim]Kein Maßband/Zollstock im Bild[/dim]")
    meta_table.add_row("Tags:", " ".join(listing.tags))

    console.print()
    console.print(
        Panel(
            meta_table,
            title=f"[bold cyan]Inserats-Vorschau: {folder_path.name}[/bold cyan]",
            border_style="cyan",
        )
    )
    console.print(
        Panel(
            listing.description,
            title="[bold white]Generierte Beschreibung[/bold white]",
            border_style="bright_blue",
        )
    )


def move_to_processed(folder_path: Path) -> Path:
    """Verschiebt einen abgeschlossenen Artikel-Ordner sicher nach `items/processed/`."""
    ensure_directories()
    destination = ITEMS_PROCESSED_DIR / folder_path.name

    # Falls ein gleichnamiger Ordner bereits in 'processed' existiert, Zeitstempel anhängen
    if destination.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        destination = ITEMS_PROCESSED_DIR / f"{folder_path.name}_{timestamp}"

    shutil.move(str(folder_path), str(destination))
    return destination


def handle_process_items(active_provider: AIProvider) -> AIProvider:
    """
    Workflow [2]: KI-Analyse + interaktiver Browser-Upload für alle Artikel in `items/to_sell/`.
    Gibt den (ggf. zwischendurch gewechselten) aktiven Provider zurück.
    """
    folders_with_images, empty_folders = find_item_folders()

    if empty_folders:
        empty_names = ", ".join(f.name for f in empty_folders)
        console.print(
            f"[dim]Hinweis: {len(empty_folders)} Unterordner ohne Bilder übersprungen ({empty_names}).[/dim]"
        )

    if not folders_with_images:
        console.print(
            Panel(
                "Keine Artikel-Ordner mit Fotos in [bold cyan]items/to_sell/[/bold cyan] gefunden.\n\n"
                "Lege pro Kleidungsstück einen Unterordner an (z. B. [cyan]items/to_sell/01_beispiel_artikel/[/cyan]) "
                "und speichere dort die Fotos (`.jpg`, `.jpeg`, `.png`, `.webp`) ab.",
                title="[bold yellow]Keine Artikel gefunden[/bold yellow]",
                border_style="yellow",
            )
        )
        return active_provider

    total = len(folders_with_images)
    console.print(
        f"\n[bold green]{total} Artikel-Ordner mit Bildern gefunden.[/bold green] "
        f"Starte Verarbeitung mit [bold]{get_provider_display_name(active_provider)}[/bold]...\n"
    )

    for index, folder_path in enumerate(folders_with_images, start=1):
        console.rule(f"[bold cyan]Artikel {index}/{total}: {folder_path.name}[/bold cyan]")

        force_reanalyze = False
        while True:
            provider_label = get_provider_display_name(active_provider)
            try:
                with console.status(
                    f"[bold cyan]Analysiere Fotos in '{folder_path.name}' über {provider_label}...[/bold cyan]",
                    spinner="dots",
                ):
                    listing = analyze_item_folder(
                        folder_path,
                        force_reanalyze=force_reanalyze,
                        provider=active_provider,
                    )
            except Exception as e:
                console.print(
                    f"[bold red]Fehler bei der Analyse von '{folder_path.name}':[/bold red] {e}"
                )
                other_provider: AIProvider = "gemini" if active_provider == "ollama" else "ollama"
                other_label = get_provider_display_name(other_provider)
                console.print("  [bold cyan][1][/bold cyan] Mit anderem KI-Modell versuchen "
                              f"([bold]{other_label}[/bold])")
                console.print("  [bold cyan][2][/bold cyan] Nächsten Artikel fortsetzen")
                console.print("  [bold cyan][0][/bold cyan] Zurück zum Hauptmenü")
                err_action = Prompt.ask("Auswahl", choices=["1", "2", "0"], default="1")
                if err_action == "1":
                    active_provider = other_provider
                    force_reanalyze = True
                    continue
                if err_action == "2":
                    break
                return active_provider

            render_listing_preview(folder_path, listing, active_provider)

            other_provider = "gemini" if active_provider == "ollama" else "ollama"
            other_label = get_provider_display_name(other_provider)

            console.print("[bold]Aktion für diesen Artikel wählen:[/bold]")
            console.print("  [bold cyan][1][/bold cyan] Auf Kleinanzeigen & Vinted einstellen")
            console.print("  [bold cyan][2][/bold cyan] Nur Kleinanzeigen")
            console.print("  [bold cyan][3][/bold cyan] Nur Vinted")
            console.print(
                f"  [bold cyan][4][/bold cyan] KI-Analyse neu generieren "
                f"[dim](mit {get_provider_display_name(active_provider)})[/dim]"
            )
            console.print(
                f"  [bold cyan][5][/bold cyan] Mit anderem KI-Modell neu analysieren "
                f"[dim](wechseln zu {other_label})[/dim]"
            )
            console.print("  [bold cyan][6][/bold cyan] Artikel überspringen")
            console.print("  [bold cyan][0][/bold cyan] Zurück zum Hauptmenü")

            action = Prompt.ask(
                "\nDeine Auswahl",
                choices=["1", "2", "3", "4", "5", "6", "0"],
                default="1",
            )

            if action == "0":
                console.print("[dim]Kehre zum Hauptmenü zurück...[/dim]\n")
                return active_provider

            if action == "4":
                force_reanalyze = True
                console.print("[yellow]Starte KI-Analyse neu (ignoriere Cache)...[/yellow]")
                continue

            if action == "5":
                active_provider = other_provider
                force_reanalyze = True
                console.print(
                    f"[yellow]Wechsle auf {get_provider_display_name(active_provider)} "
                    "und generiere Inserat neu...[/yellow]"
                )
                continue

            if action == "6":
                console.print(f"[dim]Artikel '{folder_path.name}' übersprungen.[/dim]\n")
                break

            target_map: dict[str, str] = {
                "1": "both",
                "2": "kleinanzeigen",
                "3": "vinted",
            }
            selected_target = target_map[action]

            try:
                process_upload_for_item(folder_path, listing, target=selected_target)  # type: ignore[arg-type]
            except Exception as e:
                console.print(f"[bold red]Fehler beim Browser-Upload:[/bold red] {e}")

            move_answer = Prompt.ask(
                "\nWurde der Artikel erfolgreich eingestellt? "
                "Soll der Ordner nach 'items/processed/' verschoben werden? (j/n)",
                choices=["j", "n", "ja", "nein"],
                default="j",
            )

            if move_answer.lower() in {"j", "ja"}:
                try:
                    dest = move_to_processed(folder_path)
                    console.print(
                        f"[bold green]✓ Ordner verschoben nach:[/bold green] [cyan]{dest}[/cyan]\n"
                    )
                except Exception as e:
                    console.print(
                        f"[bold red]Konnte Ordner nicht verschieben:[/bold red] {e}\n"
                    )
            else:
                console.print(
                    f"[dim]Ordner '{folder_path.name}' verbleibt in 'items/to_sell/'.[/dim]\n"
                )
            break

    return active_provider


def handle_dry_run_analysis(active_provider: AIProvider) -> None:
    """Workflow [3]: Nur KI-Analyse für alle Ordner in `items/to_sell/` (ohne Browser)."""
    folders_with_images, empty_folders = find_item_folders()

    if empty_folders:
        empty_names = ", ".join(f.name for f in empty_folders)
        console.print(
            f"[dim]Hinweis: {len(empty_folders)} Unterordner ohne Bilder übersprungen ({empty_names}).[/dim]"
        )

    if not folders_with_images:
        console.print(
            Panel(
                "Keine Artikel-Ordner mit Fotos in [bold cyan]items/to_sell/[/bold cyan] gefunden.\n"
                "Lege Fotos (`.jpg`, `.jpeg`, `.png`, `.webp`) in Unterordnern unter `items/to_sell/` ab.",
                title="[bold yellow]Keine Artikel für Trockenlauf[/bold yellow]",
                border_style="yellow",
            )
        )
        return

    total = len(folders_with_images)
    success_count = 0
    provider_label = get_provider_display_name(active_provider)

    console.print(
        f"\n[bold cyan]Starte KI-Trockenlauf ({provider_label}) für {total} Artikel-Ordner...[/bold cyan]\n"
    )

    for index, folder_path in enumerate(folders_with_images, start=1):
        console.rule(f"[bold cyan]Trockenlauf {index}/{total}: {folder_path.name}[/bold cyan]")
        try:
            with console.status(
                f"[bold cyan]Analysiere '{folder_path.name}' über {provider_label}...[/bold cyan]",
                spinner="dots",
            ):
                listing = analyze_item_folder(
                    folder_path,
                    force_reanalyze=False,
                    provider=active_provider,
                )
            render_listing_preview(folder_path, listing, active_provider)
            success_count += 1
        except Exception as e:
            console.print(
                f"[bold red]Fehler bei '{folder_path.name}':[/bold red] {e}\n"
            )
            if any(
                keyword in str(e)
                for keyword in ("GEMINI_API_KEY", "Ollama nicht erreichbar", "noch nicht verfügbar")
            ):
                break

    console.print(
        Panel(
            f"[bold green]{success_count} von {total}[/bold green] Artikel erfolgreich analysiert "
            f"und als [cyan]listing_data.json[/cyan] gecacht.",
            title="[bold green]Trockenlauf abgeschlossen[/bold green]",
            border_style="green",
        )
    )


def print_header() -> None:
    """Zeigt den Titel-Banner des Programms an."""
    banner = Text()
    banner.append("KI Second-Hand Listing-Automator\n", style="bold cyan")
    banner.append(
        f"Lokal: Ollama ({OLLAMA_MODEL_NAME})  |  Cloud: Google Gemini ({GEMINI_MODEL_NAME})  |  Playwright",
        style="dim",
    )
    console.print(Panel(banner, border_style="cyan", expand=False))


def main() -> None:
    """Startet die interaktive Hauptschleife im Terminal."""
    ensure_directories()
    console.print()
    print_header()

    # Beim Start direkt fragen, ob lokal (Ollama) oder Cloud (Gemini) genutzt werden soll
    active_provider: AIProvider = prompt_ai_provider()

    while True:
        badge_style = "bold green" if active_provider == "ollama" else "bold cyan"
        console.print(
            f"\n[bold white]Hauptmenü[/bold white] "
            f"[dim](Aktives Modell:[/dim] [{badge_style}]{get_provider_display_name(active_provider)}[/{badge_style}][dim])[/dim]"
        )
        console.print(
            "  [bold cyan][1][/bold cyan] Login-Session einrichten "
            "[dim](Einmalig bei Kleinanzeigen & Vinted einloggen)[/dim]"
        )
        console.print(
            "  [bold cyan][2][/bold cyan] Artikel in 'items/to_sell/' verarbeiten "
            "[dim](KI-Analyse + Browser-Upload)[/dim]"
        )
        console.print(
            "  [bold cyan][3][/bold cyan] Nur KI-Analyse für alle Ordner in 'items/to_sell/' ausführen "
            "[dim](Trockenlauf ohne Browser)[/dim]"
        )
        console.print(
            "  [bold cyan][4][/bold cyan] KI-Modell wechseln "
            "[dim](Lokal Ollama <-> Google Cloud)[/dim]"
        )
        console.print("  [bold cyan][5][/bold cyan] Beenden")

        choice = Prompt.ask(
            "\nBitte Option wählen",
            choices=["1", "2", "3", "4", "5"],
            default="2",
        )

        if choice == "1":
            try:
                setup_login_session()
            except Exception as e:
                console.print(f"[bold red]Fehler beim Einrichten der Login-Session:[/bold red] {e}")
        elif choice == "2":
            active_provider = handle_process_items(active_provider)
        elif choice == "3":
            handle_dry_run_analysis(active_provider)
        elif choice == "4":
            active_provider = prompt_ai_provider(active_provider)
        elif choice == "5":
            console.print("\n[bold cyan]Bis zum nächsten Mal – viel Erfolg beim Verkaufen![/bold cyan]\n")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[yellow]Programm durch Benutzer abgebrochen.[/yellow]")
        sys.exit(0)
