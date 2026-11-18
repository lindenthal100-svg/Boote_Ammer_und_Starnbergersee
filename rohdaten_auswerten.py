#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wertet eine mit `bootzaehlung.py --rohdaten` gespeicherte Szene erneut aus – ohne Internet.

Praktisch zum Nachvollziehen von Ergebnissen und zum Ausprobieren von Einstellungen:

  python rohdaten_auswerten.py daten/rohdaten_starnberger_see_2026-07-16.npz
  python rohdaten_auswerten.py daten/rohdaten_starnberger_see_2026-07-16.npz --empfindlichkeit 1.3
  python rohdaten_auswerten.py ergebnisse_ammersee/rohdaten_2026-07-16.npz --see ammersee \\
      --anleger-geojson ergebnisse_ammersee/ammersee_osm.geojson

Ergebnis (Ordner --ausgabe): zusammenfassung.csv, boote.csv, boote.geojson, vorschau_<datum>.png –
im selben Format wie bei bootzaehlung.py. "ortsfest" bleibt "unbekannt" (dafür braucht es
mehrere Tage).
"""
import argparse
import datetime as dt
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bootzaehlung as bz  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Gespeicherte Rohdaten (.npz) erneut auswerten.")
    ap.add_argument("datei", help="rohdaten_<datum>.npz aus bootzaehlung.py --rohdaten")
    ap.add_argument("--see", choices=sorted(bz.SEEN),
                    help="welcher See (Standard: aus dem Dateinamen, sonst Starnberger See)")
    ap.add_argument("--ausgabe", help="Ergebnisordner (Standard: neben der Datei, *_auswertung)")
    ap.add_argument("--anleger-geojson", help="<see>_osm.geojson, um den nächsten Anleger zu ergänzen")
    ap.add_argument("--empfindlichkeit", type=float, default=1.0)
    ap.add_argument("--min-kontrast", type=float, default=None)
    ap.add_argument("--uferpuffer", type=float, default=30.0)
    ap.add_argument("--ufernah", type=float, default=150.0)
    args = ap.parse_args(argv)

    from affine import Affine
    from pyproj import Transformer

    schluessel = args.see or ("ammersee" if "ammersee" in os.path.basename(args.datei).lower()
                              else "starnberger-see")
    bz.waehle_see(schluessel)
    r = np.load(args.datei, allow_pickle=False)
    nir, red, green, blue = (r[k].astype(np.float32) for k in ("nir", "red", "green", "blue"))
    scl, see = r["scl"], r["see"].astype(bool)
    gitter = bz.Gitter(str(r["crs"]), Affine(*[float(v) for v in r["transform"]]),
                       nir.shape[1], nir.shape[0])
    szene, zeit = str(r["szene"]), str(r["zeit_utc"])
    treffer = re.search(r"_(\d{8})", szene) or re.search(r"(\d{4}-\d{2}-\d{2})", args.datei)
    datum = (dt.datetime.strptime(treffer.group(1).replace("-", ""), "%Y%m%d").date()
             if treffer else dt.date.today())

    e = max(args.empfindlichkeit, 0.1)
    p = bz.Parameter(ufer_puffer_m=args.uferpuffer, ufernah_m=args.ufernah)
    p.k_boot /= e
    p.min_kontrast = args.min_kontrast if args.min_kontrast else p.min_kontrast / e
    p.min_kontrast_sichtbar /= e

    abstand = bz.abstand_zum_ufer(see)
    gueltig, _, klar = bz.gueltige_flaeche(scl, see, abstand, p)
    n_vorher = max(int(gueltig.sum()), 1)
    boote, info = bz.erkenne_boote(nir, red, green, blue, gueltig, see, abstand, p)
    klar *= float(info["gueltig"].sum()) / n_vorher

    anleger = []
    if args.anleger_geojson:
        t = Transformer.from_crs(4326, bz.ZIEL_EPSG, always_xy=True)
        anleger = [(n, *t.transform(lon, lat))
                   for n, lon, lat in bz.lies_umriss_geojson(args.anleger_geojson)[2]]
    bz.ergaenze_anleger(boote, gitter, anleger, p)

    satellit = szene[:3].upper() if szene[:2].upper() == "S2" else ""
    erg = bz.SzenenErgebnis(datum, zeit, satellit, szene, klar, "")
    if klar < p.min_klar:
        erg.status, erg.hinweis = "übersprungen", f"nur {klar:.0%} der Seefläche auswertbar"
    else:
        erg.boote = boote
        erg.status, erg.hinweis = bz.bewerte_qualitaet(klar, info, p)
    leer = np.zeros(see.shape, np.uint16)
    bz.markiere_ortsfeste([erg], leer, leer, p)

    ausgabe = args.ausgabe or os.path.splitext(args.datei)[0] + "_auswertung"
    os.makedirs(ausgabe, exist_ok=True)
    pfade = bz.schreibe_ergebnisse(ausgabe, [erg], gitter, p)
    for b in erg.boote:
        b["_ufernah_m"] = p.ufernah_m
    titel = (f"{len(erg.boote)} Boote · {datum:%d.%m.%Y}\n"
             f"{bz.SEE_NAME} · {zeit} UTC · {satellit}\n"
             f"gelb = offener See · orange = ufernah · □ = Fahrgastschiff\n"
             f"Pfeil = Fahrtrichtung · grau = nicht auswertbar")
    vorschau = os.path.join(ausgabe, f"vorschau_{datum.isoformat()}.png")
    bz.speichere_vorschau(vorschau, (red, green, blue), see, info["gueltig"], erg.boote, titel)

    offen = sum(b["abstand_ufer_m"] >= p.ufernah_m for b in erg.boote)
    print(f"{bz.SEE_NAME} {datum:%d.%m.%Y}: {len(erg.boote)} Boote (offener See {offen}, "
          f"ufernah {len(erg.boote) - offen}), auswertbar {klar:.0%}, Qualität {erg.status}")
    for pfad in (*pfade, vorschau):
        print(f"  {pfad}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
