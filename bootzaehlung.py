#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bootzählung Starnberger See und Ammersee – zählt Boote auf Sentinel-2-Satellitenbildern.

Datenquelle: Sentinel-2 Level-2A (ESA Copernicus), frei abrufbar über den
STAC-Katalog "Earth Search" (Element 84 / AWS Open Data). Kein Konto nötig.
Der Seeumriss kommt aus OpenStreetMap (einmal geladen, danach zwischengespeichert).

Wie gezählt wird (kurz):
  1. Für jeden Aufnahmetag die passende Sentinel-2-Kachel suchen.
  2. Wolken, Wolkenschatten und Uferstreifen aus der Seefläche ausblenden – laut
     Wolkenkarte des Satelliten UND aus dem Bild selbst (Schleier, Quellwolkenfelder
     und Wolkenfetzen, die die Wolkenkarte übersieht).
  3. Boote sind kleine Punkte, die im nahen Infrarot UND im sichtbaren Licht heller sind
     als das Wasser drumherum (Top-Hat-Filter, alle vier 10-m-Bänder). Jede solche Spitze
     zählt – auch dicht an dicht in Bojenfeldern.
  4. Nicht gezählt: alte Kielwasserspuren (lange Linien ohne Kopf), Flachwasser/Seekreide
     (heller in Grün als im Infrarot), Pflanzen/Land im Uferpixel. Boot mit Kielwasser und
     Schiffe zählen einmal. Ortsfeste Objekte (Badeinseln, Liegeplätze) werden markiert.
  5. Je Boot: Typ (Fahrgastschiff/Boot), Bewegung (fährt/steht/liegt), Fahrtrichtung aus dem
     Kielwasser, grobe Geschwindigkeit aus dem Versatz zwischen den Farbkanälen (nur helle
     Objekte), Bojenfeld oder einzeln, nächster Schiffsanleger.
  Abgestimmt an echten Sentinel-2-Daten vom 16.07.2026.

Beispiele:
  python bootzaehlung.py                                     # Starnberger See, letzte 30 Tage
  python bootzaehlung.py --see ammersee                      # Ammersee
  python bootzaehlung.py --see ammersee --von 2026-06-01 --bis 2026-08-31 --vorschau
  python bootzaehlung.py --see ammersee --nur-suchen         # nur Aufnahmen auflisten

Ergebnisse (Ordner --ausgabe):
  zusammenfassung.csv   ein Eintrag pro Aufnahmetag (Anzahl, Wolkenanteil, Qualität)
  boote.csv             jedes erkannte Boot mit Position, Größe, Uferabstand
  boote.geojson         dieselben Boote für QGIS / geojson.io / Google Earth
  verlauf.png           Diagramm: Boote pro Aufnahmetag
  vorschau_<datum>.png  Satellitenbild mit markierten Booten (mit --vorschau)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage as ndi

# --------------------------------------------------------------------------- #
# Feste Einstellungen
# --------------------------------------------------------------------------- #

# Seen: Name in OpenStreetMap, großzügiger Suchrahmen (lon_min, lat_min, lon_max, lat_max)
# für die Notlösung ohne Umriss, Rahmen für die OSM-Abfrage (lat_min, lon_min, lat_max, lon_max).
SEEN = {
    "starnberger-see": dict(name="Starnberger See",
                            such_bbox=(11.24, 47.81, 11.39, 48.01),
                            osm_rahmen=(47.78, 11.20, 48.05, 11.45)),
    "ammersee": dict(name="Ammersee",
                     such_bbox=(11.05, 47.89, 11.22, 48.10),
                     osm_rahmen=(47.86, 11.00, 48.12, 11.24)),
}
SEE_SCHLUESSEL = "starnberger-see"
SEE_NAME = SEEN[SEE_SCHLUESSEL]["name"]
SUCH_BBOX = SEEN[SEE_SCHLUESSEL]["such_bbox"]
OSM_RAHMEN = SEEN[SEE_SCHLUESSEL]["osm_rahmen"]
ZIEL_EPSG = 32632          # UTM Zone 32N – die Zone beider Seen
PIXEL_M = 10.0             # Sentinel-2 Bänder 2/3/4/8 haben 10 m Pixel
RAND_M = 300.0             # Rand um den See im Rechengitter
STEG_PUFFER_M = 15.0       # so breit werden Stege/Molen aus OSM ausgeblendet

ANBIETER = {
    "earth-search": {
        "url": "https://earth-search.aws.element84.com/v1",
        "collection": "sentinel-2-c1-l2a",
        "assets": {
            "blue": ("blue", "B02"), "green": ("green", "B03"), "red": ("red", "B04"),
            "nir": ("nir", "B08"), "scl": ("scl", "SCL"),
        },
    },
    "planetary-computer": {
        "url": "https://planetarycomputer.microsoft.com/api/stac/v1",
        "collection": "sentinel-2-l2a",
        "assets": {
            "blue": ("B02", "blue"), "green": ("B03", "green"), "red": ("B04", "red"),
            "nir": ("B08", "nir"), "scl": ("SCL", "scl"),
        },
    },
}

# Öffentliche Overpass-Server (OpenStreetMap-Abfrage), der Reihe nach probiert
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",          # früher overpass.kumi.systems
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",   # VK Maps
)
OVERPASS_TIMEOUT_S = 45     # so lange auf eine Antwort warten, dann nächster Server
OVERPASS_PAUSE_S = 5        # Pause vor dem zweiten Versuch bei Überlastung (429/502/503/504)

# Sentinel-2 Scene Classification (SCL)
SCL_KEINE_DATEN = 0
SCL_STOERUNG = (1, 3, 8, 9, 10, 11)   # defekt, Wolkenschatten, Wolke mittel/hoch, Zirrus, Schnee/Eis
SCL_WASSER = 6

GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff,.TIF,.TIFF",
    GDAL_HTTP_MAX_RETRY="5",
    GDAL_HTTP_RETRY_DELAY="2",
    GDAL_HTTP_MULTIRANGE="YES",
    GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
    VSI_CACHE="TRUE",
    AWS_NO_SIGN_REQUEST="YES",
)

WOCHENTAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

# Diagrammfarben (geprüft auf Farbfehlsichtigkeit)
FARBE_OFFEN = "#2a78d6"
FARBE_UFER = "#eb6834"
FARBE_FLAECHE = "#fcfcfb"
FARBE_TEXT = "#0b0b0b"
FARBE_TEXT2 = "#52514e"


@dataclass
class Parameter:
    """Stellschrauben der Erkennung. Abgestimmt an echten Sentinel-2-Daten vom 16.07.2026."""
    ufer_puffer_m: float = 30.0     # Uferstreifen ohne Zählung (Mischpixel Land/Wasser, heller Strand)
    ufernah_m: float = 150.0        # Boote näher am Ufer gelten als "ufernah" (Bojenfelder, Häfen)
    # --- Boote: kleine helle Punkte im Infrarot UND im sichtbaren Licht (Top-Hat, 4 Bänder) ---
    tophat_px: int = 5              # Strukturen schmaler als 50 m gelten als "klein"
    k_boot: float = 6.0             # Spitze mind. 6 Rauschbreiten über dem Wasser ...
    min_kontrast: float = 0.008     # ... und mind. so hell im Infrarot (Reflexion) ...
    min_kontrast_sichtbar: float = 0.006  # ... und im sichtbaren Licht (Mittel aus Blau/Grün/Rot)
    min_prominenz: float = 0.75     # Spitze ragt mind. 0,75 x Schwelle über ihre dunkelste Umgebung
    min_nir_gruen: float = 0.4      # Boot ist weiß/grau; darunter = Flachwasser, Seekreide, Algen
    max_nir_gruen: float = 2.0      # darüber = Pflanzen/Land im Pixel (Ufer, Schilf, Bäume)
    spur_laenge_px: int = 8         # Linie ab 80 m Länge ...
    spur_laenglich: float = 4.0     # ... und so schmal ...
    kopf_faktor: float = 2.5        # ... ohne Kopf (hellster Punkt < 2,5 x Median) = alte Kielwasserspur
    kielwasser_laenglich: float = 3.0  # langgestreckt mit klar hellstem Punkt = 1 Boot mit Kielwasser
    schiff_kontrast: float = 0.1    # so helle Objekte (Fahrgastschiff) zählen als 1, egal wie viele Spitzen
    flaeche_px: int = 300           # zusammenhängend größer = Fläche (Wolke, Bank), keine Boote
    # --- Attribute ---
    schiff_kern_m: float = 35.0     # Fahrgastschiff: heller Kern mindestens so lang ...
    schiff_min_kontrast: float = 0.1   # ... und so hell (weißer Aufbau füllt ganze Pixel)
    kiel_min_px: int = 4            # Kielwasser: mind. so viele schwache Pixel ...
    kiel_min_abstand_px: float = 1.5   # ... deren Schwerpunkt so weit hinter dem Kopf liegt
    kiel_laenglich: float = 2.5     # ... in einem langgestreckten Objekt ...
    kiel_min_weiss: float = 0.5     # ... und weiß ist (Schaum: NIR >= 0,5 x Grün; Flachwasser ist grün)
    kiel_min_ufer_m: float = 80.0   # näher am Ufer täuschen Uferstreifen ein Kielwasser vor
    tempo_min_kontrast: float = 0.1 # Geschwindigkeit nur für so helle Objekte (sonst zu ungenau)
    tempo_max_fehler_kmh: float = 10.0  # ungenauere Schätzungen werden nicht ausgegeben
    steht_max_kmh: float = 4.0      # gemessen langsamer und kein Kielwasser = steht
    faehrt_min_kmh: float = 3.0     # Geschwindigkeit minus Fehler mindestens so groß = fährt
    bojenfeld_radius_m: float = 100.0   # mind. 4 weitere Boote in diesem Umkreis = Bojenfeld/Hafen
    bojenfeld_min_nachbarn: int = 4
    kiel_frei_m: float = 60.0       # Fahrtrichtung nur, wenn kein anderes Boot so nah ist
    schiff_min_ufer_m: float = 100.0   # Fahrgastschiff: mind. so weit vom Ufer ...
    schiff_anleger_m: float = 150.0    # ... oder so nah an einem Schiffsanleger
    # --- Wolken aus der Klassenkarte (SCL) ---
    min_wolke_px: int = 12          # kleinere "Wolken" (1-2 SCL-Pixel) sind oft weiße Boote
    wolken_puffer_px: int = 10      # Abstand zu Wolkenrändern (100 m, Schleier am Rand)
    # --- Wolken/Dunst, die die Klassenkarte übersieht (aus dem Bild selbst) ---
    hintergrund_px: int = 15        # Fenster für den lokalen Wasserhintergrund (150 m)
    k_saat: float = 6.0
    k_wachs: float = 3.0
    min_sigma: float = 0.002        # Untergrenze fürs Rauschen bei der Wolkensuche
    wolke_min_kontrast: float = 0.02
    dunst_schwelle: float = 0.02    # Wasser im NIR so viel heller als das klarste Viertel = Schleier
    wolke_min_nir_gruen: float = 0.6   # Wolken sind weiß: im Infrarot fast so hell wie in Grün
    unruhe_px: int = 21             # Fenster (210 m) für die Wolkenfeld-Prüfung
    unruhe_anteil: float = 0.1      # mehr Pixel großer weißer Flecken im Fenster = Wolkenfeld
    diffus_min_px: int = 6          # Wolkenfetzen: ab 6 Pixel ...
    diffus_max_kontrast: float = 0.15  # ... und flauer als ein Schiff
    max_px: int = 30                # oder größer als 30 Pixel
    kielwasser_kontrast: float = 0.035 # (langgestreckt mit hellem Kopf ist kein Fetzen)
    uferobjekt_m: float = 200.0     # näher am Ufer: große Flecken sind Flachwasser, Häfen, Bojenfelder
    dunst_puffer_px: int = 5        # Rand um Dunst/Wolkenfelder (50 m)
    rest_puffer_px: int = 15        # Rand um Wolkenfetzen (150 m)
    # --- Bewertung ---
    min_klar: float = 0.3           # weniger klare Seefläche = Tag wird übersprungen
    sigma_unsicher: float = 0.012   # unruhiges Wasser / Glitzern
    hintergrund_unsicher: float = 0.06  # Wasser zu hell im NIR = Dunst / Sonnenglitzern
    wolkenreste_unsicher: int = 10  # so viele ausgeblendete Wolkenfetzen = Tag unsicher
    ortsfest_anteil: float = 0.5    # an >= 50 % der klaren Tage an derselben Stelle = ortsfest
    ortsfest_min_tage: int = 3


