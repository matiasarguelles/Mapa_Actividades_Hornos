"""
generar_geojson.py
Lee el Google Sheet de respuestas del Forms, procesa cada registro,
recorta el tramo de calle usando el shapefile, y genera actividades_live.geojson.
Los registros que no pueden georreferenciarse se guardan en warnings.json.
"""

import os
import re
import json
import geopandas as gpd
import pandas as pd
import pyproj
from shapely.ops import unary_union, nearest_points, linemerge, substring, transform

# ── Configuración ─────────────────────────────────────────────────────────────
SHEET_ID  = "1h3fnfzn6xwm1LqNUiXTy8UxOus7khky7Mnujg3-3qnU"
SHEET_NAME = "Respuestas - Registro de Actividades Los Hornos"
SHP_PATH  = "scripts/Calles_Hornos.shp"
OUT_GEOJSON = "actividades_live.geojson"
OUT_WARNINGS = "warnings.json"
SHP_EPSG  = 5347   # POSGAR 2007 faja 5

# URL pública del Sheet como CSV (el Sheet debe estar publicado)
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSZbEDpkN-w7BkLUvmQguHCnH_wDlSlY5mYpluJ1Mez76YY7YU6hB63PTi_-BafvXidbGa68O6G07i/pub?gid=571653749&single=true&output=csv"

# ── Paleta de colores por actividad ───────────────────────────────────────────
COLOR_ACTIVIDAD = {
    "Barrido":     "#4d94ff",
    "Limpieza":    "#1a6fd4",
    "Pasto":       "#0055aa",
    "Recoleccion": "#00267a",
    "Alumbrado":   "#ffcc00",
}
COLOR_DEFAULT = "#888888"

# ── Normalización de nombres de calle ─────────────────────────────────────────
def normalizar(nombre):
    """Convierte '54 Calle' → 'Calle 54', '31 Avenida' → 'Avenida 31', etc."""
    if not isinstance(nombre, str) or not nombre.strip():
        return None
    nombre = nombre.strip()
    m = re.match(
        r'^(\d+)\s+(Bis\s+)?(Calle|Avenida|Boulevard|Ruta)$',
        nombre, re.IGNORECASE
    )
    if m:
        num  = m.group(1)
        bis  = (m.group(2) or "").strip()
        tipo = m.group(3).capitalize()
        return f"{tipo} {num}{' Bis' if bis else ''}"
    return nombre

# ── Carga del shapefile ───────────────────────────────────────────────────────
def cargar_shape(path):
    gdf = gpd.read_file(path)
    gdf = gdf.set_crs(epsg=SHP_EPSG)
    return gdf

# ── Recorte de tramo ──────────────────────────────────────────────────────────
proj_to_wgs = pyproj.Transformer.from_crs(SHP_EPSG, 4326, always_xy=True)

