# Bootzählung Starnberger See und Ammersee – aus Satellitenbildern

Das Skript zählt die Boote auf dem **Starnberger See** oder dem **Ammersee** in Bildern des europäischen Satelliten **Sentinel-2** (Copernicus). Die Bilder sind frei und ohne Konto abrufbar. Pro Aufnahmetag bekommst du die Anzahl der Boote, ihre Positionen, eine grobe Größe und den Abstand zum Ufer.

- **Datenquelle:** Sentinel-2 Level-2A, 10 m pro Pixel, über den Katalog *Earth Search* (AWS Open Data). Geladen wird nur der Ausschnitt um den See, nicht die ganze Kachel (einige MB pro Tag).
- **Seeumriss:** aus OpenStreetMap, beim ersten Lauf geladen und gespeichert. Stege und Molen werden ausgeblendet, Inseln wie die Roseninsel nicht mitgezählt. Jeder See hat seinen eigenen Zwischenspeicher.
- **Wolken:** werden aus der Wolkenkarte des Satelliten erkannt und ausgeblendet. Weil diese Karte kleine Quellwolken und dünne Schleier oft übersieht, sucht das Skript zusätzlich selbst nach Wolkenfeldern, Schleiern und Wolkenfetzen und blendet sie samt Umgebung aus. Tage mit weniger als 30 % auswertbarer Seefläche werden übersprungen.

## Inhalt dieses Repositorys
| Pfad | Inhalt |
|---|---|
| `bootzaehlung.py` | das Zählskript (Starnberger See, Ammersee) |
| `rohdaten_auswerten.py` | wertet eine mit `--rohdaten` gespeicherte Szene erneut aus, ohne Internet |
| `selbsttest.py` | Selbsttest mit künstlichen Satellitenbildern (Wolken, Flachwasser, Bojenfelder, Schiffe, Server-Ausfälle) |
| `einrichten.cmd`, `einrichten.ps1`, `einrichten.sh` | richten mit uv eine Python-Umgebung ein (Windows bzw. macOS/Linux) |
| `requirements.txt` | benötigte Python-Pakete |
| `beispiele/starnberger_see_2026-07-16/` | Beispielergebnis: Vorschaubild, `boote.csv`, `boote.geojson`, `zusammenfassung.csv` (298 Boote) |
| `daten/` | Rohdaten vom 16.07.2026 (Starnberger See), an denen die Erkennung abgestimmt ist |
| `doku/kalibrierung.md` | wie die Erkennung an echten Pixeln abgestimmt wurde, mit Bildern |
| `doku/verlauf.md` | was die echten Läufe gezeigt haben und was daraufhin geändert wurde |

## Bedienung
```bash
python bootzaehlung.py                                                   # Starnberger See, letzte 30 Tage
python bootzaehlung.py --see ammersee                                    # Ammersee, letzte 30 Tage
python bootzaehlung.py --see ammersee --von 2026-06-01 --bis 2026-08-31  # ganzer Sommer
python bootzaehlung.py --see ammersee --von 2026-07-16 --bis 2026-07-16 --vorschau
python bootzaehlung.py --see ammersee --nur-suchen                       # nur Aufnahmen auflisten
```

Gespeicherte Rohdaten erneut auswerten, z. B. mit anderer Empfindlichkeit, ohne Internet:
```bash
python rohdaten_auswerten.py daten/rohdaten_starnberger_see_2026-07-16.npz --empfindlichkeit 1.3
```

Ohne `--see` wird der Starnberger See ausgewertet. Die Ergebnisse landen je See in einem eigenen Ordner: `ergebnisse_starnberger_see` bzw. `ergebnisse_ammersee`. Die beiden Seen kommen sich also nicht in die Quere.

> **Umstieg von der Vorgängerversion:** Das Skript hieß früher `bootzaehlung_starnberger_see.py` und schrieb nach `ergebnisse_bootzaehlung`. Die alte Datei kannst du löschen. Der alte Ergebnisordner bleibt unangetastet, der neue Ordner holt sich den Seeumriss beim ersten Lauf noch einmal aus OpenStreetMap.

