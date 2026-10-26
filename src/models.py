"""Pydantic-Datenmodelle für strukturierte Inseratsdaten aus der Gemini Vision API."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ClothingListing(BaseModel):
    """Strukturiertes Second-Hand-Kleidungsinserat für Kleinanzeigen.de und Vinted.de."""

    title: str = Field(
        description=(
            "Verkaufsstarker, präziser Titel auf Deutsch inklusive Marke (falls nicht 'No Name'), "
            "Kleidungsart, Farbe und Größe. Maximal 60 Zeichen lang, keine Emojis oder Klickbait-Großbuchstaben."
        )
    )
    brand: str = Field(
        description=(
            "Erkannte Marke anhand von Nackenetikett, Waschetikett, Stickerei oder Aufdruck/Logo. "
            "Falls keine Marke eindeutig erkennbar ist, exakt 'No Name' zurückgeben."
        )
    )
    category_hint: str = Field(
        description=(
            "Präziser Vorschlag für den Kategorie-Pfad auf Deutsch für Vinted/Kleinanzeigen "
            "(z. B. 'Herren > Kleidung > Sweatshirts & Hoodies' oder 'Damen > Kleidung > Kleider > Sommerkleider')."
        )
    )
    size: Literal["XS", "S", "M", "L", "XL", "XXL", "One Size"] = Field(
        description=(
            "Erkannte Größe laut Nackenetikett oder Waschetikett. "
            "Falls eine numerische Konfektionsgröße (z. B. EU 38, 40, 50 oder Jeansweite) angegeben ist, "
            "ordne sie passend in XS, S, M, L, XL oder XXL ein; bei Accessoires/Einheitsgröße 'One Size'."
        )
    )
    condition: Literal[
        "Neu mit Etikett",
        "Neu",
        "Sehr gut",
        "Gut",
        "Zufriedenstellend",
    ] = Field(
        description=(
            "Ehrliche Einschätzung des Zustands anhand aller Fotos: "
            "'Neu mit Etikett' (Original-Kaufetikett hängt noch am Artikel), "
            "'Neu' (ungetragen, makellos, ohne Kaufetikett), "
            "'Sehr gut' (kaum getragen, keine Flecken, Löcher oder Pilling), "
            "'Gut' (leichte normale Tragespuren, gepflegt), "
            "'Zufriedenstellend' (deutlich sichtbare Gebrauchsspuren, kleine Mängel oder Verfärbungen)."
        )
    )
    color: str = Field(
        description=(
            "Dominierende Hauptfarbe des Kleidungsstücks auf Deutsch "
            "(z. B. 'Schwarz', 'Grau', 'Dunkelblau', 'Beige', 'Olivgrün', 'Weiß')."
        )
    )
    material: Optional[str] = Field(
        default=None,
        description=(
            "Abgelesene Materialzusammensetzung auf Deutsch (z. B. '80% Baumwolle, 20% Polyester'), "
            "falls ein lesbares Waschetikett/Materialetikett auf den Fotos zu sehen ist. Andernfalls null/None."
        ),
    )
    measurements: Optional[str] = Field(
        default=None,
        description=(
            "Exakt abgelesene Maße in cm (z. B. 'Brustweite (Achsel zu Achsel): ca. 56 cm, Gesamtlänge: ca. 70 cm'), "
            "falls auf einem oder mehreren Fotos ein Zollstock oder Maßband anliegt. "
            "Schätze keine Maße ohne Messwerkzeug im Bild – falls keines sichtbar ist, zwingend null/None."
        ),
    )
    description: str = Field(
        description=(
            "Natürlich klingende, sympathische und verkaufsfördernde Beschreibung auf Deutsch. "
            "Strukturiere den Text übersichtlich mit einem kurzen Einleitungssatz, kompakten Bulletpoints ('- ') "
            "zu Details (Schnitt, Details, Farbe, Größe/Passform, Zustand inkl. eventueller Mängel sowie "
            "Material und Maße falls vorhanden). "
            "Am Ende MUSS immer exakt folgender Schlusssatz stehen: "
            "'Aus tierfreiem Nichtraucherhaushalt. Bei Fragen gerne melden!'"
        )
    )
    suggested_price_eur: int = Field(
        description=(
            "Realistischer Second-Hand-Verkaufspreis in Euro als glatte ganze Zahl (z. B. 12, 25, 45). "
            "Orientiere dich an üblichen Marktpreisen auf Vinted.de und Kleinanzeigen.de für diese Marke, "
            "Kategorie und den erkannten Zustand."
        )
    )
    tags: list[str] = Field(
        description=(
            "Exakt 5 relevante Suchbegriffe als Hashtags in Kleinschreibung für mehr Sichtbarkeit "
            "(z. B. ['#streetwear', '#vintage', '#hoodie', '#oversized', '#herrenmode'])."
        )
    )

