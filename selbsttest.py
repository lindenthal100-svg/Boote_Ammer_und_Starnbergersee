#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Selbsttest für bootzaehlung.py – ohne Internet.

Erzeugt künstliche Sentinel-2-Szenen eines Sees mit bekannter Anzahl Boote
(Wolke, Wolkenschatten, Insel, Schilf, Steg, Badeinsel, zweite UTM-Zone,
verrauschtes Wasser) und prüft, ob das Skript richtig zählt.

  python selbsttest.py
"""
import csv
import datetime as dt
import json
import math
import os
import sys
import tempfile

import numpy as np
import pystac
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from rasterio.warp import Resampling, calculate_default_transform, reproject
from scipy import ndimage as ndi
from shapely.geometry import LineString, Point, box, mapping
from shapely.affinity import scale as shp_scale

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bootzaehlung as bz  # noqa: E402
ECHT_LADE_OSM = bz.lade_osm

RNG = np.random.default_rng(42)
WGS_UTM = Transformer.from_crs(4326, 32632, always_xy=True)
UTM_WGS = Transformer.from_crs(32632, 4326, always_xy=True)

# --------------------------------------------------------------------------- #
# Künstliche Landschaft (UTM 32N, 10 m)
# --------------------------------------------------------------------------- #
lon0, lat0, lon1, lat1 = bz.SUCH_BBOX
xs, ys = WGS_UTM.transform([lon0, lon1, lon0, lon1], [lat0, lat0, lat1, lat1])
X0 = math.floor((min(xs) - 1000) / 10) * 10
Y1 = math.ceil((max(ys) + 1000) / 10) * 10
B = int((max(xs) + 1000 - X0) / 10)
H = int((Y1 - (min(ys) - 1000)) / 10)
TRANSFORM = from_origin(X0, Y1, 10, 10)

zx, zy = WGS_UTM.transform(11.31, 47.90)
see_utm = shp_scale(Point(zx, zy).buffer(1.0), 2000, 6000, origin=(zx, zy))
insel_utm = Point(zx - 900, zy + 2500).buffer(90)
see_utm = see_utm.difference(insel_utm)
# Steg: vom Ostufer 120 m in den See
steg_utm = LineString([(zx + 2000 + 20, zy - 1000), (zx + 2000 - 120, zy - 1000)])
# Ein Steg weit weg (darf nicht übernommen werden)
steg_fern_utm = LineString([(zx + 5000, zy), (zx + 5100, zy)])

see_maske = bz.rasterisiere(see_utm, bz.Gitter("EPSG:32632", TRANSFORM, B, H))
steg_maske = bz.rasterisiere(steg_utm.buffer(5), bz.Gitter("EPSG:32632", TRANSFORM, B, H))
see_maske &= ~steg_maske
abstand_px = ndi.distance_transform_edt(see_maske)
zz, ss = np.mgrid[0:H, 0:B]


def utm_zu_px(x, y):
    s, z = ~TRANSFORM * (x, y)
    return int(z), int(s)


# Wolke über dem Südteil
WZ, WS = utm_zu_px(zx + 300, zy - 4200)
wolke_maske = (zz - WZ) ** 2 + (ss - WS) ** 2 <= 55 ** 2
schatten_maske = ((zz - WZ - 70) ** 2 + (ss - WS + 50) ** 2 <= 45 ** 2) & ~wolke_maske
# Schilf 60 m vor dem Westufer
SZ, SS = utm_zu_px(zx - 2000 + 70, zy + 500)
# Badeinsel (2x2 px, in jeder Szene an derselben Stelle)
BZ_, BS_ = utm_zu_px(zx + 1000, zy + 1500)
BZ_, BS_ = BZ_ - BZ_ % 2, BS_ - BS_ % 2


def zufallspunkte(n, min_abstand_ufer_px, max_abstand_ufer_px=1e9, verboten=(), gerade=False,
                  bereich=None):
    punkte = []
    erlaubt = ((abstand_px >= min_abstand_ufer_px) & (abstand_px <= max_abstand_ufer_px)
               & ~ndi.binary_dilation(wolke_maske | schatten_maske, iterations=25))
    if bereich is not None:
        erlaubt &= bereich
    kandidaten = np.argwhere(erlaubt)
    while len(punkte) < n:
        z, s = kandidaten[RNG.integers(len(kandidaten))]
        if gerade:
            z, s = z - z % 2, s - s % 2
        alle = punkte + list(verboten) + [(BZ_, BS_), (SZ, SS)]
        if all((z - a) ** 2 + (s - b) ** 2 > 25 ** 2 for a, b in alle):
            punkte.append((int(z), int(s)))
    return punkte


def grundszene(rauschen=0.0008):      # wie am 16.07.2026 gemessen
    land = ~(see_maske | steg_maske)
    nir = np.where(land, 0.30, 0.012 + 0.003 * (zz / H)).astype(np.float32)
    red = np.where(land, 0.05, 0.025).astype(np.float32)
    green = np.where(land, 0.08, 0.045).astype(np.float32)
    blue = np.where(land, 0.06, 0.05).astype(np.float32)
    for band in (nir, red, green, blue):
        band += RNG.normal(0, rauschen, nir.shape).astype(np.float32)
    # Insel (Vegetation), Steg (hell, Beton)
    insel = bz.rasterisiere(insel_utm, bz.Gitter("EPSG:32632", TRANSFORM, B, H))
    nir[insel], red[insel] = 0.35, 0.04
    nir[steg_maske], red[steg_maske], green[steg_maske], blue[steg_maske] = 0.25, 0.22, 0.22, 0.22
    scl = np.where(land, 4, 6).astype(np.uint8)
    scl[steg_maske] = 5
    # Schilf (3x3 px, grün)
    nir[SZ - 1:SZ + 2, SS - 1:SS + 2], red[SZ - 1:SZ + 2, SS - 1:SS + 2] = 0.15, 0.03
    # Badeinsel (etwa 6 x 6 m, weiß, verteilt auf 2x2 px)
    for b in (nir, red, green, blue):
        b[BZ_:BZ_ + 2, BS_:BS_ + 2] += 0.05
    return dict(nir=nir, red=red, green=green, blue=blue, scl=scl)


def boot(sz, z, s, anteil, groesse=(1, 1)):
    """Mischpixel aus Wasser und weißem Rumpf (Reflexion 0.5)."""
    h, w = groesse
    for rolle, hell in (("nir", 0.5), ("red", 0.45), ("green", 0.45), ("blue", 0.45)):
        feld = sz[rolle][z:z + h, s:s + w]
        feld[:] = (1 - anteil) * feld + anteil * hell


def fahrendes_schiff(sz, z, s, v_px_s, laenge=5, breite=2, anteil=0.5):
    """Schiff, das sich zwischen den Bandaufnahmen nach Osten bewegt (Sentinel-2-Zeitversatz)."""
    feld = np.zeros((breite + 6, laenge + 10), np.float32)
    feld[3:3 + breite, 3:3 + laenge] = anteil
    for rolle, hell in (("blue", 0.45), ("nir", 0.5), ("green", 0.45), ("red", 0.45)):
        f = ndi.shift(feld, (0, v_px_s * bz.BAND_ZEIT[{"blue": "blau", "nir": "nir", "green": "gruen",
                                                       "red": "rot"}[rolle]]), order=1)
        aus = sz[rolle][z:z + feld.shape[0], s:s + feld.shape[1]]
        aus[:] = (1 - f) * aus + f * hell


def wolke(sz, maske, schatten):
    for rolle in ("nir", "red", "green", "blue"):
        sz[rolle][maske] = 0.5
        sz[rolle][schatten] *= 0.4
    sz["scl"][maske] = 9
    sz["scl"][schatten] = 3


# --------------------------------------------------------------------------- #
# Szenen schreiben
# --------------------------------------------------------------------------- #

def schreibe_szene(ordner, name, sz, epsg=32632):
    os.makedirs(ordner, exist_ok=True)
    pfade = {}
    for rolle in ("blue", "green", "red", "nir", "scl"):
        if rolle == "scl":
            daten, transform, aufl = sz["scl"][::2, ::2], from_origin(X0, Y1, 20, 20), 20
        else:
            dn = np.clip(np.round((sz[rolle] + 0.1) * 10000), 1, 65535)
            daten, transform, aufl = dn.astype(np.uint16), TRANSFORM, 10
        pfad = os.path.join(ordner, f"{name}_{rolle}.tif")
        h, w = daten.shape
        if epsg == 32632:
            ziel_transform, ziel_w, ziel_h, ziel = transform, w, h, daten
        else:
            l, b_, r, t = rasterio.transform.array_bounds(h, w, transform)
            ziel_transform, ziel_w, ziel_h = calculate_default_transform(
                "EPSG:32632", f"EPSG:{epsg}", w, h, l, b_, r, t, resolution=aufl)
            ziel = np.zeros((ziel_h, ziel_w), daten.dtype)
            reproject(daten, ziel, src_transform=transform, src_crs="EPSG:32632",
                      dst_transform=ziel_transform, dst_crs=f"EPSG:{epsg}",
                      resampling=Resampling.nearest, src_nodata=0, dst_nodata=0)
        with rasterio.open(pfad, "w", driver="GTiff", height=ziel_h, width=ziel_w, count=1,
                           dtype=ziel.dtype, crs=f"EPSG:{epsg}", transform=ziel_transform,
                           nodata=0, tiled=True, compress="deflate") as f:
            f.write(ziel, 1)
        pfade[rolle] = pfad
    return pfade


def mache_item(name, pfade, datum, epsg=32632, wolken=10.0, fussabdruck_utm=None):
    fuss = fussabdruck_utm or box(X0, Y1 - H * 10, X0 + B * 10, Y1)
    fuss_wgs = bz.transformiere(fuss, UTM_WGS)
    item = pystac.Item(id=name, geometry=mapping(fuss_wgs), bbox=list(fuss_wgs.bounds),
                       datetime=datum,
                       properties={"eo:cloud_cover": wolken, "platform": "sentinel-2b",
                                   "proj:epsg": epsg})
    for rolle, pfad in pfade.items():
        extra = {} if rolle == "scl" else {"raster:bands": [{"scale": 0.0001, "offset": -0.1,
                                                              "nodata": 0}]}
        item.add_asset(rolle, pystac.Asset(href=pfad, media_type=pystac.MediaType.COG,
                                           extra_fields=extra))
    return item


def baue_testdaten(ordner):
    items, erwartet = [], {}

    # Szene A: alles drin
    sz = grundszene()
    klein = zufallspunkte(10, 30)
    for z, s in klein:
        boot(sz, z, s, 0.08)
    doppel = zufallspunkte(5, 30, verboten=klein)
    for z, s in doppel:
        boot(sz, z, s, 0.06, (1, 2))
    gross = zufallspunkte(2, 40, verboten=klein + doppel)
    for z, s in gross:
        boot(sz, z, s, 0.6, (6, 2))
    ufer = zufallspunkte(3, 6, 10, verboten=klein + doppel + gross)
    for z, s in ufer:
        boot(sz, z, s, 0.08)
    wolkenboot = zufallspunkte(1, 30, verboten=klein + doppel + gross + ufer, gerade=True)
    for z, s in wolkenboot:
        boot(sz, z, s, 0.10)
        sz["scl"][z:z + 2, s:s + 2] = 9          # SCL hält das Boot für eine Wolke
    schwach = zufallspunkte(1, 30, verboten=klein + doppel + gross + ufer + wolkenboot)
    for z, s in schwach:
        boot(sz, z, s, 0.012)                     # zu klein für 10 m – darf fehlen
    wolke(sz, wolke_maske, schatten_maske)
    for z, s in ((WZ, WS), (WZ + 20, WS - 10)):   # Boote unter der Wolke
        boot(sz, z, s, 0.1)
    erwartet["A"] = dict(boote=10 + 5 + 2 + 3 + 1 + 1, gross=2, ufernah=3, status="gut")
    pfade = schreibe_szene(ordner, "A", sz)
    datum_a = dt.datetime(2026, 7, 12, 10, 20, tzinfo=dt.timezone.utc)
    items.append(mache_item("S2B_T32UPU_20260712_A", pfade, datum_a, wolken=12))
    # gleiche Aufnahme, zweite Kachel in Zone 33, deckt den See nur zur Hälfte ab
    halb = box(zx, Y1 - H * 10, X0 + B * 10, Y1)
    items.append(mache_item("S2B_T33UTP_20260712_A2", pfade, datum_a, epsg=32633,
                            wolken=1, fussabdruck_utm=halb))

    # Szene B: nur kleine Boote
    sz = grundszene()
    punkte_b = zufallspunkte(15, 30)
    for z, s in punkte_b:
        boot(sz, z, s, 0.09)
    fz, fs = zufallspunkte(1, 50, verboten=punkte_b)[0]
    fahrendes_schiff(sz, fz, fs, v_px_s=0.6)          # 6 m/s = 21,6 km/h nach Osten
    erwartet["B"] = dict(boote=17, gross=1, ufernah=0, status="gut")
    pfade = schreibe_szene(ordner, "B", sz)
    items.append(mache_item("S2B_T32UPU_20260717_B", pfade,
                            dt.datetime(2026, 7, 17, 10, 20, tzinfo=dt.timezone.utc)))

    # Szene C: Kachel in UTM-Zone 33 (muss umprojiziert werden)
    sz = grundszene()
    for z, s in zufallspunkte(8, 30, gerade=True):
        boot(sz, z, s, 0.10, (2, 2))
    erwartet["C"] = dict(boote=9, gross=0, ufernah=0, status="gut")
    pfade = schreibe_szene(ordner, "C", sz, epsg=32633)
    items.append(mache_item("S2A_T33UTP_20260720_C", pfade,
                            dt.datetime(2026, 7, 20, 10, 30, tzinfo=dt.timezone.utc), epsg=32633))

    # Szene D: fast ganz bewölkt
    sz = grundszene()
    dicht = (abstand_px > 0) & (zz > H * 0.25)
    wolke(sz, dicht, np.zeros_like(dicht))
    erwartet["D"] = dict(boote=None, status="übersprungen")
    pfade = schreibe_szene(ordner, "D", sz)
    items.append(mache_item("S2B_T32UPU_20260722_D", pfade,
                            dt.datetime(2026, 7, 22, 10, 20, tzinfo=dt.timezone.utc), wolken=60))

    # Szene E: sehr unruhiges Wasser / Glitzern
    sz = grundszene(rauschen=0.02)
    erwartet["E"] = dict(boote=None, status="unsicher")
    pfade = schreibe_szene(ordner, "E", sz)
    items.append(mache_item("S2A_T32UPU_20260725_E", pfade,
                            dt.datetime(2026, 7, 25, 10, 30, tzinfo=dt.timezone.utc)))

    # Szene F: Quellwolken, die die Wolkenkarte nur teilweise kennt, Schleier, Flachwasser
    sz = grundszene()
    zc, sc = utm_zu_px(zx, zy)
    rng = np.random.default_rng(7)
    wolken = np.zeros((H, B), np.float32)
    for _ in range(90):                              # weiche Quellwolken-Fetzen
        z0 = rng.integers(zc - 520, zc - 240)
        s0 = rng.integers(sc - 220, sc + 220)
        sig = rng.uniform(1.5, 6.0)
        amp = rng.uniform(0.05, 0.35)
        r = int(4 * sig) + 1
        zs, ss_ = np.mgrid[z0 - r:z0 + r + 1, s0 - r:s0 + r + 1]
        wolken[z0 - r:z0 + r + 1, s0 - r:s0 + r + 1] += amp * np.exp(
            -((zs - z0) ** 2 + (ss_ - s0) ** 2) / (2 * sig ** 2))
    schleier = np.zeros((H, B), np.float32)          # dünner Schleier, nicht in der Wolkenkarte
    schleier[zc + 100:zc + 230, sc - 250:sc + 250] = 0.035
    schleier = ndi.gaussian_filter(schleier, 8)
    for rolle in ("nir", "red", "green", "blue"):
        sz[rolle] += wolken + schleier
    sz["scl"][(wolken > 0.25) & (sz["scl"] == 6)] = 9   # nur dichte Kerne als Wolke markiert
    # Flachwasser mit hellem Grund (Seekreide) am Westufer: hell in Grün, kaum im Infrarot
    flach = see_maske & (abstand_px >= 2) & (abstand_px <= 15) & (zz > zc + 300) & (zz < zc + 500) \
        & (ss < sc)
    sz["green"][flach] += 0.05
    sz["blue"][flach] += 0.04
    sz["red"][flach] += 0.02
    sz["nir"][flach] += 0.006
    flecken = np.argwhere(flach & (abstand_px >= 4) & (abstand_px <= 12))
    for z, s in flecken[rng.choice(len(flecken), 15, replace=False)]:
        sz["nir"][z:z + 2, s:s + 2] += 0.025
        sz["green"][z:z + 2, s:s + 2] += 0.08
    # echte Boote im klaren Südteil
    sueden = (zz > zc + 260) & ~ndi.binary_dilation(flach, iterations=20)
    for z, s in zufallspunkte(12, 30, bereich=sueden):
        boot(sz, z, s, 0.08)
    erwartet["F"] = dict(boote=13, gross=0, ufernah=0, status=None, toleranz=1)
    pfade = schreibe_szene(ordner, "F", sz)
    items.append(mache_item("S2A_T32UPU_20260728_F", pfade,
                            dt.datetime(2026, 7, 28, 10, 30, tzinfo=dt.timezone.utc)))

    # Szene G: klarer Tag, helle Flachwasserbank am Ostufer mit Booten an Bojen,
    # ein Boot mit Kielwasser auf dem offenen See
    sz = grundszene()
    bank = see_maske & (abstand_px >= 2) & (abstand_px <= 25) & (zz > zc - 200) & (zz < zc + 200) \
        & (ss > sc)
    bank_weich = ndi.gaussian_filter(bank.astype(np.float32), 2) * see_maske
    sz["green"] += 0.07 * bank_weich
    sz["blue"] += 0.06 * bank_weich
    sz["red"] += 0.04 * bank_weich
    sz["nir"] += 0.015 * bank_weich
    bojen = zufallspunkte(5, 6, 13, bereich=bank)
    for z, s in bojen:
        boot(sz, z, s, 0.08)
    kz, ks = zufallspunkte(1, 40, bereich=(zz > zc + 260), verboten=bojen)[0]
    boot(sz, kz, ks, 0.15)                           # Boot ...
    for j in range(1, 9):                            # ... mit Kielwasser (Schaum, schwächer)
        anteil = 0.03 - 0.0025 * j
        for rolle in ("nir", "red", "green", "blue"):
            sz[rolle][kz + j, ks + j // 2] += anteil
    # großer heller Fleck 50 m vor dem Ufer, kein Anleger in der Nähe (wie die Ammermündung)
    uz, us = zufallspunkte(1, 5, 5, bereich=(zz < zc - 300) & (ss < sc), verboten=bojen + [(kz, ks)])[0]
    boot(sz, uz, us, 0.6, (2, 6))
    # kleines Bojenfeld auf offenem Wasser, ein Boot mit schwachen Pixeln dahinter
    fz, fs = zufallspunkte(1, 60, bereich=(zz > zc + 100) & (zz < zc + 250), verboten=bojen + [(kz, ks), (uz, us)])[0]
    for dz, ds in ((0, 0), (0, 3), (3, 0), (3, 3), (6, 1)):
        boot(sz, fz + dz, fs + ds, 0.08)
    for j in range(1, 4):
        for rolle in ("nir", "red", "green", "blue"):
            sz[rolle][fz - j, fs - j] += 0.006       # unter der Bootsschwelle, aber Teil des Objekts
    erwartet["G"] = dict(boote=13, gross=1, ufernah=6, status="gut")
    pfade = schreibe_szene(ordner, "G", sz)
    items.append(mache_item("S2C_T32UPU_20260730_G", pfade,
                            dt.datetime(2026, 7, 30, 10, 20, tzinfo=dt.timezone.utc)))
    return items, erwartet


def schreibe_see_geojson(pfad):
    see_wgs = bz.transformiere(see_utm, UTM_WGS)
    stege_wgs = bz.transformiere(steg_utm, UTM_WGS)
    anleger_wgs = [("Teststeg", *UTM_WGS.transform(zx + 2000 - 120, zy - 1000))]
    bz.schreibe_umriss_geojson(pfad, see_wgs, stege_wgs, anleger_wgs)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

FEHLER = []


def pruefe(bedingung, text):
    print(("  ok   " if bedingung else "  FEHLER ") + text)
    if not bedingung:
        FEHLER.append(text)


def lies_csv(pfad):
    with open(pfad, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def test_osm_zusammenbau():
    print("OSM-Umriss zusammensetzen")
    ring = [(11.30, 47.85), (11.34, 47.85), (11.34, 47.95), (11.30, 47.95), (11.30, 47.85)]
    insel = [(11.31, 47.90), (11.312, 47.90), (11.312, 47.902), (11.31, 47.902), (11.31, 47.90)]
    geo = lambda pts: [{"lon": x, "lat": y} for x, y in pts]
    daten = {"elements": [
        {"type": "relation", "tags": {"natural": "water", "name": "Starnberger See"}, "members": [
            {"type": "way", "role": "outer", "geometry": geo(ring[:3])},
            {"type": "way", "role": "outer", "geometry": geo(ring[2:])},
            {"type": "way", "role": "inner", "geometry": geo(insel)},
        ]},
        {"type": "way", "tags": {"man_made": "pier"}, "geometry": geo([(11.339, 47.90), (11.345, 47.90)])},
        {"type": "way", "tags": {"man_made": "pier"}, "geometry": geo([(11.50, 47.90), (11.51, 47.90)])},
        {"type": "node", "lat": 47.90, "lon": 11.3395, "tags": {"amenity": "ferry_terminal", "name": "Dampfersteg Test"}},
        {"type": "node", "lat": 47.9001, "lon": 11.3396, "tags": {"public_transport": "stop_position", "ferry": "yes", "name": "Test (Schiff)"}},
        {"type": "way", "center": {"lat": 47.86, "lon": 11.31}, "tags": {"amenity": "ferry_terminal", "name": "Südsteg"}},
        {"type": "node", "lat": 47.90, "lon": 11.60, "tags": {"amenity": "ferry_terminal", "name": "anderer See"}},
    ]}
    see, stege, anleger = bz.osm_zu_geometrien(daten)
    pruefe(see is not None and abs(see.area - (0.04 * 0.10 - 0.002 * 0.002)) < 1e-9,
           "Außenring aus zwei Wegen + Insel als Loch")
    pruefe(stege is not None and stege.length > 0 and stege.bounds[2] < 11.4,
           "nur der Steg am See wird übernommen")
    namen = sorted(a[0] for a in anleger)
    pruefe(namen == ["Dampfersteg Test", "Südsteg"],
           f"Schiffsanleger erkannt, doppelte zusammengefasst, fremde ignoriert ({namen})")


def test_ablauf(ordner):
    print("Testdaten erzeugen ...")
    items, erwartet = baue_testdaten(os.path.join(ordner, "szenen"))
    test_ablauf.items = items
    see_pfad = os.path.join(ordner, "see.geojson")
    schreibe_see_geojson(see_pfad)
    bz.suche_szenen = lambda *a, **k: items          # statt Internet-Suche

    ausgabe = os.path.join(ordner, "ergebnis")
    print("Skript ausführen (mit Seeumriss) ...")
    bz.main(["--von", "2026-07-01", "--bis", "2026-07-31", "--ausgabe", ausgabe,
             "--see-geojson", see_pfad, "--vorschau", "--rohdaten"])

    print("Ergebnisse prüfen (mit Seeumriss)")
    zus = {z["szene"][-1]: z for z in lies_csv(os.path.join(ausgabe, "zusammenfassung.csv"))}
    boote = lies_csv(os.path.join(ausgabe, "boote.csv"))
    pruefe(zus["A"]["szene"] == "S2B_T32UPU_20260712_A",
           "Kachel mit voller Seeabdeckung gewählt (nicht die halbe aus Zone 33)")
    for name, erw in erwartet.items():
        z = zus[name]
        if erw["status"] is not None:
            pruefe(z["qualitaet"] == erw["status"] or (erw["status"] == "gut" and z["qualitaet"] == "eingeschränkt"
                                                        and name == "A"),
                   f"Szene {name}: Qualität '{z['qualitaet']}' (erwartet {erw['status']})")
        if erw.get("boote") is not None:
            toleranz = erw.get("toleranz", 0)
            pruefe(abs(int(z["boote"]) - erw["boote"]) <= toleranz,
                   f"Szene {name}: {z['boote']} Boote gezählt (erwartet {erw['boote']}"
                   + (f" ± {toleranz})" if toleranz else ")"))
        if erw.get("gross") is not None:
            pruefe(int(z["gross"]) == erw["gross"], f"Szene {name}: {z['gross']} große Schiffe (erwartet {erw['gross']})")
        if erw.get("ufernah") is not None:
            # Badeinsel liegt weit draußen; ufernah = nur die nahe am Ufer platzierten Boote
            pruefe(int(z["ufernah"]) == erw["ufernah"],
                   f"Szene {name}: {z['ufernah']} ufernah (erwartet {erw['ufernah']})")
    pruefe(70 <= int(zus["A"]["auswertbar_see_prozent"]) <= 99,
           f"Szene A: Wolke erkannt, {zus['A']['auswertbar_see_prozent']} % auswertbar")
    pruefe(int(zus["F"]["auswertbar_see_prozent"]) < 90 and zus["F"]["qualitaet"] != "gut",
           f"Szene F: Wolkenfeld/Schleier ausgeblendet, {zus['F']['auswertbar_see_prozent']} % auswertbar, "
           f"Qualität '{zus['F']['qualitaet']}'")
    ortsfest = [b for b in boote if b["ortsfest"] == "ja"]
    tage_ortsfest = sorted({b["datum"] for b in ortsfest})
    pruefe(len(ortsfest) >= 3 and all(b["pixel"] in ("4", "2", "3", "5", "6") for b in ortsfest),
           f"Badeinsel als ortsfest markiert ({len(ortsfest)}× an {len(tage_ortsfest)} Tagen)")
    nicht_badeinsel = [b for b in ortsfest if abs(float(b["breitengrad"].replace(",", ".")) -
                                                    float(ortsfest[0]["breitengrad"].replace(",", "."))) > 0.0005]
    pruefe(not nicht_badeinsel, "keine wandernden Boote fälschlich ortsfest")
    with open(os.path.join(ausgabe, "boote.geojson"), encoding="utf-8") as f:
        geo = json.load(f)
    pruefe(len(geo["features"]) == len(boote), "GeoJSON und CSV enthalten dieselben Boote")
    lon, lat = geo["features"][0]["geometry"]["coordinates"]
    pruefe(11.25 < lon < 11.37 and 47.83 < lat < 47.97, f"Koordinaten plausibel ({lat:.5f}, {lon:.5f})")
    roh = np.load(os.path.join(ausgabe, "rohdaten_2026-07-30.npz"))
    pruefe(roh["nir"].shape == roh["scl"].shape and roh["see"].any() and str(roh["szene"]).endswith("_G"),
           f"Rohdaten-Export lesbar ({os.path.getsize(os.path.join(ausgabe, 'rohdaten_2026-07-30.npz')) / 1e6:.1f} MB)")
    print("Attribute prüfen")
    nach_tag = lambda d: [b for b in boote if b["datum"] == d]
    a = nach_tag("2026-07-12")
    schiffe = [b for b in a if b["typ"] == "Fahrgastschiff"]
    pruefe(len(schiffe) == 2 and all(b["bewegung"] == "steht" for b in schiffe),
           f"Szene A: 2 liegende Fahrgastschiffe erkannt ({[(b['typ'], b['bewegung']) for b in schiffe]})")
    bb = [b for b in nach_tag("2026-07-17") if b["typ"] == "Fahrgastschiff"]
    v = float(bb[0]["geschwindigkeit_kmh"].replace(",", ".")) if bb and bb[0]["geschwindigkeit_kmh"] else None
    pruefe(len(bb) == 1 and bb[0]["bewegung"] == "fährt" and v is not None and abs(v - 21.6) <= 8,
           f"Szene B: fahrendes Fahrgastschiff, {v} km/h gemessen (Soll 21,6)")
    g = [b for b in nach_tag("2026-07-30") if b["richtung"]]
    gr = float(g[0]["richtung_grad"].replace(",", ".")) if g else None
    pruefe(len(g) == 1 and gr is not None and 300 <= gr <= 360 and g[0]["bewegung"] == "fährt",
           f"Szene G: Boot mit Kielwasser fährt nach {g[0]['richtung'] if g else '–'} ({gr}°, Soll ca. 334°)")
    pruefe(all(b["naechster_anleger"] == "Teststeg" and b["abstand_anleger_m"] for b in boote),
           "jedes Boot hat den nächsten Anleger samt Abstand")
    bf = [b for b in nach_tag("2026-07-30") if b["umgebung"] == "Bojenfeld/Hafen"]
    pruefe(len(bf) == 5, f"nur das kleine Feld gilt als Bojenfeld ({len(bf)} Boote, Soll 5)")
    uo = [b for b in nach_tag("2026-07-30") if b["typ"] != "Boot"]
    pruefe(len(uo) == 1 and uo[0]["typ"] == "großes Uferobjekt",
           f"großer Fleck am Ufer ohne Anleger ist kein Fahrgastschiff ({[b['typ'] for b in uo]})")
    for datei in ("verlauf.png", "vorschau_2026-07-12.png", "vorschau_2026-07-30.png"):
        pruefe(os.path.getsize(os.path.join(ausgabe, datei)) > 10000, f"{datei} geschrieben")

    print("Skript ausführen (ohne Seeumriss, OSM nicht erreichbar) ...")
    def kein_osm():
        raise RuntimeError("Testmodus: kein Internet")
    bz.lade_osm = kein_osm
    ausgabe2 = os.path.join(ordner, "ergebnis_ohne_umriss")
    bz.main(["--von", "2026-07-01", "--bis", "2026-07-31", "--ausgabe", ausgabe2])
    print("Ergebnisse prüfen (ohne Seeumriss)")
    zus2 = {z["szene"][-1]: z for z in lies_csv(os.path.join(ausgabe2, "zusammenfassung.csv"))}
    for name in ("A", "B", "C", "F", "G"):
        n = int(zus2[name]["boote"])
        pruefe(abs(n - erwartet[name]["boote"]) <= 1,
               f"Szene {name}: {n} Boote (erwartet {erwartet[name]['boote']} ± 1)")
    return ausgabe


def test_ammersee(ordner, items):
    print("Ammersee-Einstellungen")
    bz.waehle_see("ammersee")
    abfrage = bz.overpass_abfrage()
    pruefe('"name"="Ammersee"' in abfrage and "(47.86,11.0,48.12,11.24)" in abfrage,
           "OpenStreetMap-Abfrage sucht den Ammersee im richtigen Rahmen")
    pruefe(bz.kurzname() == "ammersee", "eigener Zwischenspeicher ammersee_osm.geojson")
    lon0, lat0, lon1, lat1 = bz.SUCH_BBOX
    pruefe(lon0 < 11.10 < 11.17 < lon1 and lat0 < 47.93 < 48.07 < lat1,
           "Suchrahmen enthält Dießen, Herrsching und Stegen")
    pruefe(lon1 < 11.24, "Suchrahmen reicht nicht bis zum Starnberger See")
    # kompletter Lauf mit --see ammersee (künstliche Szene B, Umriss vorgegeben)
    see_pfad = os.path.join(ordner, "see.geojson")
    bz.suche_szenen = lambda *a, **k: [i for i in items if i.id.endswith("_B")]
    alt = os.getcwd()
    os.chdir(ordner)
    try:
        bz.main(["--see", "ammersee", "--von", "2026-07-01", "--bis", "2026-07-31",
                 "--see-geojson", see_pfad, "--vorschau"])
    finally:
        os.chdir(alt)
    aus = os.path.join(ordner, "ergebnisse_ammersee")
    pruefe(os.path.isdir(aus), "Standard-Ergebnisordner ergebnisse_ammersee angelegt")
    zus = lies_csv(os.path.join(aus, "zusammenfassung.csv"))
    pruefe(len(zus) == 1 and zus[0]["boote"] == "17", f"Ammersee-Lauf zählt {zus[0]['boote']} Boote (erwartet 17)")
    with open(os.path.join(aus, "boote.geojson"), encoding="utf-8") as f:
        pruefe(len(json.load(f)["features"]) == 17, "GeoJSON des Ammersee-Laufs vollständig")
    pruefe(bz.SEE_NAME == "Ammersee", "Beschriftungen verwenden den Namen Ammersee")
    bz.waehle_see("starnberger-see")


def test_osm_download(ordner):
    print("OpenStreetMap-Abruf bei gestörten Servern")
    import io
    import types
    import urllib.error
    import urllib.request
    antwort_json = json.dumps({"elements": []}).encode()
    aufrufe = []

    class Antwort(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def falsches_urlopen(fehlerbild):
        def urlopen(anfrage, timeout=None):
            server = anfrage.full_url.split("/")[2]
            aufrufe.append(server)
            art = fehlerbild.get(server, "ok")
            if art == "ok":
                return Antwort(antwort_json)
            if art == "haengt":
                raise TimeoutError("timed out")
            raise urllib.error.HTTPError(anfrage.full_url, art, "Fehler", {}, None)
        return urlopen

    echt_urlopen, echt_sleep, echt_log = urllib.request.urlopen, bz.time.sleep, bz.log
    bz.lade_osm = ECHT_LADE_OSM               # der Ablauf-Test hatte eine Attrappe eingesetzt
    bz.time.sleep = lambda s: None
    bz.log = lambda *a: None
    try:
        # Hauptserver überlastet (2x 502), Ausweichserver antwortet
        urllib.request.urlopen = falsches_urlopen({"overpass-api.de": 502})
        aufrufe.clear()
        ergebnis = bz.lade_osm()
        pruefe(ergebnis == {"elements": []} and aufrufe == ["overpass-api.de", "overpass-api.de",
                                                            "overpass.private.coffee"],
               f"502 beim Hauptserver: zweiter Versuch, dann Ausweichserver ({aufrufe})")
        # alle gestört
        urllib.request.urlopen = falsches_urlopen({"overpass-api.de": 504, "overpass.private.coffee": "haengt",
                                                   "maps.mail.ru": 403})
        aufrufe.clear()
        try:
            bz.lade_osm()
            pruefe(False, "Totalausfall wird gemeldet")
        except RuntimeError as fehler:
            pruefe(len(aufrufe) == 4 and "maps.mail.ru: HTTP 403" in str(fehler),
                   f"Totalausfall: alle drei Server probiert, klare Meldung ({len(aufrufe)} Anfragen)")
        # alter Zwischenspeicher ohne Anleger + Totalausfall: weiterrechnen, heute nicht erneut
        bz.waehle_see("ammersee")
        aus = os.path.join(ordner, "osm_cache_test")
        os.makedirs(aus, exist_ok=True)
        see_wgs = bz.transformiere(see_utm, UTM_WGS)
        bz.schreibe_umriss_geojson(os.path.join(aus, "ammersee_osm.geojson"), see_wgs, None, version=1)
        args = types.SimpleNamespace(see_geojson=None, ausgabe=aus)
        aufrufe.clear()
        see1, _, anl1 = bz.lade_see_umriss(args)
        n1 = len(aufrufe)
        see2, _, anl2 = bz.lade_see_umriss(args)
        pruefe(see1 is not None and see2 is not None and anl1 == [] and n1 == 4 and len(aufrufe) == 4,
               "alter Umriss wird weiterverwendet, am selben Tag kein zweiter Abrufversuch")
        # am nächsten Tag (Datum im Zwischenspeicher zurücksetzen) und Server wieder da
        urllib.request.urlopen = falsches_urlopen({})
        antwort_json = json.dumps({"elements": [
            {"type": "relation", "tags": {"natural": "water", "name": "Ammersee"}, "members": [
                {"type": "way", "role": "outer", "geometry": [{"lon": x, "lat": y} for x, y in see_wgs.exterior.coords]}]},
            {"type": "node", "lat": see_wgs.centroid.y, "lon": see_wgs.centroid.x,
             "tags": {"amenity": "ferry_terminal", "name": "Dampfersteg Mitte"}}]}).encode()
        pfad = os.path.join(aus, "ammersee_osm.geojson")
        d = json.load(open(pfad, encoding="utf-8")); d["features"][0]["properties"]["anleger_versuch"] = "2000-01-01"
        json.dump(d, open(pfad, "w", encoding="utf-8"))
        _, _, anl3 = bz.lade_see_umriss(args)
        pruefe([a[0] for a in anl3] == ["Dampfersteg Mitte"] and bz.lies_umriss_geojson(pfad)[3]["version"] == 2,
               "am nächsten Tag: Anleger nachgeladen und Zwischenspeicher aktualisiert")
    finally:
        urllib.request.urlopen, bz.time.sleep, bz.log = echt_urlopen, echt_sleep, echt_log
        bz.waehle_see("starnberger-see")


if __name__ == "__main__":
    test_osm_zusammenbau()
    ordner = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="bootzaehlung_test_")
    test_ablauf(ordner)
    test_ammersee(ordner, test_ablauf.items)
    test_osm_download(ordner)
    print()
    if FEHLER:
        print(f"{len(FEHLER)} Fehler:")
        for f in FEHLER:
            print("  - " + f)
        sys.exit(1)
    print(f"Alle Prüfungen bestanden. Testdaten und Ergebnisse: {ordner}")