@dataclass
class Gitter:
    crs: str
    transform: object      # affine.Affine
    breite: int
    hoehe: int

    def pixel_zu_xy(self, zeile, spalte):
        x, y = self.transform * (spalte + 0.5, zeile + 0.5)
        return x, y


@dataclass
class SzenenErgebnis:
    datum: dt.date
    zeit_utc: str
    satellit: str
    szene: str
    klar: float
    status: str
    hinweis: str = ""
    boote: list = field(default_factory=list)
    sigma: float = float("nan")
    hintergrund: float = float("nan")


# --------------------------------------------------------------------------- #
# Hilfsfunktionen
# --------------------------------------------------------------------------- #

def log(text=""):
    print(text, flush=True)


def pruefe_abhaengigkeiten():
    fehlend = []
    for modul, paket in (("rasterio", "rasterio"), ("pystac_client", "pystac-client"),
                         ("pyproj", "pyproj"), ("shapely", "shapely"),
                         ("matplotlib", "matplotlib")):
        try:
            __import__(modul)
        except ImportError:
            fehlend.append(paket)
    if fehlend:
        sys.exit("Es fehlen Python-Pakete. Installieren mit:\n"
                 f"  pip install {' '.join(fehlend)}")


def de_zahl(wert, stellen=1):
    """Zahl im deutschen Format für Excel (Komma als Dezimaltrenner)."""
    if wert is None or (isinstance(wert, float) and math.isnan(wert)):
        return ""
    if stellen == 0:
        return str(int(round(wert)))
    return f"{wert:.{stellen}f}".replace(".", ",")


def epsg_von_item(item):
    props = item.properties
    if props.get("proj:epsg"):
        return int(props["proj:epsg"])
    code = props.get("proj:code") or ""
    if code.upper().startswith("EPSG:"):
        return int(code.split(":")[1])
    for asset in item.assets.values():
        code = asset.extra_fields.get("proj:code") or ""
        if asset.extra_fields.get("proj:epsg"):
            return int(asset.extra_fields["proj:epsg"])
        if code.upper().startswith("EPSG:"):
            return int(code.split(":")[1])
    return None


# --------------------------------------------------------------------------- #
# Seeumriss (OpenStreetMap)
# --------------------------------------------------------------------------- #

def waehle_see(schluessel):
    """Stellt das Skript auf einen See ein (Name, Suchrahmen, OSM-Rahmen)."""
    global SEE_SCHLUESSEL, SEE_NAME, SUCH_BBOX, OSM_RAHMEN
    cfg = SEEN[schluessel]
    SEE_SCHLUESSEL, SEE_NAME = schluessel, cfg["name"]
    SUCH_BBOX, OSM_RAHMEN = cfg["such_bbox"], cfg["osm_rahmen"]


def kurzname():
    return SEE_SCHLUESSEL.replace("-", "_")


UMRISS_VERSION = 2          # 2 = mit Schiffsanlegern


def overpass_abfrage():
    s, w, n, e = OSM_RAHMEN
    rahmen = f"({s},{w},{n},{e})"
    return f"""[out:json][timeout:90];
(
  relation["natural"="water"]["name"="{SEE_NAME}"]{rahmen};
  way["natural"="water"]["name"="{SEE_NAME}"]{rahmen};
  way["man_made"~"^(pier|breakwater|groyne)$"]{rahmen};
);
out geom;
(
  nwr["amenity"="ferry_terminal"]{rahmen};
  node["public_transport"="stop_position"]["ferry"="yes"]{rahmen};
);
out center;"""


def osm_zu_geometrien(daten):
    """Baut aus der Overpass-Antwort den Seeumriss (mit Inseln als Löchern), die Stege/Molen
    und die Schiffsanleger. Gibt (see, stege, anleger) in WGS84 zurück;
    anleger = Liste von (name, lon, lat)."""
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import polygonize, unary_union

    def linie(punkte):
        koords = [(p["lon"], p["lat"]) for p in punkte if p]
        return LineString(koords) if len(koords) >= 2 else None

    aussen_rel, innen_rel, see_wege, stege, anleger_roh = [], [], [], [], []
    for el in daten.get("elements", []):
        tags = el.get("tags", {})
        if tags.get("amenity") == "ferry_terminal" or tags.get("ferry") == "yes":
            mitte = el.get("center") or ({"lat": el["lat"], "lon": el["lon"]} if "lat" in el else None)
            if mitte:
                name = tags.get("name") or tags.get("name:de") or "Anleger"
                anleger_roh.append((name, float(mitte["lon"]), float(mitte["lat"]),
                                    tags.get("amenity") == "ferry_terminal"))
            continue
        if el["type"] == "relation":
            for m in el.get("members", []):
                if m.get("type") != "way" or not m.get("geometry"):
                    continue
                l = linie(m["geometry"])
                if l is not None:
                    (innen_rel if m.get("role") == "inner" else aussen_rel).append(l)
        elif el["type"] == "way" and el.get("geometry"):
            l = linie(el["geometry"])
            if l is None:
                continue
            if tags.get("man_made") in ("pier", "breakwater", "groyne"):
                if l.is_closed and len(l.coords) >= 4:
                    stege.append(Polygon(l.coords))
                else:
                    stege.append(l)
            elif tags.get("natural") == "water":
                see_wege.append(l)

    def flaeche(linien):
        if not linien:
            return None
        polys = list(polygonize(unary_union(linien)))
        return unary_union(polys) if polys else None

    aussen = flaeche(aussen_rel) if aussen_rel else flaeche(see_wege)
    if aussen is None or aussen.is_empty:
        return None, None, []
    innen = flaeche(innen_rel)
    see = aussen.difference(innen) if innen is not None else aussen
    see = see.buffer(0)
    # nur Stege, die den See berühren
    stege = [s for s in stege if s.intersects(see.buffer(0.001))]
    # Anleger: höchstens ~500 m vom See; doppelte (Terminal + Haltepunkt) zusammenfassen,
    # benannte Terminals bevorzugen
    nah = see.buffer(0.006)
    anleger_roh.sort(key=lambda a: (not a[3], a[0] == "Anleger"))
    anleger = []
    for name, lon, lat, _ in anleger_roh:
        if not nah.contains(Point(lon, lat)):
            continue
        if any(abs(lon - a[1]) < 0.0015 and abs(lat - a[2]) < 0.001 for a in anleger):
            continue
        anleger.append((name, lon, lat))
    return see, (unary_union(stege) if stege else None), anleger


def lade_osm():
    """Fragt OpenStreetMap über die Overpass-Server ab. Bei Überlastung (429/502/503/504)
    ein zweiter Versuch nach kurzer Pause, sonst gleich der nächste Server."""
    import urllib.error
    abfrage = urllib.parse.urlencode({"data": overpass_abfrage()}).encode()
    fehler_liste = []
    for nr, url in enumerate(OVERPASS_URLS):
        server = urllib.parse.urlparse(url).netloc
        if nr:
            log(f"  versuche {server} ...")
        for versuch in (1, 2):
            try:
                anfrage = urllib.request.Request(
                    url, data=abfrage,
                    headers={"User-Agent": "Bootzaehlung-Seen/1.2 (Python-Skript)"})
                with urllib.request.urlopen(anfrage, timeout=OVERPASS_TIMEOUT_S) as antwort:
                    return json.loads(antwort.read().decode("utf-8"))
            except urllib.error.HTTPError as fehler:
                if fehler.code in (429, 502, 503, 504) and versuch == 1:
                    log(f"  {server}: überlastet (HTTP {fehler.code}), neuer Versuch in "
                        f"{OVERPASS_PAUSE_S} s ...")
                    time.sleep(OVERPASS_PAUSE_S)
                    continue
                fehler_liste.append(f"{server}: HTTP {fehler.code}")
            except Exception as fehler:          # Zeitüberschreitung, Netzwerk, kaputte Antwort
                fehler_liste.append(f"{server}: {fehler}")
            log(f"  {fehler_liste[-1]}")
            break
    raise RuntimeError("OpenStreetMap nicht erreichbar (" + "; ".join(fehler_liste) + ")")


