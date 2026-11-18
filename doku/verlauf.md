# Entwicklung an echten Satellitenbildern

Die Bootzählung wurde zuerst mit künstlichen Bildern entwickelt (siehe `selbsttest.py`) und dann Schritt für Schritt an echten Sentinel-2-Aufnahmen vom Sommer 2026 korrigiert. Hier steht, was die echten Läufe gezeigt haben und was daraus geändert wurde.

| Lauf | Bild | Ergebnis | Was falsch war | Änderung |
|---|---|---|---|---|
| 1 | [Starnberger See, 21.07.2026](bilder/v1_starnberger_see_2026-07-21_389_boote_wolken.jpg) | 389 „Boote“ | Fast alle Kreise saßen auf kleinen Quellwolken und Schleiern, die die Wolkenkarte des Satelliten (SCL) nicht kennt. | Wolkenfelder, Schleier und Wolkenfetzen werden aus dem Bild selbst erkannt und ausgeblendet. Der 21.07. wird jetzt übersprungen (nur 18 % auswertbar). |
| 2 | [Starnberger See, 16.07.2026](bilder/v2_starnberger_see_2026-07-16_58_boote.jpg) | 58 Boote | An einem wolkenlosen Tag wurden 72 „Wolkenfetzen“ gemeldet: helle Flachwasserbänke und Häfen. Sie wurden samt Bojenfeldern grau ausgeblendet, jeder Tag galt als „unsicher“. Kleine Boote auf offenem Wasser fehlten. | Flachwasser (heller in Grün als im Infrarot) und Uferobjekte gelten nicht mehr als Wolken. Option `--rohdaten` eingeführt, um echte Pixelwerte auswerten zu können. |
| 3 | [Starnberger See, 16.07.2026](bilder/v3_starnberger_see_2026-07-16_133_boote.jpg) | 133 Boote | Etwa 60 Treffer lagen 20–40 m vor dem Ufer auf einem hellen Flachwasserstreifen. Bojenfelder wurden fast nicht erkannt: im dichtesten Feld 11 von etwa 35 Booten. | Neuer Detektor, abgestimmt an den Rohdaten dieses Tages, siehe [Kalibrierung](kalibrierung.md). |
| 4 | [Starnberger See, 16.07.2026](../beispiele/starnberger_see_2026-07-16/vorschau_2026-07-16.png) | **298 Boote** | – | Ergebnis des abgestimmten Detektors, mit Attributen (Typ, Bewegung, Fahrtrichtung, Geschwindigkeit, Bojenfeld). Nachrechnen: `python rohdaten_auswerten.py daten/rohdaten_starnberger_see_2026-07-16.npz` |
| 5 | [Ammersee, 16.07.2026](bilder/v5_ammersee_2026-07-16_560_boote_erster_lauf.jpg) | 560 Boote | Eine helle Bank an der Ammermündung wurde als Fahrgastschiff (□) erkannt. Ein Boot am Rand eines Bojenfelds bekam einen Richtungspfeil, weil seine Nachbarn wie Kielwasser aussahen. | Fahrgastschiff nur auf offenem Wasser (ab 100 m vom Ufer) oder am Anleger, sonst „großes Uferobjekt“. Fahrtrichtung nur ohne anderes Boot im Umkreis von 60 m. |

## Noch offen
- **Ammersee:** Die 560 Boote vom 16.07. sind nicht Stück für Stück geprüft. Der See hat breite helle Flachwasserzonen (Herrschinger Bucht, vor Schondorf), auf denen am Starnberger See etwa jeder sechste Treffer daneben lag. Prüfen mit `--rohdaten` wie beim Starnberger See.
- **Fahrgastschiffe:** Das Kriterium (heller Kern ab 35 m) ist an keinem echten Fahrgastschiff geprüft. Am 16.07. um 12:17 Uhr war auf dem Starnberger See keines auf offenem Wasser.
- **Tage mit Quellwolken und Wind** mit dem neuen Detektor.