| Option | Wirkung |
|---|---|
| `--see ammersee` | welcher See: `starnberger-see` (Standard) oder `ammersee` |
| `--von`, `--bis` | Zeitraum (JJJJ-MM-TT), Standard: die letzten 30 Tage |
| `--vorschau` | pro Tag ein Bild mit markierten Booten (gelb = offener See, orange = ufernah, grau = Wolke/nicht auswertbar) |
| `--nur-suchen` | listet die verfügbaren Aufnahmen mit Wolkenanteil, lädt nichts |
| `--ausgabe ORDNER` | Ergebnisordner, Standard `ergebnisse_starnberger_see` bzw. `ergebnisse_ammersee` |
| `--max-wolken 70` | Kacheln mit mehr Wolken (in %, ganze Kachel) gar nicht erst laden |
| `--min-klar 30` | Tage mit weniger auswertbarer Seefläche (in %) überspringen |
| `--empfindlichkeit 1` | 1 = Standard. Größer (z. B. `1.3`) = empfindlicher: mehr kleine Boote, aber auch mehr Fehltreffer. Kleiner (z. B. `0.8`) = strenger |
| `--min-kontrast 0.008` | Mindesthelligkeit eines Bootes über dem Wasser im Infrarot (überschreibt die Empfindlichkeit) |
| `--uferpuffer 30` | Uferstreifen in m, der nicht ausgewertet wird |
| `--ufernah 150` | Boote näher als so viele m am Ufer gelten als „ufernah“ (Bojenfelder, Häfen) |
| `--see-geojson DATEI` | eigener Seeumriss statt OpenStreetMap |
| `--anbieter planetary-computer` | anderer Katalog (Microsoft), braucht zusätzlich `pip install planetary-computer` |
| `--max-szenen N` | höchstens N Tage auswerten, z. B. zum Ausprobieren |
| `--rohdaten` | pro Tag die Satellitendaten des Sees als `rohdaten_<datum>.npz` speichern (etwa 10 MB). Zur Fehlersuche und zum Abstimmen der Schwellen |

Mit **Strg+C** kannst du jederzeit abbrechen, die bis dahin ausgewerteten Tage werden gespeichert.

## Ergebnisse
| Datei | Inhalt |
|---|---|
| `zusammenfassung.csv` | ein Eintrag pro Aufnahmetag: Wochentag, Uhrzeit (UTC), auswertbarer Anteil des Sees, Boote gesamt, davon offener See / ufernah / groß / Fahrgastschiffe / fahrend / ortsfest, hochgerechnet, Qualität |
| `boote.csv` | jedes Boot: Position, Typ, geschätzte Länge, Größe (klein unter 6 m, mittel 6–15 m, groß ab 15 m), Bewegung, Fahrtrichtung, Geschwindigkeit, Umgebung, nächster Anleger, Uferabstand, ortsfest. Siehe *Attribute* |
| `boote.geojson` | dieselben Boote zum Öffnen in QGIS, geojson.io oder Google Earth |
| `verlauf.png` | Balkendiagramm: Boote je Aufnahmetag, bewölkte Tage als × |
| `vorschau_<datum>.png` | Satellitenbild mit markierten Booten (nur mit `--vorschau`): Kreis = Boot, weißes Quadrat = Fahrgastschiff, Pfeil = Fahrtrichtung, grau = ausgeblendet (Wolken, Schleier, Wolkenfetzen). Die Legende nennt die Anzahl je Symbol. „Fahrgastschiff (keins erkannt)“ heißt: An diesem Tag wurde kein Fahrgastschiff auf offenem Wasser gefunden, deshalb gibt es kein Quadrat |
| `starnberger_see_osm.geojson` / `ammersee_osm.geojson` | zwischengespeicherter Seeumriss mit Stegen und Schiffsanlegern. Löschen, um ihn neu zu laden. Ein älterer Zwischenspeicher ohne Anleger wird automatisch einmal neu geladen |

Die CSV-Dateien sind für ein deutsches Excel eingestellt (Semikolon, Dezimalkomma) und öffnen sich per Doppelklick richtig.

**Qualität** in der Zusammenfassung:
- `gut`: mindestens 90 % des Sees auswertbar, ruhiges Wasser
- `eingeschränkt`: Teile des Sees bewölkt oder ausgeblendet. Die Spalte *hochgerechnet* schätzt die Zahl für den ganzen See (setzt gleichmäßige Verteilung voraus, nur grob)
- `unsicher`: unruhiges Wasser, Dunst, Sonnenglitzern oder viele Wolkenfetzen (ab 10). Die Zahl kann zu hoch sein, im Vorschaubild prüfen
- `übersprungen`: zu bewölkt (laut Wolkenkarte oder nach dem Ausblenden von Schleiern und Wolkenfeldern)

