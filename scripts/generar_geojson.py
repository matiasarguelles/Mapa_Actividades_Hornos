"""
generar_geojson.py
Lee el Google Sheet de respuestas del Forms, procesa cada registro,
recorta el tramo de calle usando el shapefile, y genera actividades_live.geojson.
Los registros que no pueden georreferenciarse se guardan en warnings.json.
"""

import re
import json
import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union, nearest_points, linemerge, substring
from shapely.geometry import mapping

# ── Configuración ─────────────────────────────────────────────────────────────
SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1vSZbEDpkN--w7BkLUvmQguHCnH_wDlSIY5mYpluJ1Mez76YY7YU6hB63PTi_-BafvXidbGa6806G07i/pub?gid=571653749&single=true&output=csv"
SHP_PATH      = "scripts/Calles_Hornos.shp"
OUT_GEOJSON   = "actividades_live.geojson"
OUT_WARNINGS  = "warnings.json"
SHP_EPSG      = 5347

COLOR_ACTIVIDAD = {
    "Barrido":     "#4d94ff",
    "Limpieza":    "#1a6fd4",
    "Pasto":       "#0055aa",
    "Recoleccion": "#00267a",
    "Alumbrado":   "#ffcc00",
}
COLOR_DEFAULT = "#888888"

# ── Normalización de nombres ───────────────────────────────────────────────────
def normalizar(nombre):
    if not isinstance(nombre, str) or not nombre.strip():
        return None
    nombre = nombre.strip()
    m = re.match(r'^(\d+)\s+(Bis\s+)?(Calle|Avenida|Boulevard|Ruta)$', nombre, re.IGNORECASE)
    if m:
        num  = m.group(1)
        bis  = (m.group(2) or "").strip()
        tipo = m.group(3).capitalize()
        return f"{tipo} {num}{' Bis' if bis else ''}"
    return nombre

# ── Recorte de tramo ──────────────────────────────────────────────────────────
def get_tramo(calle, desde, hasta, gdf_wgs, gdf_proj):
    geom_p = unary_union(gdf_proj[gdf_proj["name"] == calle].geometry)
    geom_h = unary_union(gdf_proj[gdf_proj["name"] == hasta].geometry)

    if geom_p.is_empty:
        return None, f"Calle principal no encontrada: '{calle}'"
    if geom_h.is_empty:
        return None, f"Calle 'Hasta' no encontrada: '{hasta}'"

    # Mergear segmentos
    if geom_p.geom_type == "MultiLineString":
        merged = linemerge(geom_p)
        if merged.geom_type == "MultiLineString":
            segs = list(merged.geoms)
            geom_p = min(segs, key=lambda s: s.distance(geom_h))
        else:
            geom_p = merged

    # Verificar proximidad
    dist_ph = geom_p.distance(geom_h)
    if dist_ph > 1000:
        return None, f"Calles no adyacentes: '{calle}' y '{hasta}' a {dist_ph:.0f}m"

    # Punto Desde
    if desde == calle or desde is None:
        p_h, _ = nearest_points(geom_h, geom_p)
        dist_h = geom_p.project(p_h)
        dist_d = 0 if dist_h > geom_p.length / 2 else geom_p.length
    else:
        geom_d = unary_union(gdf_proj[gdf_proj["name"] == desde].geometry)
        if geom_d.is_empty:
            return None, f"Calle 'Desde' no encontrada: '{desde}'"
        dist_pd = geom_p.distance(geom_d)
        if dist_pd > 1000:
            return None, f"Calles no adyacentes: '{calle}' y '{desde}' a {dist_pd:.0f}m"
        p_d, _ = nearest_points(geom_d, geom_p)
        p_h, _ = nearest_points(geom_h, geom_p)
        dist_d = geom_p.project(p_d)
        dist_h = geom_p.project(p_h)

    if abs(dist_d - dist_h) < 1:
        return None, f"Desde '{desde}' y Hasta '{hasta}' son el mismo punto"

    d1, d2 = min(dist_d, dist_h), max(dist_d, dist_h)

    try:
        seg_proj = substring(geom_p, d1, d2)
        # Reproyectar usando GeoPandas (más robusto)
        import geopandas as gpd2
        from shapely.geometry import shape
        gs = gpd.GeoSeries([seg_proj], crs=f"EPSG:{SHP_EPSG}")
        gs_wgs = gs.to_crs(epsg=4326)
        return gs_wgs.iloc[0], None
    except Exception as e:
        return None, f"Error al recortar tramo: {e}"

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Cargando shapefile...")
    gdf_proj = gpd.read_file(SHP_PATH).set_crs(epsg=SHP_EPSG)
    gdf_wgs  = gdf_proj.to_crs(epsg=4326)

    print("Descargando datos del Sheet...")
    try:
        df = pd.read_csv(SHEET_CSV_URL)
    except Exception as e:
        print(f"ERROR al leer el Sheet: {e}")
        raise

    print(f"  {len(df)} registros encontrados")

    features = []
    warnings = []

    for i, row in df.iterrows():
        ts       = str(row.get("Timestamp", ""))
        actividad = str(row.get("Actividad", "")).strip()

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
                "fila": i + 2, "timestamp": ts,
                "calle": str(calle_raw), "desde": str(desde_raw),
                "hasta": str(hasta_raw), "actividad": actividad,
                "motivo": "Calle o Hasta vacíos"
            })
            continue

        seg, err = get_tramo(calle, desde, hasta, gdf_wgs, gdf_proj)

        if err:
            warnings.append({
                "fila": i + 2, "timestamp": ts,
                "calle": calle, "desde": desde,
                "hasta": hasta, "actividad": actividad,
                "motivo": err
            })
            print(f"  WARNING fila {i+2}: {err}")
        else:
            features.append({
                "type": "Feature",
                "geometry": mapping(seg),
                "properties": {
                    "calle": calle, "desde": desde, "hasta": hasta,
                    "actividad": actividad, "timestamp": ts,
                    "color": COLOR_ACTIVIDAD.get(actividad, COLOR_DEFAULT)
                }
            })
            print(f"  OK fila {i+2}: {calle} | {desde} → {hasta}")

    geojson = {"type": "FeatureCollection", "features": features}

    with open(OUT_GEOJSON, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)

    with open(OUT_WARNINGS, "w", encoding="utf-8") as f:
        json.dump(warnings, f, ensure_ascii=False, indent=2)

    print(f"\nListo: {len(features)} features OK, {len(warnings)} warnings")

if __name__ == "__main__":
    main()
