# KI Second-Hand Listing-Automator (Kleinanzeigen & Vinted)

Halbautomatisches CLI-Tool auf Basis von **Google Gemini Vision (`gemini-3.8-flash`)** und **Playwright**, das Fotos von Kleidungsstücken aus lokalen Ordnern analysiert, verkaufsstarke deutsche Inseratsdaten (JSON) generiert und direkt in die Web-Formulare von **Kleinanzeigen.de** und **Vinted.de** einträgt.

---

## 1. Voraussetzungen & Einrichtung

### API-Key eintragen
Öffne die Datei `.env` im Hauptverzeichnis (oder kopiere `.env.example` zu `.env`) und trage deinen Google Gemini API-Key ein:

```env
GEMINI_API_KEY=AIzaSy...
```

*(Einen kostenlosen API-Key erhältst du im [Google AI Studio](https://aistudio.google.com/apikey)).*

### Virtuelle Umgebung & Pakete (falls noch nicht eingerichtet)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

---

## 2. Fotos vorbereiten

Lege für jedes Kleidungsstück einen eigenen Unterordner in `items/to_sell/` an, z. B.:

```text
items/
└── to_sell/
    ├── 01_beispiel_artikel/
    ├── hoodie_grau/
    │   ├── 01_vorne.jpg
    │   ├── 02_hinten.jpg
    │   ├── 03_etikett.jpg
    │   ├── 04_waschetikett.jpg
    │   └── 05_zollstock_brustweite.jpg
    └── jeans_levis/
        ├── 01_front.jpg
        └── 02_label.jpg
```

**Tipps für beste KI-Ergebnisse:**
- Fotografiere **Vorderseite** und **Rückseite** bei gutem Tageslicht.
- Fotografiere das **Nackenetikett** (für Marke & Größe) sowie das **Waschetikett** (für die Materialzusammensetzung).
- Optional: Lege einen **Zollstock oder ein Maßband** an (z. B. Achsel zu Achsel & Gesamtlänge) – Gemini liest die Zentimeter automatisch ab!
- Unterstützte Bildformate: `.jpg`, `.jpeg`, `.png`, `.webp`.

---

## 3. Programm starten & Workflow

Starte das interaktive Hauptmenü im Terminal:

```powershell
.\.venv\Scripts\python.exe main.py
```

### Ablauf im Menü:
1. **`[1] Login-Session einrichten`** *(nur beim allerersten Start nötig)*:
   - Öffnet ein Chromium-Fenster mit persistentem Profil (`./browser_profile`) auf Kleinanzeigen.de und Vinted.de.
   - Akzeptiere die Cookie-Banner und logge dich auf beiden Plattformen ein.
   - Drücke anschließend `[ENTER]` im Terminal – deine Login-Session bleibt für künftige Starts gespeichert.
2. **`[2] Artikel in 'items/to_sell/' verarbeiten`**:
   - Analysiert die Fotos pro Ordner und speichert das Ergebnis lokal als `listing_data.json` (spart API-Calls bei erneutem Öffnen).
   - Zeigt dir eine formatierte Vorschau und lässt dich wählen (`Kleinanzeigen & Vinted`, `Nur Kleinanzeigen`, `Nur Vinted`, `Neu generieren`, `Überspringen`).
   - Lädt im Browser automatisch alle Fotos hoch und füllt Titel, Beschreibung und Preis aus.
   - Im Terminal erhältst du einen kompakten **Spickzettel** für die restlichen Dropdowns (Kategorie, Marke, Größe, Zustand, Farbe).
   - **Sicherheit:** Das Skript klickt **niemals** selbst auf „Veröffentlichen“ – du behältst jederzeit die volle Endkontrolle.
   - Nach dem Einstellen kann der Ordner auf Wunsch direkt nach `items/processed/` verschoben werden.
3. **`[3] Nur KI-Analyse (Trockenlauf)`**:
   - Generiert und prüft vorab für alle Ordner in `items/to_sell/` die `listing_data.json`, ohne den Browser zu öffnen.

---

## 4. Selektoren aktualisieren mit Playwright Codegen

Falls Kleinanzeigen.de oder Vinted.de künftig HTML-IDs oder Feldnamen ändern sollten, ist jedes Formularfeld in `src/uploader.py` einzeln abgesichert, sodass das Programm nicht abstürzt. Neue Selektoren lassen sich mit dem integrierten **Playwright Inspector (`codegen`)** in wenigen Sekunden herausfinden:

```powershell
# Selektoren auf Kleinanzeigen.de inspizieren
.\.venv\Scripts\playwright.exe codegen https://www.kleinanzeigen.de/p-anzeige-aufgeben-schritt2.html

# Selektoren auf Vinted.de inspizieren
.\.venv\Scripts\playwright.exe codegen https://www.vinted.de/items/new
```

Klicke im geöffneten Browserfenster einfach in das gewünschte Eingabefeld – Playwright zeigt dir im Inspector-Fenster sofort den passenden Locator (z. B. `#postad-title` oder `input[name="title"]`), den du direkt in `src/uploader.py` übernehmen kannst.