**Ortsfest** heißt: An derselben Stelle wurde an mindestens der Hälfte der klaren Tage etwas gefunden (ab 3 Tagen im Zeitraum). Das sind Badeinseln, Markierungsbojen, Steganlagen oder Boote, die die ganze Zeit am Liegeplatz liegen. Je länger der Zeitraum, desto zuverlässiger.

## Wie genau ist das?
Ein Pixel ist 10 × 10 m groß. Ein Boot ist kleiner, fällt aber trotzdem auf: Wasser ist dunkel, ein weißes Deck hellt sein Pixel deutlich auf. Das Skript zählt nur Punkte, die **im Infrarot und im sichtbaren Licht** zugleich heller sind als das Wasser drumherum. Zufälliges Rauschen tut das nicht.

**Abgestimmt an echten Daten vom Starnberger See (16.07.2026, wolkenlos):** Alle Treffer wurden mit dem Satellitenbild verglichen.
- **Offener See:** Alle 13 sichtbaren Boote mehr als 500 m vom Ufer werden gefunden. Alte Kielwasserspuren werden nicht gezählt.
- **Bojenfelder:** Im dichtesten Feld (vor dem Westufer) zählt das Skript 34 Boote bei etwa 35 sichtbaren Punkten. Die alte Version fand dort 11.
- **Ganzer See:** 298 Boote, davon 232 näher als 150 m am Ufer. Die alte Version kam auf 133, darunter etwa 60 Fehltreffer am Uferstreifen.

Was das für die Zahlen heißt:
- **Gut erkannt:** Boote mit hellem Deck ab etwa 4–5 m, auch dicht an dicht in Bojenfeldern (ab etwa 20 m Abstand zueinander), Boote mit Kielwasser (zählen als ein Boot), Fahrgastschiffe.
- **Oft verpasst:** dunkle Rümpfe, SUPs, Kajaks, Ruderboote, Boote dichter als 20 m nebeneinander. Die Zahl ist deshalb eine **Mindestzahl**.
- **Länge:** geschätzt aus der Gesamthelligkeit (angenommen: helles Deck, Länge zu Breite 3 : 1). Grob, etwa ±50 %.
- **Nicht gezählt:** der 30-m-Uferstreifen, Stegbereiche, alles unter Wolken und 150 m um Wolkenfetzen auf offenem Wasser.
- **Fehltreffer:** vereinzelt an den Rändern heller Flachwasserbänke und Uferstreifen (in der Stichprobe vom 16.07. etwa jeder sechste der Treffer über hellem Flachwasser, auf dunklem Wasser fast keine).
- **Wolken:** Quellwolken über dem See sind die häufigste Fehlerquelle. Das Skript erkennt Wolkenfelder, Schleier und Wolkenfetzen (alles, was breiter als etwa 50 m ist). Kleine Fetzen können trotzdem durchrutschen. An Tagen mit Quellwolken die Vorschau ansehen.
- **Zeitpunkt:** Der Satellit kommt nur alle paar Tage vorbei, immer um den späten Vormittag. Die Uhrzeit (UTC) steht in der Tabelle. Viele Tage fallen wegen Wolken weg, besonders im Winter.
- **Unsichere Tage:** Sonnenglitzern und Schaumkronen bei Wind werden als `unsicher` markiert.

**Bisherige echte Läufe (Juli/August 2026) und was daraus wurde:**
1. Quellwolken wurden massenhaft als Boote gezählt (389 am 21.07.). Behoben: Tage voller Quellwolken werden übersprungen.
2. Helle Flachwasserbänke wurden als Wolkenfetzen gewertet und samt Bojenfeldern ausgeblendet (72 am wolkenlosen 16.07.). Behoben.
3. Kleine Boote und Bojenfelder wurden größtenteils übersehen, gleichzeitig zählten helle Uferstreifen als Boote (133 Treffer am 16.07.). Behoben durch einen neuen Detektor, abgestimmt an den echten Pixelwerten vom 16.07. (`--rohdaten`).