def lies_umriss_geojson(pfad):
    """Gibt (see, stege, anleger, meta) zurück; meta = Eigenschaften des Seeumrisses
    (u. a. version, anleger_versuch = Datum des letzten erfolglosen Nachladens)."""
    from shapely.geometry import shape
    from shapely.ops import unary_union
    with open(pfad, encoding="utf-8") as f:
        daten = json.load(f)
    features = daten["features"] if daten.get("type") == "FeatureCollection" else [daten]
    see, stege, anleger, meta = [], [], [], {"version": 1}
    for ft in features:
        props = ft.get("properties") or {}
        geom = shape(ft["geometry"] if "geometry" in ft else ft)
        art = props.get("art", "see")
        if art == "anleger":
            anleger.append((props.get("name", "Anleger"), geom.x, geom.y))
        elif art == "stege":
            stege.append(geom)
        else:
            see.append(geom)
            meta.update(props)
            meta["version"] = int(props.get("version", 1))
    if not see:
        raise ValueError(f"{pfad} enthält keinen Seeumriss")
    return unary_union(see), (unary_union(stege) if stege else None), anleger, meta


def schreibe_umriss_geojson(pfad, see, stege, anleger=(), version=UMRISS_VERSION, **extra):
    from shapely.geometry import mapping
    props = {"art": "see", "name": SEE_NAME, "version": version, **extra}
    features = [{"type": "Feature", "properties": props, "geometry": mapping(see)}]
    if stege is not None and not stege.is_empty:
        features.append({"type": "Feature", "properties": {"art": "stege"},
                         "geometry": mapping(stege)})
    for name, lon, lat in anleger:
        features.append({"type": "Feature", "properties": {"art": "anleger", "name": name},
                         "geometry": {"type": "Point", "coordinates": [lon, lat]}})
    with open(pfad, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False)


def lade_see_umriss(args):
    """Gibt (see, stege, anleger) zurück; see = None, wenn kein Umriss verfügbar ist."""
    if args.see_geojson:
        log(f"Seeumriss aus {args.see_geojson}")
        return lies_umriss_geojson(args.see_geojson)[:3]
    cache = os.path.join(args.ausgabe, f"{kurzname()}_osm.geojson")
    alt = None
    heute = dt.date.today().isoformat()
    if os.path.exists(cache):
        alt = lies_umriss_geojson(cache)
        if alt[3]["version"] >= UMRISS_VERSION:
            log(f"Seeumriss aus Zwischenspeicher {cache} ({len(alt[2])} Schiffsanleger)")
            return alt[:3]
        if alt[3].get("anleger_versuch") == heute:
            log(f"Seeumriss aus Zwischenspeicher {cache} (ohne Schiffsanleger; OpenStreetMap "
                "war heute nicht erreichbar, nächster Versuch morgen)")
            return alt[:3]
        log("Zwischenspeicher ohne Schiffsanleger – lade neu aus OpenStreetMap ...")
    else:
        log("Lade Seeumriss aus OpenStreetMap ...")
    try:
        see, stege, anleger = osm_zu_geometrien(lade_osm())
        if see is None:
            raise RuntimeError("kein Umriss in der Antwort")
    except Exception as fehler:
        log(f"  Achtung: {fehler}")
        if alt is not None:
            log("  Verwende den alten Zwischenspeicher (ohne Schiffsanleger). Neuer Versuch "
                "beim ersten Lauf morgen, oder sofort: Datei löschen und neu starten.")
            schreibe_umriss_geojson(cache, alt[0], alt[1], alt[2], version=alt[3]["version"],
                                    anleger_versuch=heute)
            return alt[:3]
        log("  Weiter ohne Umriss: Die Seefläche wird aus der Wasser-Klasse des "
            "Satellitenbilds bestimmt (ungenauer am Ufer).")
        return None, None, []
    schreibe_umriss_geojson(cache, see, stege, anleger)
    log(f"  gespeichert: {cache} ({len(anleger)} Schiffsanleger"
        + (": " + ", ".join(a[0] for a in anleger[:12]) if anleger else "") + ")")
    return see, stege, anleger


# --------------------------------------------------------------------------- #
# Rechengitter und Masken
# --------------------------------------------------------------------------- #

def erstelle_gitter(see_wgs84):
    from affine import Affine
    from pyproj import Transformer

    t = Transformer.from_crs(4326, ZIEL_EPSG, always_xy=True)
    if see_wgs84 is not None:
        lon0, lat0, lon1, lat1 = see_wgs84.bounds
        rand = RAND_M
    else:
        lon0, lat0, lon1, lat1 = SUCH_BBOX
        rand = 0.0
    xs, ys = t.transform([lon0, lon1, lon0, lon1], [lat0, lat0, lat1, lat1])
    x0 = math.floor((min(xs) - rand) / PIXEL_M) * PIXEL_M
    x1 = math.ceil((max(xs) + rand) / PIXEL_M) * PIXEL_M
    y0 = math.floor((min(ys) - rand) / PIXEL_M) * PIXEL_M
    y1 = math.ceil((max(ys) + rand) / PIXEL_M) * PIXEL_M
    return Gitter(crs=f"EPSG:{ZIEL_EPSG}",
                  transform=Affine(PIXEL_M, 0.0, x0, 0.0, -PIXEL_M, y1),
                  breite=int(round((x1 - x0) / PIXEL_M)),
                  hoehe=int(round((y1 - y0) / PIXEL_M)))


def transformiere(geom, transformer):
    """Geometrie umprojizieren (shapely >= 2.1 und ältere Versionen)."""
    import shapely
    try:
        return shapely.transform(geom, transformer.transform, interleaved=False)
    except TypeError:
        from shapely.ops import transform as shp_transform
        return shp_transform(transformer.transform, geom)


def nach_utm(geom_wgs84):
    from pyproj import Transformer
    return transformiere(geom_wgs84, Transformer.from_crs(4326, ZIEL_EPSG, always_xy=True))


def rasterisiere(geom_utm, gitter):
    from rasterio.features import rasterize
    if geom_utm is None or geom_utm.is_empty:
        return np.zeros((gitter.hoehe, gitter.breite), bool)
    return rasterize([(geom_utm, 1)], out_shape=(gitter.hoehe, gitter.breite),
                     transform=gitter.transform, fill=0, dtype="uint8").astype(bool)


def see_maske_aus_scl(scl_liste, max_loch_px):
    """Notlösung ohne OSM-Umriss: Ein Pixel gehört zum See, wenn es an der Mehrheit
    der wolkenfreien Tage als Wasser klassifiziert ist. Davon die größte
    zusammenhängende Fläche; kleine Löcher (Boote) werden aufgefüllt, Inseln bleiben."""
    wasser_tage = np.zeros(scl_liste[0].shape, np.uint16)
    klar_tage = np.zeros(scl_liste[0].shape, np.uint16)
    for scl in scl_liste:
        klar_tage += ~(np.isin(scl, SCL_STOERUNG) | (scl == SCL_KEINE_DATEN))
        wasser_tage += scl == SCL_WASSER
    wasser = (klar_tage > 0) & (2 * wasser_tage >= np.maximum(klar_tage, 1))
    lab, n = ndi.label(wasser)
    if n == 0:
        return wasser
    groessen = np.bincount(lab.ravel())
    groessen[0] = 0
    see = lab == int(np.argmax(groessen))
    loecher = ndi.binary_fill_holes(see) & ~see
    lab_l, n_l = ndi.label(loecher)
    if n_l:
        g = np.bincount(lab_l.ravel())
        klein = g <= max_loch_px
        klein[0] = False
        see |= klein[lab_l]
    return see


def abstand_zum_ufer(see):
    return ndi.distance_transform_edt(see) * PIXEL_M


def gueltige_flaeche(scl, see, abstand_ufer, p: Parameter):
    """Seepixel, auf denen gezählt werden kann: kein Ufer, keine Wolke, Daten vorhanden.
    Gibt (gueltig, wolke, klar_anteil) zurück."""
    keine_daten = scl == SCL_KEINE_DATEN
    stoerung = np.isin(scl, SCL_STOERUNG)
    lab, n = ndi.label(stoerung)
    wolke = np.zeros_like(stoerung)
    if n:
        groessen = np.bincount(lab.ravel(), minlength=n + 1)
        gross = groessen >= p.min_wolke_px
        gross[0] = False
        wolke = gross[lab]
        if p.wolken_puffer_px > 0 and wolke.any():
            wolke = ndi.binary_dilation(wolke, iterations=p.wolken_puffer_px)
    see_kern = see & (abstand_ufer > p.ufer_puffer_m)
    gueltig = see_kern & ~keine_daten & ~wolke
    n_kern = int(see_kern.sum())
    klar = float(gueltig.sum()) / n_kern if n_kern else 0.0
    return gueltig, wolke, klar


# --------------------------------------------------------------------------- #
# Bootserkennung
# --------------------------------------------------------------------------- #

DECK_REFLEXION = 0.5      # angenommene Helligkeit eines Decks/Rumpfs im nahen Infrarot


def geschaetzte_laenge(helligkeit):
    """Bootslänge aus der Gesamthelligkeit (Summe der Aufhellung über alle Pixel des Boots).
    Helligkeit x Pixelfläche / Deck-Reflexion = sichtbare Deckfläche; Länge bei Länge:Breite 3:1.
    Grob (etwa ±50 %): dunkle Rümpfe wirken kleiner, Segel und Kielwasser größer."""
    flaeche = max(helligkeit, 0.0) * PIXEL_M ** 2 / DECK_REFLEXION
    return math.sqrt(3.0 * flaeche)


def groessenklasse(laenge_m):
    if laenge_m >= 15:
        return "groß"        # z. B. Fahrgastschiff, große Motoryacht
    if laenge_m >= 6:
        return "mittel"      # typische Segel- und Motorboote
    return "klein"           # Jollen, kleine Boote


def _robustes_sigma(werte, untergrenze):
    med = float(np.median(werte))
    return med, max(1.4826 * float(np.median(np.abs(werte - med))), untergrenze)


def _tophat(band, see, n):
    """Weißer Top-Hat: Band minus morphologische Öffnung. Übrig bleiben helle Strukturen,
    die schmaler als n Pixel sind (Boote). Breite Flächen (Bänke, Trübung) und deren Ränder
    verschwinden. Außerhalb des Sees wird mit Wasser aufgefüllt."""
    ref = float(np.nanmedian(band[see])) if see.any() else 0.0
    b = np.nan_to_num(np.where(see, band, ref), nan=ref).astype(np.float32)
    return b - ndi.grey_opening(b, size=(n, n))


