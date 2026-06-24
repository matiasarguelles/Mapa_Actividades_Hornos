# Mapa Actividades Hornos

Mapa interactivo de actividades municipales en Los Hornos, La Plata.
Se actualiza automáticamente con cada carga del formulario de operarios.

## Estructura del repo

```
├── index.html                          # Mapa Leaflet
├── actividades_live.geojson            # GeoJSON actualizado automáticamente
├── warnings.json                       # Registros que no pudieron georreferenciarse
├── scripts/
│   ├── generar_geojson.py              # Script Python principal
│   └── Calles_Hornos.shp + .dbf etc   # Shapefile de calles (subir manualmente)
├── .github/workflows/
│   └── actualizar_geojson.yml          # GitHub Actions workflow
└── apps_script.gs                      # Código para pegar en Apps Script (referencia)
```

## Setup inicial

### 1. Subir el shapefile de calles
Copiá los archivos del shapefile de calles a la carpeta `scripts/`:
- `Calles_Hornos.shp`
- `Calles_Hornos.dbf`
- `Calles_Hornos.shx`
- `Calles_Hornos.prj` (si existe)

### 2. Publicar el Google Sheet
En el Google Sheet de respuestas:
- Archivo → Compartir → Publicar en la web
- Elegir la hoja "Respuestas - Registro de Actividades Los Hornos"
- Formato: CSV
- Publicar

### 3. Configurar Apps Script
1. Abrí el Google Sheet de respuestas
2. Extensiones → Apps Script
3. Pegá el contenido de `apps_script.gs`
4. Reemplazá `PEGAR_TOKEN_AQUI` con tu Personal Access Token de GitHub
5. Guardá
6. Ejecutá la función `instalarTrigger` una sola vez
7. Aceptá los permisos

### 4. Activar GitHub Pages
- Settings del repo → Pages
- Source: Deploy from branch → main → / (root)

### 5. Habilitar workflow_dispatch en Actions
- Settings del repo → Actions → General
- Asegurate que "Allow all actions" esté habilitado

## Cómo funciona

1. Operario envía el Forms
2. Apps Script se activa automáticamente (`onFormSubmit`)
3. Apps Script llama a la API de GitHub para disparar el workflow
4. GitHub Actions ejecuta `generar_geojson.py`:
   - Descarga todos los registros del Sheet como CSV
   - Normaliza los nombres de calle
   - Recorta el tramo exacto usando el shapefile
   - Genera `actividades_live.geojson`
   - Los registros con error quedan en `warnings.json`
5. GitHub Actions hace commit del GeoJSON actualizado
6. GitHub Pages sirve el archivo nuevo
7. El mapa lo muestra en la capa "⚡ En curso (live)"

## Renovar el token de GitHub

El token de GitHub vence periódicamente. Cuando venza:
1. Generá uno nuevo en GitHub → Settings → Developer settings → Fine-grained tokens
2. Actualizá el valor de `GITHUB_TOKEN` en el Apps Script

## Revisar warnings

Los registros que no pudieron georreferenciarse se guardan en `warnings.json` con el motivo del error. Los casos más comunes son:
- Calle no encontrada en el shapefile (calle nueva o nombre incorrecto)
- Calles que no se cruzan (error de carga del operario)
- Registro incompleto (falta Calle o Hasta)