def get_tramo(calle, desde, hasta, gdf):
    """
    Devuelve (geometría WGS84, None) o (None, mensaje_warning).
    """
    geom_p = unary_union(gdf[gdf["name"] == calle].geometry)
    geom_h = unary_union(gdf[gdf["name"] == hasta].geometry)

    if geom_p.is_empty:
        return None, f"Calle principal no encontrada en shape: '{calle}'"
    if geom_h.is_empty:
        return None, f"Calle 'Hasta' no encontrada en shape: '{hasta}'"

    # Mergear segmentos de la calle principal
    if geom_p.geom_type == "MultiLineString":
        merged = linemerge(geom_p)
        if merged.geom_type == "MultiLineString":
            # Elegir el segmento más cercano a la calle Hasta
            segs = list(merged.geoms)
            geom_p = min(segs, key=lambda s: s.distance(geom_h))
        else:
            geom_p = merged

    # Distancia mínima entre calle principal y Hasta: si > 1000m → warning
    dist_ph = geom_p.distance(geom_h)
    if dist_ph > 1000:
        return None, (
            f"Calles no se cruzan ni son adyacentes: "
            f"'{calle}' y '{hasta}' están a {dist_ph:.0f}m entre sí"
        )

    # Punto Desde
    if desde == calle or desde is None:
        # Operario puso la misma calle como Desde → usar extremo más lejano al Hasta
        p_h, _ = nearest_points(geom_h, geom_p)
        dist_h = geom_p.project(p_h)
        dist_d = 0 if dist_h > geom_p.length / 2 else geom_p.length
    else:
        geom_d = unary_union(gdf[gdf["name"] == desde].geometry)
        if geom_d.is_empty:
            return None, f"Calle 'Desde' no encontrada en shape: '{desde}'"

        dist_pd = geom_p.distance(geom_d)
        if dist_pd > 1000:
            return None, (
                f"Calles no se cruzan ni son adyacentes: "
                f"'{calle}' y '{desde}' están a {dist_pd:.0f}m entre sí"
            )

        p_d, _ = nearest_points(geom_d, geom_p)
        p_h, _ = nearest_points(geom_h, geom_p)
        dist_d = geom_p.project(p_d)
        dist_h = geom_p.project(p_h)

    if abs(dist_d - dist_h) < 1:
        return None, (
            f"Desde '{desde}' y Hasta '{hasta}' proyectan al mismo punto "
            f"sobre '{calle}' — puede ser un error de carga"
        )

    d1, d2 = min(dist_d, dist_h), max(dist_d, dist_h)

    try:
        seg     = substring(geom_p, d1, d2)
        seg_wgs = transform(proj_to_wgs.transform, seg)
        return seg_wgs, None
    except Exception as e:
        return None, f"Error al recortar tramo: {e}"

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Cargando shapefile...")
    gdf = cargar_shape(SHP_PATH)

    print("Descargando datos del Sheet...")
    try:
        df = pd.read_csv(SHEET_CSV_URL)
    except Exception as e:
        print(f"ERROR al leer el Sheet: {e}")
        raise

    print(f"  {len(df)} registros encontrados")

    features  = []
    warnings  = []

    for i, row in df.iterrows():
        ts       = str(row.get("Timestamp", ""))
        actividad = str(row.get("Actividad", "")).strip()

        # Calle: primero la del desplegable, si es "Otro" usar la manual
        calle_raw = row.get("Calle", "")
        if str(calle_raw).strip().lower() in ("otro", "other", "nan", ""):
            calle_raw = row.get('Calle (si eligió "Otro", escribila acá)', "")

        desde_raw = row.get("Desde (intersección inicial)", "")
        if str(desde_raw).strip().lower() in ("otro", "other", "nan", ""):
            desde_raw = row.get('Desde (si eligió "Otro", escribila acá)', "")

        hasta_raw = row.get("Hasta (intersección final)", "")
        if str(hasta_raw).strip().lower() in ("otro", "other", "nan", ""):
            hasta_raw = row.get('Hasta (si eligió "Otro", escribila acá)', "")

        calle = normalizar(calle_raw)
        desde = normalizar(desde_raw)
        hasta = normalizar(hasta_raw)

        if not calle or not hasta:
            warnings.append({
                "fila": i + 2,
                "timestamp": ts,
                "calle": str(calle_raw),
                "desde": str(desde_raw),
                "hasta": str(hasta_raw),
                "actividad": actividad,
                "motivo": "Calle o Hasta vacíos — registro incompleto"
            })
            continue

        seg, err = get_tramo(calle, desde, hasta, gdf)

        if err:
            warnings.append({
                "fila": i + 2,
                "timestamp": ts,
                "calle": calle,
                "desde": desde,
                "hasta": hasta,
                "actividad": actividad,
                "motivo": err
            })
            print(f"  WARNING fila {i+2}: {err}")
        else:
            features.append({
                "type": "Feature",
                "geometry": seg.__geo_interface__,
                "properties": {
                    "calle":     calle,
                    "desde":     desde,
                    "hasta":     hasta,
                    "actividad": actividad,
                    "timestamp": ts,
                    "color":     COLOR_ACTIVIDAD.get(actividad, COLOR_DEFAULT)
                }
            })

    geojson = {
        "type": "FeatureCollection",
        "features": features
    }

    with open(OUT_GEOJSON, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)

    with open(OUT_WARNINGS, "w", encoding="utf-8") as f:
        json.dump(warnings, f, ensure_ascii=False, indent=2)

    print(f"\nListo: {len(features)} features OK, {len(warnings)} warnings")
    print(f"  → {OUT_GEOJSON}")
    print(f"  → {OUT_WARNINGS}")

if __name__ == "__main__":
    main()
