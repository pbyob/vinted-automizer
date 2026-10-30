# KI Second-Hand Listing-Automator (Kleinanzeigen & Vinted)

Halbautomatisches CLI-Tool mit **Dual-KI-Backend** (**Lokal via Ollama `qwen3-vl:8b`** oder **Google Cloud Gemini `gemini-3.8-flash`**) und **Playwright**, das Fotos von Kleidungsstücken aus lokalen Ordnern analysiert, verkaufsstarke deutsche Inseratsdaten (JSON) generiert und direkt in die Web-Formulare von **Kleinanzeigen.de** und **Vinted.de** einträgt.

---

## 1. Voraussetzungen & KI-Auswahl

Beim Programmstart (und jederzeit im Hauptmenü über `[4]`) kannst du frei wählen zwischen:

### Option A: 100% Lokal über Ollama (`qwen3-vl:8b`)
Kein API-Key nötig, komplett offline, kostenlos und ohne Server-Rate-Limits:
1. [Ollama](https://ollama.com/) installieren und starten.
2. Modell herunterladen:
   ```powershell
   ollama pull qwen3-vl:8b
   ```

### Option B: Google Cloud (`gemini-3.8-flash`)
Trage deinen Google AI Studio API-Key in die Datei `.env` im Hauptverzeichnis ein (Vorlage: `.env.example`):
```env
GEMINI_API_KEY=AIzaSy...
```

*(Optional kannst du in `.env` auch `AI_PROVIDER=ollama`, `OLLAMA_MODEL=qwen3-vl:8b` oder `OLLAMA_BASE_URL=http://localhost:11434` anpassen.)*

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
- Optional: Lege einen **Zollstock oder ein Maßband** an (z. B. Achsel zu Achsel & Gesamtlänge) – die KI liest die Zentimeter automatisch ab!
- Große Smartphone-Fotos (auch 40+ MB PNGs) werden sowohl für die KI-Analyse als auch für den Browser-Upload automatisch komprimiert.

---

## 3. Programm starten & Workflow

Starte das interaktive Hauptmenü im Terminal:

```powershell
.\.venv\Scripts\python.exe main.py
```

### Ablauf im Menü:
- **Modell-Auswahl beim Start:** Wähle `[1]` für **Lokal: Ollama (`qwen3-vl:8b`)** oder `[2]` für **Google Cloud (`gemini-3.8-flash`)**.
- **`[1] Login-Session einrichten`** *(nur beim allerersten Start nötig)*:
  Öffnet ein Chromium-Fenster mit persistentem Profil (`./browser_profile`) auf Kleinanzeigen.de und Vinted.de zum einmaligen Einloggen.
- **`[2] Artikel in 'items/to_sell/' verarbeiten`**:
  Analysiert die Fotos, speichert `listing_data.json` als Cache, zeigt eine Vorschau (inkl. Option `[5]`, denselben Artikel direkt mit dem jeweils anderen KI-Modell gegenzuprüfen) und füllt die Browser-Formulare aus. Das Skript klickt **niemals** automatisch auf „Veröffentlichen“.
- **`[3] Nur KI-Analyse (Trockenlauf)`**:
  Generiert vorab für alle Ordner in `items/to_sell/` die `listing_data.json` ohne Browser.
- **`[4] KI-Modell wechseln`**:
  Wechselt jederzeit im laufenden Betrieb zwischen lokalem Ollama und Google Cloud.

---

## 4. Selektoren aktualisieren mit Playwright Codegen

Falls Kleinanzeigen.de oder Vinted.de künftig HTML-IDs oder Feldnamen ändern sollten, lassen sich neue Selektoren mit dem **Playwright Inspector (`codegen`)** sofort ermitteln:

```powershell
# Selektoren auf Kleinanzeigen.de inspizieren
.\.venv\Scripts\playwright.exe codegen https://www.kleinanzeigen.de/p-anzeige-aufgeben-schritt2.html

# Selektoren auf Vinted.de inspizieren
.\.venv\Scripts\playwright.exe codegen https://www.vinted.de/items/new
```
