# Rohdaten

`rohdaten_starnberger_see_2026-07-16.npz` ist der Ausschnitt um den Starnberger See aus der Sentinel-2-Szene `S2C_T32UPU_20260716T100555_L2A` (16.07.2026, 10:17 UTC, wolkenlos). Gespeichert wurde er mit `bootzaehlung.py --rohdaten`. An diesen Daten ist die Erkennung abgestimmt, siehe [doku/kalibrierung.md](../doku/kalibrierung.md).

| Feld | Inhalt |
|---|---|
| `nir`, `red`, `green`, `blue` | Reflexion (0–1) der Bänder B08, B04, B03, B02, 10 m, float16, NaN = keine Daten |
| `scl` | Klassenkarte (Scene Classification) auf 10 m, uint8 |
| `see` | Seefläche aus OpenStreetMap ohne Stege |
| `gueltig_scl` | auswertbare Seefläche laut Klassenkarte (ohne Wolken, ohne Uferstreifen) |
| `transform`, `crs` | Rechengitter: Affine-Transformation (6 Werte) in EPSG:32632 |
| `szene`, `zeit_utc` | Szenen-ID und Aufnahmezeit |

Erneut auswerten, ohne Internet:
```bash
python rohdaten_auswerten.py daten/rohdaten_starnberger_see_2026-07-16.npz
```

Lesen in Python:
```python
import numpy as np
r = np.load("daten/rohdaten_starnberger_see_2026-07-16.npz", allow_pickle=False)
nir = r["nir"].astype("float32")
```

Quelle: enthält veränderte Copernicus-Sentinel-Daten 2026 (Contains modified Copernicus Sentinel data 2026), bezogen über Earth Search / AWS Open Data. Seeumriss: © OpenStreetMap-Mitwirkende (ODbL).