Noch nicht geprüft: Tage mit Quellwolken und Wind mit dem neuen Detektor. Vergleich dafür ein paar Vorschaubilder mit dem echten Satellitenbild im [Copernicus Browser](https://browser.dataspace.copernicus.eu). Fehlen viele Boote, `--empfindlichkeit 1.3` probieren. Gibt es Kreise auf leerem Wasser, `--empfindlichkeit 0.8`.

Für kleine Boote wären Bilder mit unter 1 m Auflösung nötig. Die gibt es nur von kommerziellen Satelliten und kosten Geld.

## Attribute
Zu jedem Boot schreibt das Skript in `boote.csv` und `boote.geojson`:

| Spalte | Bedeutung | Wie zuverlässig |
|---|---|---|
| `typ` | `Fahrgastschiff`, `großes Uferobjekt` oder `Boot`. Fahrgastschiff = heller Kern von mindestens 35 m Länge, der ganze Pixel ausfüllt, und zwar auf offenem Wasser (ab 100 m vom Ufer) oder an einem Schiffsanleger (bis 150 m). Dasselbe direkt am Ufer ohne Anleger = `großes Uferobjekt` (Sandbank, Bootshaus, Steg) | Private Boote auf den Seen sind fast alle deutlich kürzer. Am Ammersee (16.07.2026) wurde die helle Bank an der Ammermündung zuerst fälschlich als Schiff erkannt, das ist behoben. An einem echten Fahrgastschiff noch nicht nachgeprüft |
| `bewegung` | `fährt` (Kielwasser oder messbare Geschwindigkeit), `steht` (helles Objekt ohne messbare Bewegung), `liegt` (im Bojenfeld/Hafen), `unklar` | „fährt“ geprüft am 16.07.2026: alle 6 Treffer echte Boote mit Kielwasser |
| `richtung`, `richtung_grad` | Fahrtrichtung (N, NO, …; 0° = Norden, im Uhrzeigersinn), aus der Lage des Kielwassers hinter dem Boot | Nur für Boote mit weißem Kielwasser, mindestens 80 m vom Ufer, nicht im Bojenfeld und ohne anderes Boot im Umkreis von 60 m (sonst täuschen Nachbarboote ein Kielwasser vor). Bei den geprüften Booten stimmte sie |
| `geschwindigkeit_kmh`, `..._fehler_kmh` | aus dem Versatz zwischen den Farbkanälen (Sentinel-2 nimmt Blau, Infrarot, Grün und Rot innerhalb von 1 s nacheinander auf) | **Grob**, Fehler mindestens ±5 km/h. Nur für helle Objekte (Kontrast ab 0,1), also große Boote und Schiffe |
| `umgebung` | `Bojenfeld/Hafen` (mindestens 4 weitere Boote im Umkreis von 100 m) oder `einzeln` | einfach und robust |
| `naechster_anleger`, `abstand_anleger_m` | nächster Schiffsanleger laut OpenStreetMap (`ferry_terminal`) | so gut wie die OSM-Daten |
| `ortsfest` | an mindestens der Hälfte der klaren Tage an derselben Stelle (ab 3 Tagen) | Badeinseln, Stege, Dauerlieger |

Was **nicht** geht: Segel- und Motorboote unterscheiden (Segel stehen senkrecht und sind von oben kaum zu sehen), Bootsnamen lesen, kleine Boote vermessen (unter 10 m ist die Länge nur aus der Helligkeit geschätzt).

**Fahrgastschiffe am Ammersee:** MS Augsburg, Raddampfer Diessen, Raddampfer Herrsching (54 m) und MS Utting (50 m). Welches Schiff ein Treffer ist, lässt sich aus dem Bild allein nicht sagen. Mit dem Einsatzplan der Bayerischen Seenschifffahrt (welche Schiffe an welchem Tag fahren) und der Aufnahmezeit (Tabelle, etwa 12:20 Uhr Sommerzeit) lässt es sich aber oft eingrenzen. Fahrgastschiffe am Anleger liegen meist im 30-m-Uferstreifen oder am Steg und werden dort nicht gezählt.

## Ammersee
Der Ammersee funktioniert genauso. Ein paar Besonderheiten:
- **Abgestimmt ist die Erkennung am Starnberger See** (16.07.2026). Am Ammersee wurde sie an echten Bildern noch nicht geprüft. Für einen klaren Sommertag einmal mit `--vorschau --rohdaten` laufen lassen und die Kreise mit dem Satellitenbild vergleichen.
- **Trübes Wasser im Süden:** Die Ammer bringt Schwebstoffe in den See, das Wasser vor ihrer Mündung bei Dießen/Fischen ist oft heller und grünlicher. Das Skript vergleicht jedes Boot mit seiner direkten Umgebung und verwirft grünliche Aufhellungen. Trotzdem ist das die Stelle, auf die man in der Vorschau zuerst schauen sollte.
- **Ortsfeste Objekte:** die Bootshäuser in Stegen, Stege und Badeinseln. Bei Läufen über mehrere Wochen werden sie als *ortsfest* markiert.
- **Fahrgastschiffe:** Die Schiffe der Bayerischen Seenschifffahrt (bis 54 m) erscheinen als `Fahrgastschiff`, siehe *Attribute*.

## Installation
Repository klonen (`git clone https://github.com/lindenthal100-svg/Boote_Ammer_und_Starnbergersee`) oder auf GitHub unter *Code → Download ZIP* herunterladen und entpacken. Dann:

Am einfachsten mit den Einrichtungs-Skripten. Sie verwenden **uv**, legen im Ordner eine eigene Python-Umgebung `.venv` an und installieren alle Pakete. Python muss dafür nicht installiert sein, uv holt Python 3.12 selbst. Fehlt uv, fragt das Skript, ob es uv installieren soll (offizieller Installer, ohne Administratorrechte).

**Windows:** `einrichten.cmd` doppelklicken. Oder in PowerShell:
```powershell
.\einrichten.cmd -Test
.\.venv\Scripts\Activate.ps1
python bootzaehlung.py --see ammersee --nur-suchen
```

**macOS / Linux:**
```bash
./einrichten.sh --test
source .venv/bin/activate
python bootzaehlung.py --see ammersee --nur-suchen
```

| Option (Windows / macOS, Linux) | Wirkung |
|---|---|
| `-Test` / `--test` | nach dem Einrichten den Selbsttest laufen lassen |
| `-Neu` / `--neu` | vorhandene `.venv` löschen und neu aufbauen |
| `-Python 3.13` / `--python 3.13` | andere Python-Version (Standard 3.12) |
| `-Ja` / `--ja` | uv ohne Rückfrage installieren, falls es fehlt |

Ein zweiter Aufruf verwendet die vorhandene `.venv` weiter und bringt nur die Pakete auf Stand.

**Ohne uv** geht es auch klassisch mit Python 3.9 oder neuer: `pip install -r requirements.txt`.

Ein Internetzugang ist nötig (Pakete, Satellitenkatalog und OpenStreetMap), ein Konto nicht.

**Selbsttest ohne Internet:** `python selbsttest.py` erzeugt künstliche Satellitenbilder mit bekannter Bootsanzahl (Rauschen wie in echten Daten gemessen; inklusive Wolke, Quellwolkenfeld, das die Wolkenkarte nur teilweise kennt, Wolkenschleier, Flachwasser mit hellem Grund, Flachwasserbank mit Booten an Bojen, Boot mit Kielwasser, Insel, Schilf, Steg, Badeinsel und einer Kachel aus der Nachbar-UTM-Zone) und prüft, ob richtig gezählt wird.

## Hinweise
- Der erste Lauf über einen ganzen Sommer dauert je nach Leitung einige Minuten, pro Tag werden 5 Bänder geladen (Blau, Grün, Rot, nahes Infrarot, Klassenkarte).
- **OpenStreetMap überlastet** (z. B. „HTTP Error 502: Bad Gateway“): Das kommt bei den öffentlichen Servern öfter vor. Das Skript versucht es ein zweites Mal und weicht dann auf zwei andere Server aus (private.coffee, VK Maps), jeweils höchstens 45 s. Gibt es schon einen Zwischenspeicher, rechnet es damit weiter (dann ohne Schiffsanleger) und versucht das Nachladen erst am nächsten Tag wieder. Sofort neu versuchen: die Datei `<see>_osm.geojson` im Ergebnisordner löschen.
- Gibt es noch gar keinen Zwischenspeicher und OpenStreetMap ist nicht erreichbar, bestimmt das Skript den See aus der Wasser-Klasse der Satellitenbilder. Das ist am Ufer ungenauer, Stege werden dann nicht ausgeblendet (aber als *ortsfest* markiert).
- Sentinel-2-Daten: © Copernicus / ESA, frei nutzbar mit Quellenangabe. Seeumriss: © OpenStreetMap-Mitwirkende (ODbL).

## Technik
- Python, ein Skript: `bootzaehlung.py`. Neue Seen: ein Eintrag in `SEEN` (Name wie in OpenStreetMap, Suchrahmen, OSM-Rahmen); die Erkennung ist für alle Seen gleich. Einrichtung: `einrichten.ps1` / `einrichten.cmd` (Windows), `einrichten.sh` (macOS/Linux), alle mit `uv venv` und `uv pip install -r requirements.txt`
- Suche: STAC-API Earth Search, Sammlung `sentinel-2-c1-l2a`, pro Tag die Kachel, die den See ganz abdeckt (bevorzugt UTM-Zone 32)
- Lesen: Cloud-Optimized GeoTIFFs über HTTP, direkt in ein festes 10-m-Gitter in UTM 32N umprojiziert (`rasterio` / GDAL)
- Bänder: B02/B03/B04 (sichtbar) und B08 (nahes Infrarot), alle 10 m, dazu SCL (Klassenkarte, 20 m) für Wolken und Schatten
- Wolken aus der SCL: Flecken ab 12 Pixeln (3 SCL-Pixel) plus 100 m Rand. Kleinere bleiben erhalten, weil weiße Boote dort oft als Wolke eingestuft werden.
- Wolken aus dem Bild, nur auf Strukturen breiter als 50 m (morphologische Öffnung, 5 × 5 Pixel), damit Bojenfelder nicht als Wolken gelten: Schleier = NIR mehr als 0,02 über dem klarsten Viertel des Wassers und weiß. Wolkenfeld = mehr als 10 % der Fläche in 210 × 210 m gehören zu breiten weißen Flecken. Wolkenfetzen = breite weiße Flecken auf offenem Wasser (ab 200 m vom Ufer), samt 150 m Umgebung ausgeblendet.
- Boote: weißer Top-Hat (Band minus Öffnung 5 × 5) in allen vier Bändern. Spitze = lokales Maximum im NIR-Top-Hat ab 0,008 (bzw. 6 Rauschbreiten), sichtbarer Top-Hat (Mittel B/G/R, 3 × 3 Maximum wegen Bandversatz bei Fahrt) ab 0,006, ragt mindestens 0,006 über das Minimum der 5 × 5-Umgebung. NIR/Grün zwischen 0,4 (darunter Flachwasser/Seekreide) und 2,0 (darüber Pflanzen/Land im Pixel).
- Gruppierung: zusammenhängende Pixel ab halber Schwelle. Lange schmale Linie (ab 80 m, Achsenverhältnis ab 4) ohne Kopf (hellste Spitze unter 2,5 × Median) = alte Kielwasserspur, nicht gezählt. Langgestreckt mit klar hellster Spitze (mindestens doppelt so hell wie die zweite) oder sehr hell (ab 0,1) = ein Boot mit Kielwasser bzw. ein Schiff. Sonst zählt jede Spitze (Bojenfelder). Ab 300 Pixeln zusammenhängend = Fläche, nicht gezählt.
- Größe: Gesamthelligkeit im NIR × 100 m² / 0,5 = Deckfläche, Länge = √(3 × Fläche). Klein unter 6 m, mittel 6–15 m, groß ab 15 m.
- Fahrgastschiff: Kern (Pixel ab 50 % der Spitze) mindestens 35 m lang und Kontrast ab 0,1, ab 100 m vom Ufer oder bis 150 m an einem Anleger; sonst „großes Uferobjekt“.
- Fahrtrichtung: Vektor vom Schwerpunkt der schwachen Pixel (unter 30 % der Spitze, mindestens 4, weiß: NIR ≥ 0,5 × Grün) zum Kopf. Nur in langgestreckten Objekten (Achsenverhältnis ab 2,5), ab 80 m vom Ufer, nicht im Bojenfeld, kein anderes Boot innerhalb 60 m.
- Geschwindigkeit: je Band Schwerpunkt der Pixel ab 20 % der Spitze in 110 × 110 m, Regression gegen die Aufnahmezeit (B02 0 s, B08 0,263 s, B03 0,527 s, B04 1,005 s; Heiselberg 2019, Sensors 19:2873). Nur der Betrag: Die Bandreihenfolge kehrt sich je nach Detektor um, die Richtung kommt deshalb aus dem Kielwasser.
- Ortsfest: Trefferkarte über alle Tage, bezogen auf die Tage, an denen die Stelle wolkenfrei war