def _form(zz, ss):
    """Länge (Pixel) und Längsstreckung eines Objekts aus seinen Pixelkoordinaten."""
    if len(zz) < 2:
        return 1.0, 1.0
    lam = np.linalg.eigvalsh(np.cov(np.vstack([zz, ss]).astype(float), bias=True))
    lang = 12.0 * float(lam[-1]) + 1.0
    return math.sqrt(lang), math.sqrt(lang / (12.0 * max(float(lam[0]), 0.0) + 1.0))


def maskiere_wolken(nir, green, gueltig, abstand_ufer, p: Parameter, info):
    """Blendet Schleier, Wolkenfelder und Wolkenfetzen aus, die die Klassenkarte übersehen hat.
    Gibt die verbleibende auswertbare Fläche zurück."""
    def hintergrund_von(band):
        bezug = float(np.percentile(band[gueltig], 25))
        gefuellt = np.where(gueltig, band, bezug).astype(np.float32)
        return gefuellt, ndi.median_filter(gefuellt, size=p.hintergrund_px, mode="nearest"), bezug

    nir_f, hg_nir, nir_bezug = hintergrund_von(nir)
    green_f, hg_green, gruen_bezug = hintergrund_von(green)
    # Rauschen des Wassers (ungeglättet) – für die Bewertung "unruhiges Wasser / Glitzern"
    info["sigma"] = _robustes_sigma((nir_f - hg_nir)[gueltig], p.min_sigma)[1]
    # Nur Strukturen, die breiter als ~50 m sind (morphologische Öffnung): Wolken und Schleier
    # bleiben, einzelne Boote verschwinden – auch dicht an dicht in einem Bojenfeld.
    diff = ndi.grey_opening(nir_f, size=(p.tophat_px, p.tophat_px)) - hg_nir
    diff_gruen = ndi.grey_opening(green_f, size=(p.tophat_px, p.tophat_px)) - hg_green
    info["hintergrund"] = nir_bezug

    # a) Schleier: Wasser großflächig heller als klares Wasser, und zwar weiß
    #    (Flachwasser mit hellem Grund ist vor allem in Grün heller und bleibt auswertbar).
    hell_nir, hell_gruen = hg_nir - nir_bezug, hg_green - gruen_bezug
    dunst = gueltig & (hell_nir > p.dunst_schwelle) & (hell_nir >= p.wolke_min_nir_gruen * hell_gruen)
    # b) Wolkenfelder: viele breite, WEISSE Flecken auf engem Raum. Flachwasser ist nicht weiß.
    rest = gueltig & ~dunst
    med, sigma = _robustes_sigma(diff[rest] if rest.sum() > 100 else diff[gueltig], p.min_sigma)
    schwelle_wachs = max(med + p.k_wachs * sigma, 0.5 * p.wolke_min_kontrast)
    weiss = diff >= p.wolke_min_nir_gruen * diff_gruen
    erhoeht = gueltig & weiss & (diff > schwelle_wachs)
    lab0, n0 = ndi.label(erhoeht, structure=np.ones((3, 3), bool))
    if n0:
        gross0 = np.bincount(lab0.ravel(), minlength=n0 + 1) >= p.diffus_min_px
        gross0[0] = False
        dichte = ndi.uniform_filter(gross0[lab0].astype(np.float32), size=p.unruhe_px, mode="constant")
        flaeche = ndi.uniform_filter(gueltig.astype(np.float32), size=p.unruhe_px, mode="constant")
        dunst |= gueltig & (dichte > p.unruhe_anteil * np.maximum(flaeche, 0.25))
    if dunst.any():
        gueltig = gueltig & ~ndi.binary_dilation(dunst, iterations=p.dunst_puffer_px)
    if gueltig.sum() < 100:
        return gueltig

    # c) Wolkenfetzen: breite weiße Flecken auf offenem Wasser (samt Umgebung)
    med, sigma = _robustes_sigma(diff[gueltig], p.min_sigma)
    saat = gueltig & (diff > max(med + p.k_saat * sigma, p.wolke_min_kontrast))
    wachs = gueltig & (diff > max(med + p.k_wachs * sigma, 0.5 * p.wolke_min_kontrast))
    if not saat.any():
        return gueltig
    lab, n = ndi.label(wachs, structure=np.ones((3, 3), bool))
    index = np.arange(1, n + 1)
    hat_saat = ndi.maximum(saat, lab, index) > 0
    groessen = np.bincount(lab.ravel(), minlength=n + 1)[1:]
    ausschnitte = ndi.find_objects(lab)
    reste = []
    for i in index[hat_saat]:
        npx = int(groessen[i - 1])
        if npx < p.diffus_min_px:
            continue
        sl = ausschnitte[i - 1]
        m = lab[sl] == i
        werte = diff[sl][m]
        k = int(np.argmax(werte))
        kontrast = float(werte[k])
        zz, ss = np.nonzero(m)
        _, laenglich = _form(zz, ss)
        kielwasser = laenglich >= p.kielwasser_laenglich and kontrast >= p.kielwasser_kontrast
        if not (npx > p.max_px or (kontrast < p.diffus_max_kontrast and not kielwasser)):
            continue
        d_gruen = float(diff_gruen[sl][m][k])
        if d_gruen > 0.01 and kontrast / d_gruen < p.wolke_min_nir_gruen:
            continue                                   # Flachwasser/heller Grund
        if abstand_ufer[sl[0].start + zz[k], sl[1].start + ss[k]] < p.uferobjekt_m:
            continue                                   # Hafen, Bootshäuser, Bojenfeld am Ufer
        reste.append((sl, m))
    info["wolkenreste"] = len(reste)
    if reste:
        rest_maske = np.zeros_like(gueltig)
        for sl, m in reste:
            rest_maske[sl] |= m
        gueltig = gueltig & ~ndi.binary_dilation(rest_maske, iterations=p.rest_puffer_px)
    return gueltig


# Aufnahmezeitpunkte der 10-m-Bänder relativ zu Blau (s), Sentinel-2 (Heiselberg 2019,
# Sensors 19:2873). Je nach Detektor läuft die Reihenfolge vorwärts oder rückwärts, deshalb
# liefert der Versatz nur die Geschwindigkeit, nicht das Vorzeichen der Richtung.
BAND_ZEIT = {"blau": 0.0, "nir": 0.263, "gruen": 0.527, "rot": 1.005}
KOMPASS = ("N", "NO", "O", "SO", "S", "SW", "W", "NW")


