# Plán opravy MounThor projektu

## Problém
- `rows.py` používa `Adw.ActionRow`, ale nemá importovaný `Adw`
- Chýba globálne inicializovanie GTK/Adw verzií v balíku

## Riešenie

### 1. Opraviť rows.py
- Pridať `from gi.repository import Adw, Gdk, Gtk` (miesto len `Gdk, Gtk`)

### 2. Opraviť __init__.py
- Pridať `gi.require_version` pre GTK a Adw pred importom
- Správne nastaviť verzie: `Gtk` 4.0 a `Adw` 1

### 3. Skontrolovať všetky súbory
- Overiť, že každý súbor má správne importy
- Uistiť sa, že žiadny súbor nepoužíva `Adw` bez importu

### 4. Otestovať
- Spustiť `python3 -m MounThor` a overiť, že beží bez chýb