def kompass(grad):
    return KOMPASS[int(((grad % 360) + 22.5) // 45) % 8]


def _kielwasser_richtung(th_nir, th_gruen, m, sl, kz, ks, spitze, p: Parameter):
    """Fahrtrichtung eines Bootes mit Kielwasser (Grad, 0 = Nord, im Uhrzeigersinn):
    vom Schwerpunkt der schwachen Spur zum hellen Kopf. None, wenn keine Spur erkennbar."""
    zz, ss = np.nonzero(m)
    zz = zz + sl[0].start
    ss = ss + sl[1].start
    schwach = th_nir[zz, ss] < 0.3 * spitze
    if schwach.sum() < p.kiel_min_px:
        return None
    zw, sw = zz[schwach], ss[schwach]
    if th_nir[zw, sw].clip(0).sum() < p.kiel_min_weiss * th_gruen[zw, sw].clip(0).sum():
        return None                                   # grünlich = Flachwasser/Ufer, kein Schaum
    dz, ds = zz[schwach].mean() - kz, ss[schwach].mean() - ks
    if math.hypot(dz, ds) < p.kiel_min_abstand_px:
        return None
    return (math.degrees(math.atan2(-ds, dz)) + 360.0) % 360.0


def _geschwindigkeit(th_baender, z, s, h=5, kern_anteil=0.2, min_fehler_kmh=5.0):
    """Geschwindigkeit (km/h) und ihr Fehler aus dem Versatz zwischen den Bändern.
    Je Band der Schwerpunkt des Objekts (ohne das schwache Kielwasser unter 20 % der Spitze),
    dann lineare Regression Ort über Zeit. Geprüft an künstlich verschobenen Schiffen
    (Abweichung etwa 2 km/h); echte Schätzungen sind gröber, daher mindestens ±5 km/h."""
    t, zs_, ss_ = [], [], []
    for name, th in th_baender.items():
        w = th[z - h:z + h + 1, s - h:s + h + 1].astype(np.float64)
        if w.shape != (2 * h + 1, 2 * h + 1) or w.max() <= 0:
            return None, None
        w[w < kern_anteil * w.max()] = 0.0
        gz, gs = np.mgrid[0:2 * h + 1, 0:2 * h + 1]
        t.append(BAND_ZEIT[name])
        zs_.append(float((w * gz).sum() / w.sum()))
        ss_.append(float((w * gs).sum() / w.sum()))
    t, zs_, ss_ = np.array(t), np.array(zs_), np.array(ss_)
    vz, z0 = np.polyfit(t, zs_, 1)
    vs, s0 = np.polyfit(t, ss_, 1)
    rest = np.concatenate([zs_ - (z0 + vz * t), ss_ - (s0 + vs * t)])
    sigma_ort = math.sqrt(float((rest ** 2).sum()) / max(2 * (len(t) - 2), 1))
    sigma_v = sigma_ort / math.sqrt(float(((t - t.mean()) ** 2).sum()))
    return (math.hypot(vz, vs) * PIXEL_M * 3.6, max(sigma_v * PIXEL_M * 3.6, min_fehler_kmh))


def erkenne_boote(nir, red, green, blue, gueltig, see, abstand_ufer, p: Parameter):
    """Zählt Boote: kleine Punkte, die im Infrarot UND im sichtbaren Licht heller sind als das
    Wasser drumherum.

    nir, red, green, blue: Reflexion (0..1), NaN = keine Daten
    gueltig: auswertbare Seefläche (Wolken laut Klassenkarte schon entfernt)
    see:     ganze Seefläche (für den Top-Hat)
    Rückgabe: (liste_boote, info); info["gueltig"] = Fläche nach dem Ausblenden weiterer Wolken.
    """
    gueltig = gueltig & np.isfinite(nir) & np.isfinite(red) & np.isfinite(green)
    info = dict(sigma=float("nan"), hintergrund=float("nan"), wolkenreste=0, gueltig=gueltig,
                verworfen_flachwasser=0, verworfen_ufer=0, verworfen_spur=0, verworfen_flaeche=0,
                doppelt=0, schwelle_nir=float("nan"), schwelle_sichtbar=float("nan"))
    if gueltig.sum() < 100:
        info["gueltig"] = np.zeros_like(gueltig)
        return [], info

    # --- 1. Wolken, die die Klassenkarte übersehen hat ---------------------------------
    gueltig = maskiere_wolken(nir, green, gueltig, abstand_ufer, p, info)
    gueltig &= abstand_ufer > p.ufer_puffer_m
    info["gueltig"] = gueltig
    if gueltig.sum() < 100:
        return [], info

    # --- 2. Kleine helle Strukturen in jedem Band (Top-Hat) -----------------------------
    th_nir = _tophat(nir, see, p.tophat_px)
    th_gruen = _tophat(green, see, p.tophat_px)
    th_blau = _tophat(blue, see, p.tophat_px)
    th_rot = _tophat(red, see, p.tophat_px)
    th_sicht = (th_blau + th_gruen + th_rot) / 3
    th_baender = {"blau": th_blau, "nir": th_nir, "gruen": th_gruen, "rot": th_rot}
    # Die Bänder werden kurz nacheinander aufgenommen: ein fahrendes Boot ist im sichtbaren
    # Licht bis zu 1 Pixel versetzt. Deshalb dort das Maximum der 3x3-Umgebung.
    sicht3 = ndi.maximum_filter(th_sicht, 3)
    gruen3 = ndi.maximum_filter(th_gruen, 3)
    _, sig_nir = _robustes_sigma(th_nir[gueltig], 0.0)
    _, sig_sicht = _robustes_sigma(th_sicht[gueltig], 0.0)
    t_nir = max(p.min_kontrast, p.k_boot * sig_nir)
    t_sicht = max(p.min_kontrast_sichtbar, p.k_boot * sig_sicht)
    info.update(schwelle_nir=t_nir, schwelle_sichtbar=t_sicht)

    # --- 3. Spitzen: lokales Maximum, hell genug in beiden, ragt aus der Umgebung heraus ---
    spitze = (gueltig & (th_nir == ndi.maximum_filter(th_nir, 3)) & (th_nir >= t_nir)
              & (sicht3 >= t_sicht)
              & (th_nir - ndi.minimum_filter(th_nir, 5) >= p.min_prominenz * t_nir))
    verhaeltnis = th_nir / np.maximum(gruen3, 1e-4)
    info["verworfen_flachwasser"] = int((spitze & (verhaeltnis < p.min_nir_gruen)).sum())
    info["verworfen_ufer"] = int((spitze & (verhaeltnis > p.max_nir_gruen)).sum())
    spitze &= (verhaeltnis >= p.min_nir_gruen) & (verhaeltnis <= p.max_nir_gruen)
    # gleich helle Nachbarpixel (Plateau) = eine Spitze
    lab_s, n_s = ndi.label(spitze, structure=np.ones((3, 3), bool))
    if n_s == 0:
        return [], info
    punkte = ndi.maximum_position(th_nir, lab_s, np.arange(1, n_s + 1))

    # --- 4. Spitzen zu Objekten zusammenfassen (Boot + Kielwasser, Schiff, Spur) ---------
    t_wachs = 0.5 * t_nir
    wachs = (gueltig & ((th_nir >= t_wachs) | (th_sicht >= t_wachs))) | spitze
    lab, n = ndi.label(wachs, structure=np.ones((3, 3), bool))
    ausschnitte = ndi.find_objects(lab)
    je_objekt = {}
    for z, s_ in punkte:
        je_objekt.setdefault(int(lab[z, s_]), []).append((int(z), int(s_)))

    boote = []
    for i, pkt in je_objekt.items():
        sl = ausschnitte[i - 1]
        m = lab[sl] == i
        zz, ss = np.nonzero(m)
        npx = len(zz)
        if npx >= p.flaeche_px:
            info["verworfen_flaeche"] += len(pkt)
            continue
        laenge, laenglich = _form(zz, ss)
        werte_obj = th_nir[sl][m]
        pkt.sort(key=lambda q: -th_nir[q])
        hellste = float(th_nir[pkt[0]])
        if (laenge >= p.spur_laenge_px and laenglich >= p.spur_laenglich
                and hellste < p.kopf_faktor * float(np.median(werte_obj))):
            info["verworfen_spur"] += len(pkt)           # alte Kielwasserspur, kein Boot
            continue
        kopf_dominant = len(pkt) == 1 or hellste >= 2.0 * float(th_nir[pkt[1]])
        einzeln = (laenglich >= p.kielwasser_laenglich and npx >= 6 and kopf_dominant) \
            or hellste >= p.schiff_kontrast
        if einzeln and len(pkt) > 1:
            info["doppelt"] += len(pkt) - 1
            pkt = pkt[:1]
        for z, s_ in pkt:
            # Position: Schwerpunkt der 3x3-Umgebung
            z0, z1, s0, s1 = max(z - 1, 0), z + 2, max(s_ - 1, 0), s_ + 2
            w = np.clip(th_nir[z0:z1, s0:s1], 0, None)
            gz, gs = np.mgrid[z0:z1, s0:s1]
            zeile = float((w * gz).sum() / max(w.sum(), 1e-9))
            spalte = float((w * gs).sum() / max(w.sum(), 1e-9))
            kontrast = float(th_nir[z, s_])
            richtung = tempo = tempo_fehler = None
            if len(pkt) == 1:                            # ganzes Objekt gehört zu diesem Boot
                im_nir = m & (th_nir[sl] >= t_wachs)
                helligkeit = float(np.clip(th_nir[sl][im_nir], 0, None).sum())
                kern = m & (th_nir[sl] >= 0.5 * kontrast)
                kern_m = _form(*np.nonzero(kern))[0] * PIXEL_M
                if laenglich >= p.kiel_laenglich and npx >= 6 \
                        and abstand_ufer[z, s_] >= p.kiel_min_ufer_m:
                    richtung = _kielwasser_richtung(th_nir, th_gruen, m, sl, z, s_, kontrast, p)
                if kontrast >= p.tempo_min_kontrast:
                    tempo, tempo_fehler = _geschwindigkeit(th_baender, z, s_)
                    if tempo is not None and tempo_fehler > p.tempo_max_fehler_kmh:
                        tempo = tempo_fehler = None
            else:                                        # Boot in einem Bojenfeld: 3x3-Umgebung
                im_nir = (th_nir[z0:z1, s0:s1] >= t_wachs) & (lab[z0:z1, s0:s1] == i)
                helligkeit = float(np.clip(th_nir[z0:z1, s0:s1][im_nir], 0, None).sum())
                kern_m = PIXEL_M
            pixel = max(int(im_nir.sum()), 1)
            laenge_m = geschaetzte_laenge(helligkeit)
            n_hell, r_hell = float(nir[z, s_]), float(red[z, s_])
            boote.append(dict(
                zeile=zeile, spalte=spalte, pixel=pixel, laenge_m=laenge_m,
                groesse=groessenklasse(laenge_m), kontrast=kontrast,
                snr=kontrast / max(t_nir / p.k_boot, 1e-4),
                ndvi=(n_hell - r_hell) / max(abs(n_hell + r_hell), 1e-6),
                abstand_ufer_m=float(abstand_ufer[z, s_]),
                kern_m=kern_m, richtung_grad=richtung,
                geschwindigkeit_kmh=tempo, geschwindigkeit_fehler_kmh=tempo_fehler,
            ))
    ergaenze_attribute(boote, p)
    return boote, info


def ergaenze_attribute(boote, p: Parameter):
    """Typ, Bewegung, Richtung (Himmelsrichtung) und Umgebung (Bojenfeld) je Boot."""
    if not boote:
        return
    pos = np.array([(b["zeile"], b["spalte"]) for b in boote]) * PIXEL_M
    for i, b in enumerate(boote):
        d = np.hypot(*(pos - pos[i]).T)
        nachbarn = int(((d > 0) & (d <= p.bojenfeld_radius_m)).sum())
        b["umgebung"] = "Bojenfeld/Hafen" if nachbarn >= p.bojenfeld_min_nachbarn else "einzeln"
        if b["umgebung"] != "einzeln" or ((d > 0) & (d <= p.kiel_frei_m)).any():
            b["richtung_grad"] = None           # Nachbarboote sind kein Kielwasser
        b["typ"] = ("Fahrgastschiff" if b["kern_m"] >= p.schiff_kern_m
                    and b["kontrast"] >= p.schiff_min_kontrast else "Boot")
        v, f = b["geschwindigkeit_kmh"], b["geschwindigkeit_fehler_kmh"]
        if b["richtung_grad"] is not None or (v is not None and v - f >= p.faehrt_min_kmh):
            b["bewegung"] = "fährt"
        elif v is not None and v <= p.steht_max_kmh:
            b["bewegung"] = "steht"
        elif b["umgebung"] == "Bojenfeld/Hafen":
            b["bewegung"] = "liegt"
        else:
            b["bewegung"] = "unklar"
        b["richtung"] = kompass(b["richtung_grad"]) if b["richtung_grad"] is not None else ""


def ergaenze_anleger(boote, gitter, anleger_utm, p: Parameter):
    """Nächster Schiffsanleger (Name, Abstand in m) je Boot. Ein großes helles Objekt direkt
    am Ufer, aber an keinem Anleger, ist eher eine Sandbank, ein Bootshaus oder ein Steg als
    ein Fahrgastschiff (z. B. die Ammermündung am 16.07.2026)."""
    for b in boote:
        b["anleger"], b["abstand_anleger_m"] = "", None
        if anleger_utm:
            x, y = gitter.pixel_zu_xy(b["zeile"], b["spalte"])
            name, ax, ay = min(anleger_utm, key=lambda a: (a[1] - x) ** 2 + (a[2] - y) ** 2)
            b["anleger"], b["abstand_anleger_m"] = name, math.hypot(ax - x, ay - y)
        if b.get("typ") == "Fahrgastschiff" and b["abstand_ufer_m"] < p.schiff_min_ufer_m:
            am_anleger = b["abstand_anleger_m"] is not None and \
                b["abstand_anleger_m"] <= p.schiff_anleger_m
            if not am_anleger:
                b["typ"] = "großes Uferobjekt"


def bewerte_qualitaet(klar, info, p: Parameter):
    if info["sigma"] > p.sigma_unsicher or info["hintergrund"] > p.hintergrund_unsicher:
        return "unsicher", "unruhiges Wasser, Dunst oder Sonnenglitzern – Zahl kann zu hoch sein"
    if info["wolkenreste"] >= p.wolkenreste_unsicher:
        return "unsicher", (f"{info['wolkenreste']} Wolkenfetzen ausgeblendet – "
                            "es können weitere übrig sein, Zahl kann zu hoch sein")
    if klar < 0.9:
        return "eingeschränkt", f"nur {klar:.0%} der Seefläche auswertbar"
    return "gut", ""


# --------------------------------------------------------------------------- #
# Satellitendaten
# --------------------------------------------------------------------------- #

def oeffne_katalog(anbieter):
    import pystac_client
    cfg = ANBIETER[anbieter]
    modifier = None
    if anbieter == "planetary-computer":
        try:
            import planetary_computer
        except ImportError:
            sys.exit("Für --anbieter planetary-computer bitte installieren: "
                     "pip install planetary-computer")
        modifier = planetary_computer.sign_inplace
    return pystac_client.Client.open(cfg["url"], modifier=modifier)


def suche_szenen(anbieter, bbox, von, bis, max_wolken):
    """Sucht Sentinel-2-Aufnahmen über dem See. Gibt eine Liste von pystac.Items zurück."""
    cfg = ANBIETER[anbieter]
    katalog = oeffne_katalog(anbieter)
    suche = katalog.search(collections=[cfg["collection"]], bbox=list(bbox),
                           datetime=f"{von.isoformat()}T00:00:00Z/{bis.isoformat()}T23:59:59Z",
                           limit=100)
    items = []
    for item in suche.items():
        wolken = item.properties.get("eo:cloud_cover")
        if wolken is not None and wolken > max_wolken:
            continue
        items.append(item)
    return items


def gruppiere_nach_tag(items, see_wgs84):
    """Pro Aufnahmetag die Kandidaten-Kacheln, beste zuerst
    (deckt den See ab, gleiche UTM-Zone, wenig Wolken)."""
    from shapely.geometry import shape
    tage = {}
    for item in items:
        tage.setdefault(item.datetime.date(), []).append(item)

    def abdeckung(item):
        if see_wgs84 is None or not item.geometry:
            return 1.0
        try:
            return shape(item.geometry).intersection(see_wgs84).area / see_wgs84.area
        except Exception:
            return 0.0

    ergebnis = []
    for tag in sorted(tage):
        kandidaten = []
        for item in tage[tag]:
            a = abdeckung(item)
            if a < 0.05:
                continue
            kandidaten.append((-round(a, 2), epsg_von_item(item) != ZIEL_EPSG,
                               item.properties.get("eo:cloud_cover", 100.0), item.id, item))
        if kandidaten:
            kandidaten.sort(key=lambda k: k[:4])
            ergebnis.append((tag, [k[4] for k in kandidaten], [-k[0] for k in kandidaten]))
    return ergebnis


def finde_asset(item, rolle, anbieter):
    for schluessel in ANBIETER[anbieter]["assets"][rolle]:
        if schluessel in item.assets:
            return item.assets[schluessel]
    raise KeyError(f"Band '{rolle}' fehlt in {item.id} (vorhanden: {', '.join(item.assets)})")


def skalierung(item, asset):
    """Umrechnung Rohwert -> Reflexion. Bevorzugt die Angaben aus dem Katalog."""
    for feld_name in ("raster:bands", "bands"):
        baender = asset.extra_fields.get(feld_name)
        if baender:
            b = baender[0]
            scale = b.get("scale", b.get("raster:scale"))
            offset = b.get("offset", b.get("raster:offset", 0.0))
            if scale is not None:
                return float(scale), float(offset or 0.0)
    # Ab ESA-Prozessierung 04.00 (2022) sind die Rohwerte um +1000 verschoben
    props = item.properties
    basis = props.get("s2:processing_baseline") or props.get("processing:version")
    offset = 0.0
    try:
        if basis is not None and float(basis) >= 4.0 \
                and not props.get("earthsearch:boa_offset_applied", False):
            offset = -0.1
    except (TypeError, ValueError):
        pass
    return 1e-4, offset


def lese_raster(href, gitter, versuche=3):
    """Liest einen Ausschnitt eines (Cloud-Optimized) GeoTIFFs direkt ins Rechengitter.
    Nur der benötigte Ausschnitt wird übertragen, nicht die ganze Kachel."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    for versuch in range(1, versuche + 1):
        try:
            with rasterio.Env(**GDAL_ENV):
                with rasterio.open(href) as src:
                    with WarpedVRT(src, crs=gitter.crs, transform=gitter.transform,
                                   width=gitter.breite, height=gitter.hoehe,
                                   resampling=Resampling.nearest, nodata=0) as vrt:
                        return vrt.read(1)
        except rasterio.errors.RasterioIOError as fehler:
            if versuch == versuche:
                raise
            log(f"    Lesefehler ({fehler}), neuer Versuch ...")
            time.sleep(3 * versuch)


def waehle_kachel(kandidaten, gitter, anbieter, see):
    """Nimmt die erste Kachel, die den See ohne Datenlücke abdeckt.
    Gibt (item, scl) zurück, oder (None, None) wenn keine lesbar ist."""
    beste = (None, None, 2.0)
    for kandidat in kandidaten[:3]:
        try:
            scl = lese_raster(finde_asset(kandidat, "scl", anbieter).href, gitter)
        except Exception as fehler:
            log(f"    {kandidat.id}: nicht lesbar ({fehler})")
            continue
        bereich = see if see is not None else np.ones(scl.shape, bool)
        luecke = float(np.mean(scl[bereich] == SCL_KEINE_DATEN)) if bereich.any() else 1.0
        if luecke < beste[2]:
            beste = (kandidat, scl, luecke)
        if luecke < 0.02:
            break
    return beste[0], beste[1]


def lese_reflexion(item, rolle, gitter, anbieter):
    asset = finde_asset(item, rolle, anbieter)
    roh = lese_raster(asset.href, gitter)
    scale, offset = skalierung(item, asset)
    refl = roh.astype(np.float32) * np.float32(scale) + np.float32(offset)
    refl[roh == 0] = np.nan
    return refl


# --------------------------------------------------------------------------- #
# Ausgabe
# --------------------------------------------------------------------------- #

def speichere_vorschau(pfad, rgb, see, gueltig, boote, titel):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bild = np.dstack(rgb).astype(np.float32)
    bild = np.nan_to_num(bild, nan=0.0)
    lo, hi = np.percentile(bild[bild > 0], (1, 99)) if (bild > 0).any() else (0, 1)
    bild = np.clip((bild - lo) / max(hi - lo, 1e-6), 0, 1) ** 0.6

    # auf den See zuschneiden
    zz, ss = np.nonzero(see)
    if len(zz):
        z0, z1 = max(zz.min() - 20, 0), min(zz.max() + 20, see.shape[0])
        s0, s1 = max(ss.min() - 20, 0), min(ss.max() + 20, see.shape[1])
    else:
        z0, z1, s0, s1 = 0, see.shape[0], 0, see.shape[1]
    bild = bild[z0:z1, s0:s1]
    nicht_auswertbar = (see & ~gueltig)[z0:z1, s0:s1]

    hoehe, breite = bild.shape[:2]
    zoll = 12.0
    kopf = 0.07                                  # Platz für den Titel oben
    fig = plt.figure(figsize=(max(zoll * breite / hoehe, 4.0), zoll / (1 - kopf)),
                     dpi=max(100, 2 * hoehe / zoll))
    ax = fig.add_axes((0, 0, 1, 1 - kopf))
    ax.imshow(bild, interpolation="nearest")
    grau = np.zeros((hoehe, breite, 4), np.float32)
    grau[nicht_auswertbar] = (0.55, 0.55, 0.55, 0.45)
    ax.imshow(grau, interpolation="nearest")
    for b in boote:
        farbe = FARBE_UFER if b["abstand_ufer_m"] < b.get("_ufernah_m", 150) else "#ffd84d"
        r = 2.5 + min(b["laenge_m"], 60) / 10          # Kreis in Pixeln, wächst mit der Bootsgröße
        x, y = b["spalte"] - s0, b["zeile"] - z0
        if b.get("typ") == "Fahrgastschiff":
            ax.add_patch(plt.Rectangle((x - 7, y - 7), 14, 14, fill=False, edgecolor="#ffffff",
                                       linewidth=1.2))
        else:
            ax.add_patch(plt.Circle((x, y), r, fill=False, edgecolor=farbe, linewidth=0.6))
        if b.get("richtung_grad") is not None:      # Pfeil in Fahrtrichtung (0° = Norden)
            w = math.radians(b["richtung_grad"])
            ax.annotate("", xy=(x + 25 * math.sin(w), y - 25 * math.cos(w)),
                        xytext=(x + r * math.sin(w), y - r * math.cos(w)),
                        arrowprops=dict(arrowstyle="-|>", color="#ffffff", lw=1.0, mutation_scale=7))
    ax.set_axis_off()
    zeilen = titel.split("\n")
    fig.text(0.03, 1 - kopf * 0.3, zeilen[0], fontsize=11, va="center", color=FARBE_TEXT,
             fontweight="bold")
    fig.text(0.03, 1 - kopf * 0.7, "\n".join(zeilen[1:]), fontsize=8, va="center",
             color=FARBE_TEXT2, linespacing=1.4)
    fig.savefig(pfad, facecolor=FARBE_FLAECHE)
    plt.close(fig)


def speichere_verlauf(pfad, ergebnisse, p: Parameter):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    gute = [e for e in ergebnisse if e.status in ("gut", "eingeschränkt", "unsicher")]
    if not gute:
        return False
    tage = [dt.datetime.combine(e.datum, dt.time(12)) for e in gute]
    offen = np.array([sum(b["abstand_ufer_m"] >= p.ufernah_m for b in e.boote) for e in gute])
    ufer = np.array([sum(b["abstand_ufer_m"] < p.ufernah_m for b in e.boote) for e in gute])

    fig, ax = plt.subplots(figsize=(10, 4.5), dpi=150)
    fig.patch.set_facecolor(FARBE_FLAECHE)
    ax.set_facecolor(FARBE_FLAECHE)
    spanne = max((max(tage) - min(tage)).days, 1)
    breite = max(0.6, min(3.0, spanne / max(len(tage), 1) * 0.6))
    ax.bar(tage, offen, width=breite, color=FARBE_OFFEN, edgecolor=FARBE_FLAECHE,
           linewidth=1.5, label="offener See")
    ax.bar(tage, ufer, width=breite, bottom=offen, color=FARBE_UFER, edgecolor=FARBE_FLAECHE,
           linewidth=1.5, label=f"ufernah (< {p.ufernah_m:.0f} m)")
    for t, o, u, e in zip(tage, offen, ufer, gute):
        beschriftung = (str(o + u) if len(gute) <= 20 else "") + ("*" if e.status != "gut" else "")
        if beschriftung:
            ax.annotate(beschriftung, (t, o + u), xytext=(0, 3), textcoords="offset points",
                        ha="center", va="bottom", color=FARBE_TEXT2, fontsize=8)
    bewoelkt = [dt.datetime.combine(e.datum, dt.time(12)) for e in ergebnisse
                if e.status == "übersprungen"]
    if bewoelkt:
        ax.plot(bewoelkt, [0] * len(bewoelkt), linestyle="none", marker="x", markersize=6,
                color="#9a998f", clip_on=False, zorder=3, label="bewölkt, nicht zählbar")
    fig.suptitle(f"Boote auf dem {SEE_NAME} je Sentinel-2-Aufnahme", x=0.01, ha="left",
                 color=FARBE_TEXT, fontsize=12)
    ax.set_ylabel("Anzahl Boote", color=FARBE_TEXT2)
    ax.grid(axis="y", color="#e4e3df", linewidth=0.8)
    ax.set_axisbelow(True)
    for seite in ("top", "right", "left"):
        ax.spines[seite].set_visible(False)
    ax.spines["bottom"].set_color("#b9b8b1")
    ax.tick_params(colors=FARBE_TEXT2, length=0)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m.%y"))
    fig.autofmt_xdate()
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3,
              labelcolor=FARBE_TEXT2, fontsize=9, handlelength=1.2, borderaxespad=0.2)
    ax.set_xlim(min(tage + bewoelkt) - dt.timedelta(days=breite),
                max(tage + bewoelkt) + dt.timedelta(days=breite))
    ax.set_ylim(0, max(1, int((offen + ufer).max())) * 1.12)
    fig.tight_layout(rect=(0, 0.04, 1, 0.97))
    if any(e.status != "gut" for e in gute):
        fig.text(0.01, 0.01, "* teilweise bewölkt oder unsichere Bedingungen",
                 color=FARBE_TEXT2, fontsize=8)
    fig.savefig(pfad, facecolor=FARBE_FLAECHE)
    plt.close(fig)
    return True


def markiere_ortsfeste(ergebnisse, treffer, beobachtet, p: Parameter):
    for e in ergebnisse:
        for b in e.boote:
            z, s = int(round(b["zeile"])), int(round(b["spalte"]))
            n_beob = int(beobachtet[z, s])
            if n_beob >= p.ortsfest_min_tage:
                anteil = treffer[z, s] / n_beob
                b["wiederholung"] = float(anteil)
                b["ortsfest"] = "ja" if anteil >= p.ortsfest_anteil else "nein"
            else:
                b["wiederholung"] = float("nan")
                b["ortsfest"] = "unbekannt"


def schreibe_ergebnisse(ordner, ergebnisse, gitter, p: Parameter):
    from pyproj import Transformer
    nach_wgs = Transformer.from_crs(ZIEL_EPSG, 4326, always_xy=True)

    pfad_zus = os.path.join(ordner, "zusammenfassung.csv")
    pfad_boote = os.path.join(ordner, "boote.csv")
    pfad_geo = os.path.join(ordner, "boote.geojson")

    features = []
    with open(pfad_zus, "w", newline="", encoding="utf-8-sig") as f_zus, \
            open(pfad_boote, "w", newline="", encoding="utf-8-sig") as f_boote:
        w_zus = csv.writer(f_zus, delimiter=";")
        w_boote = csv.writer(f_boote, delimiter=";")
        w_zus.writerow(["datum", "wochentag", "uhrzeit_utc", "satellit", "szene",
                        "auswertbar_see_prozent", "boote", "offener_see", "ufernah",
                        "gross", "fahrgastschiffe", "fahrend", "ortsfest", "hochgerechnet",
                        "qualitaet", "hinweis"])
        w_boote.writerow(["datum", "wochentag", "uhrzeit_utc", "nr", "breitengrad", "laengengrad",
                          "typ", "bootslaenge_m", "groesse", "bewegung", "richtung", "richtung_grad",
                          "geschwindigkeit_kmh", "geschwindigkeit_fehler_kmh", "umgebung",
                          "naechster_anleger", "abstand_anleger_m", "abstand_ufer_m", "lage",
                          "ortsfest", "wiederholung_prozent", "pixel", "kern_m", "kontrast_nir",
                          "signal_rausch", "szene"])
        for e in ergebnisse:
            wt = WOCHENTAGE[e.datum.weekday()]
            gezaehlt = e.status in ("gut", "eingeschränkt", "unsicher")
            n = len(e.boote)
            offen = sum(b["abstand_ufer_m"] >= p.ufernah_m for b in e.boote)
            gross = sum(b["groesse"] == "groß" for b in e.boote)
            ortsfest = sum(b.get("ortsfest") == "ja" for b in e.boote)
            schiffe = sum(b.get("typ") == "Fahrgastschiff" for b in e.boote)
            fahrend = sum(b.get("bewegung") == "fährt" for b in e.boote)
            hoch = n / e.klar if gezaehlt and e.klar > 0 else None
            w_zus.writerow([
                e.datum.isoformat(), wt, e.zeit_utc, e.satellit, e.szene,
                de_zahl(100 * e.klar, 0),
                n if gezaehlt else "", offen if gezaehlt else "", (n - offen) if gezaehlt else "",
                gross if gezaehlt else "", schiffe if gezaehlt else "",
                fahrend if gezaehlt else "", ortsfest if gezaehlt else "",
                de_zahl(hoch, 0) if hoch is not None else "", e.status, e.hinweis])
            for nr, b in enumerate(e.boote, 1):
                x, y = gitter.pixel_zu_xy(b["zeile"], b["spalte"])
                lon, lat = nach_wgs.transform(x, y)
                lage = "offener See" if b["abstand_ufer_m"] >= p.ufernah_m else "ufernah"
                wdh = b.get("wiederholung", float("nan"))
                w_boote.writerow([
                    e.datum.isoformat(), wt, e.zeit_utc, nr, de_zahl(lat, 6), de_zahl(lon, 6),
                    b["typ"], de_zahl(b["laenge_m"], 0), b["groesse"], b["bewegung"],
                    b["richtung"], de_zahl(b["richtung_grad"], 0),
                    de_zahl(b["geschwindigkeit_kmh"], 0), de_zahl(b["geschwindigkeit_fehler_kmh"], 0),
                    b["umgebung"], b.get("anleger", ""), de_zahl(b.get("abstand_anleger_m"), 0),
                    de_zahl(b["abstand_ufer_m"], 0), lage, b.get("ortsfest", "unbekannt"),
                    de_zahl(100 * wdh, 0) if not math.isnan(wdh) else "",
                    b["pixel"], de_zahl(b["kern_m"], 0),
                    de_zahl(b["kontrast"], 3), de_zahl(b["snr"], 1), e.szene])
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                    "properties": {
                        "datum": e.datum.isoformat(), "wochentag": wt, "uhrzeit_utc": e.zeit_utc,
                        "nr": nr, "typ": b["typ"], "bootslaenge_m": round(b["laenge_m"]),
                        "groesse": b["groesse"], "bewegung": b["bewegung"],
                        "richtung": b["richtung"],
                        "richtung_grad": (round(b["richtung_grad"]) if b["richtung_grad"] is not None
                                          else None),
                        "geschwindigkeit_kmh": (round(b["geschwindigkeit_kmh"])
                                                if b["geschwindigkeit_kmh"] is not None else None),
                        "geschwindigkeit_fehler_kmh": (round(b["geschwindigkeit_fehler_kmh"])
                                                       if b["geschwindigkeit_fehler_kmh"] is not None
                                                       else None),
                        "umgebung": b["umgebung"], "naechster_anleger": b.get("anleger", ""),
                        "abstand_anleger_m": (round(b["abstand_anleger_m"])
                                              if b.get("abstand_anleger_m") is not None else None),
                        "pixel": b["pixel"], "kontrast_nir": round(b["kontrast"], 4),
                        "signal_rausch": round(b["snr"], 1),
                        "abstand_ufer_m": round(b["abstand_ufer_m"]), "lage": lage,
                        "ortsfest": b.get("ortsfest", "unbekannt"), "szene": e.szene,
                        "qualitaet": e.status,
                    },
                })
    with open(pfad_geo, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False)
    return pfad_zus, pfad_boote, pfad_geo


# --------------------------------------------------------------------------- #
# Ablauf
# --------------------------------------------------------------------------- #

def lies_argumente(argv=None):
    heute = dt.date.today()
    ap = argparse.ArgumentParser(
        description="Zählt Boote auf dem Starnberger See oder dem Ammersee in "
                    "Sentinel-2-Satellitenbildern.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--von", type=dt.date.fromisoformat, default=heute - dt.timedelta(days=30),
                    help="erster Tag (JJJJ-MM-TT)")
    ap.add_argument("--bis", type=dt.date.fromisoformat, default=heute,
                    help="letzter Tag (JJJJ-MM-TT)")
    ap.add_argument("--see", choices=sorted(SEEN), default="starnberger-see",
                    help="welcher See ausgewertet wird")
    ap.add_argument("--ausgabe", default=None,
                    help="Ergebnisordner (Standard: ergebnisse_<see>, z. B. ergebnisse_ammersee)")
    ap.add_argument("--max-wolken", type=float, default=70.0,
                    help="Kacheln mit mehr Wolken (%%, ganze Kachel) gar nicht erst laden")
    ap.add_argument("--min-klar", type=float, default=30.0,
                    help="Tage mit weniger auswertbarer Seefläche (%%) überspringen")
    ap.add_argument("--vorschau", action="store_true",
                    help="pro Tag ein Bild mit markierten Booten speichern")
    ap.add_argument("--rohdaten", action="store_true",
                    help="pro Tag die Satellitendaten des Sees als .npz speichern (ca. 10 MB, "
                         "zur Fehlersuche und Abstimmung)")
    ap.add_argument("--nur-suchen", action="store_true",
                    help="nur verfügbare Aufnahmen auflisten, nichts herunterladen")
    ap.add_argument("--max-szenen", type=int, default=0, help="höchstens so viele Tage (0 = alle)")
    ap.add_argument("--anbieter", choices=sorted(ANBIETER), default="earth-search",
                    help="Satellitendaten-Katalog")
    ap.add_argument("--see-geojson", help="eigener Seeumriss als GeoJSON statt OpenStreetMap")
    ap.add_argument("--empfindlichkeit", type=float, default=1.0,
                    help="1 = Standard. Größer (z. B. 1.3) = empfindlicher: mehr kleine Boote, "
                         "aber auch mehr Fehltreffer. Kleiner (z. B. 0.8) = strenger")
    ap.add_argument("--min-kontrast", type=float, default=None,
                    help="Mindestkontrast eines Bootes im Infrarot (Reflexion), Standard 0.008 "
                         "geteilt durch die Empfindlichkeit")
    ap.add_argument("--uferpuffer", type=float, default=30.0,
                    help="Uferstreifen in m, der nicht ausgewertet wird")
    ap.add_argument("--ufernah", type=float, default=150.0,
                    help="Boote näher als so viele m am Ufer gelten als ufernah")
    args = ap.parse_args(argv)
    if args.bis < args.von:
        ap.error("--bis liegt vor --von")
    return args


def main(argv=None):
    args = lies_argumente(argv)
    waehle_see(args.see)
    if not args.ausgabe:
        args.ausgabe = f"ergebnisse_{kurzname()}"
    pruefe_abhaengigkeiten()
    e = max(args.empfindlichkeit, 0.1)
    p = Parameter(ufer_puffer_m=args.uferpuffer, ufernah_m=args.ufernah,
                  min_klar=args.min_klar / 100.0)
    p.k_boot = p.k_boot / e
    p.min_kontrast = args.min_kontrast if args.min_kontrast else p.min_kontrast / e
    p.min_kontrast_sichtbar = p.min_kontrast_sichtbar / e
    os.makedirs(args.ausgabe, exist_ok=True)

    log(f"Bootzählung {SEE_NAME}: {args.von:%d.%m.%Y} bis {args.bis:%d.%m.%Y}")
    see_wgs, stege_wgs, anleger = lade_see_umriss(args)
    gitter = erstelle_gitter(see_wgs)
    feste_maske = see_wgs is not None
    if feste_maske:
        see = rasterisiere(nach_utm(see_wgs), gitter)
        if stege_wgs is not None:
            stege = rasterisiere(nach_utm(stege_wgs).buffer(STEG_PUFFER_M), gitter)
            see &= ~stege
        abstand = abstand_zum_ufer(see)
        log(f"Seefläche: {see.sum() * PIXEL_M ** 2 / 1e6:.1f} km² "
            f"({gitter.breite} × {gitter.hoehe} Pixel à {PIXEL_M:.0f} m)")
        such_bbox = see_wgs.bounds
        if anleger:
            from pyproj import Transformer
            t_utm = Transformer.from_crs(4326, ZIEL_EPSG, always_xy=True)
            anleger = [(name, *t_utm.transform(lon, lat)) for name, lon, lat in anleger]
    else:
        see = abstand = None
        such_bbox = SUCH_BBOX

    log(f"Suche Sentinel-2-Aufnahmen ({args.anbieter}) ...")
    items = suche_szenen(args.anbieter, such_bbox, args.von, args.bis, args.max_wolken)
    tage = gruppiere_nach_tag(items, see_wgs)
    log(f"  {len(items)} Kacheln an {len(tage)} Tagen mit höchstens {args.max_wolken:.0f} % Wolken")
    if args.nur_suchen:
        for tag, kandidaten, abdeckungen in tage:
            it = kandidaten[0]
            log(f"  {tag:%d.%m.%Y} {WOCHENTAGE[tag.weekday()]}  {it.id:<40} "
                f"Wolken Kachel {it.properties.get('eo:cloud_cover', float('nan')):5.1f} %  "
                f"See abgedeckt {abdeckungen[0]:.0%}")
        return 0
    if args.max_szenen:
        tage = tage[:args.max_szenen]

    vorab = {}
    if not feste_maske:
        # Ohne Umriss: erst die Klassenkarten aller Tage lesen, daraus den See bestimmen
        log("Bestimme die Seefläche aus den Wasser-Klassen aller Aufnahmen ...")
        for tag, kandidaten, _ in tage:
            item, scl = waehle_kachel(kandidaten, gitter, args.anbieter, None)
            if item is not None:
                vorab[tag] = (item, scl)
        if not vorab:
            sys.exit("Keine Aufnahme lesbar.")
        see = see_maske_aus_scl([s for _, s in vorab.values()], max_loch_px=2 * p.max_px)
        if see.sum() < 1000:
            sys.exit("Keine Seefläche erkennbar (alles bewölkt?). Bitte --see-geojson angeben "
                     "oder einen anderen Zeitraum wählen.")
        abstand = abstand_zum_ufer(see)
        log(f"  Seefläche: {see.sum() * PIXEL_M ** 2 / 1e6:.1f} km²")

    ergebnisse = []
    treffer = np.zeros((gitter.hoehe, gitter.breite), np.uint16)
    beobachtet = np.zeros_like(treffer)
    try:
        for nr, (tag, kandidaten, _) in enumerate(tage, 1):
            log(f"[{nr}/{len(tage)}] {tag:%d.%m.%Y} {WOCHENTAGE[tag.weekday()]}")
            if feste_maske:
                item, scl = waehle_kachel(kandidaten, gitter, args.anbieter, see)
            else:
                item, scl = vorab.get(tag, (None, None))
            if item is None:
                continue
            zeit = item.datetime.strftime("%H:%M")
            satellit = str(item.properties.get("platform", "")).replace("sentinel-", "S").upper()
            see_t, abstand_t = see, abstand
            gueltig, wolke, klar = gueltige_flaeche(scl, see_t, abstand_t, p)
            erg = SzenenErgebnis(tag, zeit, satellit, item.id, klar, "")
            if klar < p.min_klar:
                erg.status, erg.hinweis = "übersprungen", f"nur {klar:.0%} der Seefläche wolkenfrei"
                log(f"    {erg.hinweis} – übersprungen")
                ergebnisse.append(erg)
                continue

            nir = lese_reflexion(item, "nir", gitter, args.anbieter)
            red = lese_reflexion(item, "red", gitter, args.anbieter)
            green = lese_reflexion(item, "green", gitter, args.anbieter)
            blue = lese_reflexion(item, "blue", gitter, args.anbieter)
            if args.rohdaten:
                pfad_roh = os.path.join(args.ausgabe, f"rohdaten_{tag.isoformat()}.npz")
                np.savez_compressed(
                    pfad_roh, nir=nir.astype(np.float16), red=red.astype(np.float16),
                    green=green.astype(np.float16), blue=blue.astype(np.float16), scl=scl,
                    see=see_t, gueltig_scl=gueltig,
                    transform=np.array(tuple(gitter.transform)[:6]), crs=gitter.crs,
                    szene=item.id, zeit_utc=zeit)
                log(f"    Rohdaten gespeichert: {pfad_roh} "
                    f"({os.path.getsize(pfad_roh) / 1e6:.1f} MB)")
            n_vorher = max(int(gueltig.sum()), 1)
            boote, info = erkenne_boote(nir, red, green, blue, gueltig, see_t, abstand_t, p)
            ergaenze_anleger(boote, gitter, anleger, p)
            gueltig = info["gueltig"]
            klar = klar * float(gueltig.sum()) / n_vorher     # nach Dunst/Wolkenresten
            erg.klar = klar
            erg.sigma, erg.hintergrund = info["sigma"], info["hintergrund"]
            if klar < p.min_klar:
                erg.status = "übersprungen"
                erg.hinweis = (f"nur {klar:.0%} der Seefläche auswertbar "
                               "(Wolkenfelder/Dunst, die die Wolkenkarte nicht kennt)")
                log(f"    {erg.hinweis} – übersprungen")
                ergebnisse.append(erg)
                continue
            erg.boote = boote
            erg.status, erg.hinweis = bewerte_qualitaet(klar, info, p)
            ergebnisse.append(erg)

            gueltig_ok = gueltig
            beobachtet += gueltig_ok
            if boote:
                punkte = np.zeros_like(gueltig_ok)
                for b in boote:
                    punkte[int(round(b["zeile"])), int(round(b["spalte"]))] = True
                treffer += ndi.binary_dilation(punkte, structure=np.ones((3, 3), bool))

            offen = sum(b["abstand_ufer_m"] >= p.ufernah_m for b in boote)
            log(f"    {len(boote)} Boote (offener See {offen}, ufernah {len(boote) - offen}), "
                f"auswertbar {klar:.0%}, Qualität: {erg.status}"
                + (f" – {erg.hinweis}" if erg.hinweis and erg.status != "eingeschränkt" else ""))

            if args.vorschau:
                for b in boote:
                    b["_ufernah_m"] = p.ufernah_m
                titel = (f"{len(boote)} Boote · {tag:%d.%m.%Y}\n"
                         f"{SEE_NAME} · {zeit} UTC · {satellit}\n"
                         f"gelb = offener See · orange = ufernah · □ = Fahrgastschiff\n"
                         f"Pfeil = Fahrtrichtung · grau = nicht auswertbar")
                speichere_vorschau(os.path.join(args.ausgabe, f"vorschau_{tag.isoformat()}.png"),
                                   (red, green, blue), see_t, gueltig, boote, titel)
    except KeyboardInterrupt:
        log("\nAbgebrochen – speichere die bisherigen Ergebnisse ...")

    markiere_ortsfeste(ergebnisse, treffer, beobachtet, p)
    pfade = schreibe_ergebnisse(args.ausgabe, ergebnisse, gitter, p)
    grafik = os.path.join(args.ausgabe, "verlauf.png")
    hat_grafik = speichere_verlauf(grafik, ergebnisse, p)

    gezaehlt = [e for e in ergebnisse if e.status in ("gut", "eingeschränkt", "unsicher")]
    log("")
    log(f"Ausgewertet: {len(gezaehlt)} von {len(ergebnisse)} Tagen")
    if gezaehlt:
        spitze = max(gezaehlt, key=lambda e: len(e.boote))
        log(f"Höchster Wert: {len(spitze.boote)} Boote am {spitze.datum:%d.%m.%Y} "
            f"({WOCHENTAGE[spitze.datum.weekday()]})")
    log("Dateien:")
    for pfad in pfade + ((grafik,) if hat_grafik else ()):
        log(f"  {pfad}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
