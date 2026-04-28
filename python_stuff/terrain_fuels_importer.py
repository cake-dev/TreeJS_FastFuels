"""
Combined ROI -> Niagara assets pipeline + UE5 Importer
======================================================

Reads a single ROI GeoJSON, then in one run produces all the assets needed
to drive the Niagara fire-spread sim, and immediately imports them into Unreal Engine.

    1. Fetches the FastFuels tree inventory CSV for the ROI
    2. Bakes the CSV into two 16-bit EXR textures sized to the sim grid
    3. Fetches Mapzen Terrarium elevation tiles and outputs a 16-bit PNG heightmap.
    4. Automatically imports the resulting files into Unreal Engine with specific 
       texture properties applied.

Usage (CLI):
    python import_textures.py roi.geojson out/ --sim-extent 512 --ue-content-path /Game/Textures/ImportedData

Usage (Unreal Editor):
    Set RUN_IN_EDITOR = True below and execute the script directly in the Output Log.
"""

import argparse
import csv
import io
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import requests

# ---- Geospatial deps ----
try:
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point, Polygon, mapping, box
    from pyproj import Transformer
    _HAS_GEO = True
except ImportError:
    _HAS_GEO = False

# ---- Image deps ----
from PIL import Image

# ---- Unreal Engine deps ----
try:
    import unreal
    _HAS_UNREAL = True
except ImportError:
    _HAS_UNREAL = False


# =====================================================================
# CONFIGURATION
# =====================================================================

FASTFUELS_API_URL = "https://api.fastfuels.silvxlabs.com/"
FASTFUELS_API_KEY = "770a09d244dd45d38105dbaa0eb8023d"  # your personal key
TERRARIUM_TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
LIVE_ONLY = True

# --- UNREAL EDITOR OVERRIDES ---
# If running directly inside the Unreal Engine editor (not from CLI),
# set this to True and configure the paths below.
RUN_IN_EDITOR = True
EDITOR_ROI_PATH = "C:\\Users\\bovam\\Documents\\Code\\Thesis\\TreeJS_FastFuels\\python_stuff\\roi2.geojson"
EDITOR_OUT_DIR = "C:\\Users\\bovam\\Documents\\Code\\Thesis\\TreeJS_FastFuels\\python_stuff\\out_editor"
EDITOR_UE_CONTENT_PATH = "/Game"
EDITOR_SIM_EXTENT = 512.0
EDITOR_GRID_RES = 512
EDITOR_HM_SIZE = 256
EDITOR_HM_ZOOM = 15
EDITOR_HM_NORM = "smart"


# =====================================================================
# 1. ROI / BBOX HANDLING
# =====================================================================

def load_roi_centroid_latlon(geojson_path):
    gdf = gpd.read_file(geojson_path)
    
    if gdf.empty:
        raise ValueError(f"The GeoJSON file '{geojson_path}' contains no features.")
        
    gdf = gdf[gdf.geometry.notnull()]
    gdf = gdf[~gdf.geometry.is_empty]
    if gdf.empty:
        raise ValueError(f"The GeoJSON file '{geojson_path}' contains no valid geometries.")

    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
        
    gdf_metric = gdf.to_crs("EPSG:5070")
    minx, miny, maxx, maxy = gdf_metric.total_bounds
    cx_m = (minx + maxx) / 2.0
    cy_m = (miny + maxy) / 2.0
    
    transformer = Transformer.from_crs("EPSG:5070", "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(cx_m, cy_m)
    return lon, lat, cx_m, cy_m

def make_square_bbox_5070(cx_m, cy_m, extent_m):
    half = extent_m * 0.5
    return (cx_m - half, cy_m - half, cx_m + half, cy_m + half)

def bbox_5070_to_latlon_polygon(bbox_5070, n_per_side=8):
    xmin, ymin, xmax, ymax = bbox_5070
    transformer = Transformer.from_crs("EPSG:5070", "EPSG:4326", always_xy=True)

    pts_5070 = []
    for i in range(n_per_side):
        pts_5070.append((xmin + (i / n_per_side) * (xmax - xmin), ymin))
    for i in range(n_per_side):
        pts_5070.append((xmax, ymin + (i / n_per_side) * (ymax - ymin)))
    for i in range(n_per_side):
        pts_5070.append((xmax - (i / n_per_side) * (xmax - xmin), ymax))
    for i in range(n_per_side):
        pts_5070.append((xmin, ymax - (i / n_per_side) * (ymax - ymin)))
    pts_5070.append(pts_5070[0])

    pts_latlon = [transformer.transform(x, y) for (x, y) in pts_5070]
    return Polygon(pts_latlon)


# =====================================================================
# 2. FASTFUELS CSV FETCH
# =====================================================================

def fetch_fastfuels_csv(roi_polygon_latlon, out_csv_path, api_key):
    headers = {"api-key": api_key}
    feature_collection = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": mapping(roi_polygon_latlon),
        }]
    }

    print("Submitting domain to FastFuels...")
    r = requests.post(FASTFUELS_API_URL + "v1/domains", json=feature_collection, headers=headers)
    r.raise_for_status()
    domain_id = r.json()["id"]
    print(f"  domain_id = {domain_id}")

    print("Requesting tree inventory (TreeMap)...")
    r = requests.post(FASTFUELS_API_URL + f"v1/domains/{domain_id}/inventories/tree", json={"sources": ["TreeMap"]}, headers=headers)
    r.raise_for_status()

    print("Polling inventory status...")
    while True:
        r = requests.get(FASTFUELS_API_URL + f"v1/domains/{domain_id}/inventories/tree/", headers=headers)
        r.raise_for_status()
        status = r.json()["status"]
        if status == "completed": break
        if status == "failed": raise RuntimeError(f"FastFuels inventory failed: {r.json()}")
        print(f"  status={status} ... waiting")
        time.sleep(5)

    print("Requesting CSV export...")
    r = requests.post(FASTFUELS_API_URL + f"v1/domains/{domain_id}/inventories/tree/exports/csv", headers=headers)
    r.raise_for_status()

    print("Polling export status...")
    signed_url = None
    while True:
        r = requests.get(FASTFUELS_API_URL + f"v1/domains/{domain_id}/inventories/tree/exports/csv/", headers=headers)
        r.raise_for_status()
        rj = r.json()
        if rj["status"] == "completed":
            signed_url = rj["signedUrl"]
            break
        if rj["status"] == "failed":
            raise RuntimeError(f"FastFuels export failed: {rj}")
        print(f"  status={rj['status']} ... waiting")
        time.sleep(5)

    print(f"Downloading CSV from signed URL...")
    r = requests.get(signed_url)
    r.raise_for_status()
    out_csv_path.write_bytes(r.content)
    print(f"  wrote {out_csv_path}")
    return domain_id

def reproject_csv_to_5070(in_csv_path, out_csv_path, source_crs="EPSG:32612"):
    df = pd.read_csv(in_csv_path).dropna()
    if "X" not in df.columns or "Y" not in df.columns:
        raise ValueError("CSV missing X/Y columns")

    sample_x = df["X"].iloc[0]
    if sample_x < -1_000_000:
        print("  CSV appears to already be in EPSG:5070 — skipping reprojection")
        df.to_csv(out_csv_path, index=False)
        return

    print(f"  Reprojecting from {source_crs} to EPSG:5070...")
    gdf = gpd.GeoDataFrame(df, geometry=[Point(x, y) for x, y in zip(df["X"], df["Y"])], crs=source_crs)
    gdf_5070 = gdf.to_crs("EPSG:5070")
    gdf_5070["X"] = gdf_5070.geometry.x
    gdf_5070["Y"] = gdf_5070.geometry.y
    gdf_5070.drop(columns="geometry").to_csv(out_csv_path, index=False)

def detect_csv_utm_zone(roi_centroid_lon):
    zone = int((roi_centroid_lon + 180) / 6) + 1
    return f"EPSG:{32600 + zone}"


# =====================================================================
# 3. CSV -> TREE EXR BAKE
# =====================================================================

def parse_csv_5070(csv_path, live_only=True):
    with open(csv_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                status = int(float(row["STATUSCD"]))
                if live_only and status != 1: continue
                yield (float(row["X"]), float(row["Y"]), int(float(row["SPCD"])), float(row["DIA"]), float(row["HT"]), float(row["CR"]))
            except (ValueError, KeyError):
                continue

def rasterize_trees(trees, grid_res, bbox_5070):
    xmin, ymin, xmax, ymax = bbox_5070
    width = xmax - xmin
    height = ymax - ymin

    spcd_grid = np.zeros((grid_res, grid_res), dtype=np.float32)
    dia_grid = np.zeros((grid_res, grid_res), dtype=np.float32)
    ht_grid = np.zeros((grid_res, grid_res), dtype=np.float32)
    cr_grid = np.zeros((grid_res, grid_res), dtype=np.float32)

    cell_w = width / grid_res
    cell_h = height / grid_res
    kept = dropped = 0
    
    for x, y, spcd, dia, ht, cr in trees:
        col = int((x - xmin) / cell_w)
        row = int((y - ymin) / cell_h)
        if col < 0 or col >= grid_res or row < 0 or row >= grid_res:
            dropped += 1
            continue
        if dia > dia_grid[row, col]:
            spcd_grid[row, col] = float(spcd)
            dia_grid[row, col] = dia
            ht_grid[row, col] = ht
            cr_grid[row, col] = cr
            kept += 1

    occupied = int(np.count_nonzero(dia_grid))
    total = grid_res * grid_res
    print(f"  Rasterized: {kept} cell-updates, {dropped} trees outside bbox")
    print(f"  Occupancy: {occupied}/{total} cells ({100.0*occupied/total:.1f}%)")
    return spcd_grid, dia_grid, ht_grid, cr_grid

def write_exr_rgba(path, r, g, b, a):
    img = np.stack([r, g, b, a], axis=-1).astype(np.float16)
    h, w, _ = img.shape
    errors = []

    try:
        import OpenEXR
        import Imath
        header = OpenEXR.Header(w, h)
        half = Imath.PixelType(Imath.PixelType.HALF)
        header["channels"] = {"R": Imath.Channel(half), "G": Imath.Channel(half), "B": Imath.Channel(half), "A": Imath.Channel(half)}
        out = OpenEXR.OutputFile(str(path), header)
        out.writePixels({"R": img[..., 0].tobytes(), "G": img[..., 1].tobytes(), "B": img[..., 2].tobytes(), "A": img[..., 3].tobytes()})
        out.close()
        return
    except Exception as e:
        errors.append(f"OpenEXR: {e}")

    try:
        import OpenImageIO as oiio
        spec = oiio.ImageSpec(w, h, 4, "half")
        out = oiio.ImageOutput.create(str(path))
        if out is None: raise RuntimeError("OIIO could not create EXR writer")
        out.open(str(path), spec)
        out.write_image(img)
        out.close()
        return
    except Exception as e:
        errors.append(f"OpenImageIO: {e}")

    raise RuntimeError("Could not write EXR. Install OpenEXR or openimageio.\n" + "\n".join(errors))

def bake_tree_exrs(csv_path, out_dir, grid_res, bbox_5070):
    print(f"Reading {csv_path}...")
    trees = list(parse_csv_5070(csv_path, live_only=LIVE_ONLY))
    print(f"  {len(trees)} live trees")

    if not trees: raise RuntimeError("No trees to rasterize.")

    print(f"Rasterizing to {grid_res}x{grid_res}...")
    spcd, dia, ht, cr = rasterize_trees(trees, grid_res, bbox_5070)

    spcd = np.flipud(spcd)
    dia = np.flipud(dia)
    ht = np.flipud(ht)
    cr = np.flipud(cr)
    mask = (dia > 0.0).astype(np.float32)

    out_rg = out_dir / "TreeData_RG.exr"
    out_ba = out_dir / "TreeData_BA.exr"
    print(f"Writing {out_rg}...")
    write_exr_rgba(out_rg, spcd, dia, np.zeros_like(spcd), mask)
    print(f"Writing {out_ba}...")
    write_exr_rgba(out_ba, ht, cr, np.zeros_like(ht), mask)

    meta_path = out_dir / "TreeData_meta.txt"
    xmin, ymin, xmax, ymax = bbox_5070
    with open(meta_path, "w") as f:
        f.write(f"grid_res={grid_res}\nxmin={xmin}\nymin={ymin}\nxmax={xmax}\nymax={ymax}\n")
        f.write(f"cell_w={(xmax - xmin) / grid_res}\ncell_h={(ymax - ymin) / grid_res}\n")
        f.write(f"width_m={xmax-xmin}\nheight_m={ymax-ymin}\n")
    return meta_path


# =====================================================================
# 4. HEIGHTMAP FETCH
# =====================================================================

def latlon_to_tile_xy(lat, lon, zoom):
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y

def decode_terrarium(rgb):
    r = rgb[..., 0].astype(np.float32)
    g = rgb[..., 1].astype(np.float32)
    b = rgb[..., 2].astype(np.float32)
    return (r * 256.0 + g + b / 256.0) - 32768.0

def fetch_terrarium_tile(x, y, z, session):
    url = TERRARIUM_TILE_URL.format(x=x, y=y, z=z)
    r = session.get(url, timeout=30)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    return np.asarray(img)

def build_heightmap(bbox_latlon, zoom, out_size_px, normalize="smart"):
    south, west, north, east = bbox_latlon

    tx_min_f, ty_max_f = latlon_to_tile_xy(south, west, zoom)
    tx_max_f, ty_min_f = latlon_to_tile_xy(north, east, zoom)
    tx_min, tx_max = int(math.floor(tx_min_f)), int(math.floor(tx_max_f))
    ty_min, ty_max = int(math.floor(ty_min_f)), int(math.floor(ty_max_f))

    nx, ny = tx_max - tx_min + 1, ty_max - ty_min + 1
    print(f"  Fetching {nx}x{ny} = {nx*ny} terrarium tiles at zoom {zoom}...")

    session = requests.Session()
    mosaic = np.zeros((ny * 256, nx * 256, 3), dtype=np.uint8)
    for ix, tx in enumerate(range(tx_min, tx_max + 1)):
        for iy, ty in enumerate(range(ty_min, ty_max + 1)):
            tile = fetch_terrarium_tile(tx, ty, zoom, session)
            mosaic[iy*256:(iy+1)*256, ix*256:(ix+1)*256, :] = tile

    elev_m = decode_terrarium(mosaic)

    nw_x_tile, nw_y_tile = latlon_to_tile_xy(north, west, zoom)
    se_x_tile, se_y_tile = latlon_to_tile_xy(south, east, zoom)
    px_left, px_right = (nw_x_tile - tx_min) * 256.0, (se_x_tile - tx_min) * 256.0
    px_top, px_bottom = (nw_y_tile - ty_min) * 256.0, (se_y_tile - ty_min) * 256.0

    crop_left = max(0, int(math.floor(px_left)))
    crop_right = min(mosaic.shape[1], int(math.ceil(px_right)))
    crop_top = max(0, int(math.floor(px_top)))
    crop_bottom = min(mosaic.shape[0], int(math.ceil(px_bottom)))

    cropped = elev_m[crop_top:crop_bottom, crop_left:crop_right]
    
    pil_img = Image.fromarray(cropped, mode="F")
    pil_img = pil_img.resize((out_size_px, out_size_px), Image.LANCZOS)
    elev_resampled = np.asarray(pil_img, dtype=np.float32)

    if normalize == "none":
        clamped = np.clip(elev_resampled, 0, 65535).astype(np.uint16)
        return clamped, float(elev_resampled.min()), float(elev_resampled.max())
    elif normalize == "regular":
        emin, emax = float(elev_resampled.min()), float(elev_resampled.max())
    elif normalize == "smart":
        flat = elev_resampled.flatten()
        lo, hi = np.percentile(flat, [0.05, 99.95])
        true_min, true_max = float(flat.min()), float(flat.max())
        std = float(np.std(flat))
        emin = true_min if abs(true_min - lo) < std else float(lo)
        emax = true_max if abs(true_max - hi) < std else float(hi)
    else:
        raise ValueError(f"unknown normalize mode: {normalize}")

    span = max(emax - emin, 1e-6)
    norm = np.clip((elev_resampled - emin) / span, 0.0, 1.0)
    arr_u16 = (norm * 65535.0 + 0.5).astype(np.uint16)
    return arr_u16, emin, emax

def write_heightmap_png(path, arr_u16):
    img = Image.fromarray(arr_u16, mode="I;16")
    img.save(str(path), format="PNG")


# =====================================================================
# 5. UE5 IMPORT PIPELINE
# =====================================================================

def import_and_configure_textures(source_dir, dest_path):
    if not _HAS_UNREAL:
        print("Unreal Engine environment not detected. Skipping import.")
        return

    unreal.log("--- Starting UE5 Texture Import ---")

    try:
        asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    except Exception as e:
        unreal.log_error(f"Failed to get AssetTools: {e}")
        return

    # Define the files and their specific import settings inside scope to 
    # prevent errors if `unreal` module isn't loaded externally
    IMPORT_CONFIG = {
        "heightmap.png": {
            "mip_gen_settings": unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS,
            "compression_settings": unreal.TextureCompressionSettings.TC_GRAYSCALE,
            "srgb": False,
            "address_x": unreal.TextureAddress.TA_CLAMP,
            "address_y": unreal.TextureAddress.TA_CLAMP,
            "filter": unreal.TextureFilter.TF_BILINEAR
        },
        "TreeData_BA.exr": {
            "mip_gen_settings": unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS,
            "compression_settings": unreal.TextureCompressionSettings.TC_HDR,
            "srgb": False,
            "address_x": unreal.TextureAddress.TA_CLAMP,
            "address_y": unreal.TextureAddress.TA_CLAMP,
            "filter": unreal.TextureFilter.TF_NEAREST
        },
        "TreeData_RG.exr": {
            "mip_gen_settings": unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS,
            "compression_settings": unreal.TextureCompressionSettings.TC_HDR,
            "srgb": False,
            "address_x": unreal.TextureAddress.TA_CLAMP,
            "address_y": unreal.TextureAddress.TA_CLAMP,
            "filter": unreal.TextureFilter.TF_NEAREST
        }
    }

    tasks = []
    task_to_filename = {}

    for filename in IMPORT_CONFIG.keys():
        full_path = os.path.join(source_dir, filename)
        if not os.path.isfile(full_path):
            unreal.log_warning(f"File not found, skipping UE5 import: {full_path}")
            continue
            
        task = unreal.AssetImportTask()
        task.set_editor_property('automated', True)
        task.set_editor_property('destination_name', '')
        task.set_editor_property('destination_path', dest_path)
        task.set_editor_property('filename', full_path)
        task.set_editor_property('replace_existing', True)
        task.set_editor_property('save', True)
        
        tasks.append(task)
        task_to_filename[task] = filename

    if not tasks:
        unreal.log_warning("No files found to import. Process aborted.")
        return

    unreal.log(f"Executing import for {len(tasks)} files...")
    asset_tools.import_asset_tasks(tasks)

    for task in tasks:
        imported_paths = task.get_editor_property('imported_object_paths')
        if not imported_paths:
            unreal.log_error(f"Import failed for {task.get_editor_property('filename')}")
            continue
            
        asset_path = imported_paths[0]
        texture_asset = unreal.EditorAssetLibrary.load_asset(asset_path)
        
        if texture_asset and isinstance(texture_asset, unreal.Texture2D):
            filename = task_to_filename[task]
            settings = IMPORT_CONFIG[filename]
            
            unreal.log(f"Applying settings to {filename}...")
            try:
                for property_name, property_value in settings.items():
                    texture_asset.set_editor_property(property_name, property_value)
                
                unreal.EditorAssetLibrary.save_asset(asset_path)
                unreal.log(f"Successfully configured and saved: {asset_path}")
            except Exception as e:
                unreal.log_error(f"Failed to apply settings to {texture_asset.get_name()}: {e}")
        else:
            unreal.log_error(f"Asset {asset_path} is not a Texture2D.")

    unreal.log("--- UE5 Texture Import Complete ---")


# =====================================================================
# 6. MAIN
# =====================================================================

def main():
    if not _HAS_GEO:
        print("ERROR: missing geopandas / shapely / pyproj.\n"
              "Install with: pip install geopandas shapely pyproj pandas",
              file=sys.stderr)
        sys.exit(1)

    # -------------------------------------------------------------
    # Setup Arguments/Variables based on Context (Editor vs CLI)
    # -------------------------------------------------------------
    if RUN_IN_EDITOR:
        print("Running in Editor Mode using hardcoded configurations...")
        roi_path = Path(EDITOR_ROI_PATH)
        out_dir = Path(EDITOR_OUT_DIR)
        sim_extent = EDITOR_SIM_EXTENT
        grid_res = EDITOR_GRID_RES
        hm_size = EDITOR_HM_SIZE
        hm_zoom = EDITOR_HM_ZOOM
        hm_norm = EDITOR_HM_NORM
        ue_content_path = EDITOR_UE_CONTENT_PATH
        skip_fastfuels = False
        skip_heightmap = False
        reuse_csv = None
    else:
        ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        ap.add_argument("roi", type=Path, help="ROI GeoJSON (lat/lon polygon)")
        ap.add_argument("out_dir", type=Path, help="Output directory")
        ap.add_argument("--sim-extent", type=float, default=512.0, help="Square footprint side length in meters. Default 512.")
        ap.add_argument("--grid", type=int, default=512, help="Tree EXR grid resolution (default 512 to match sim).")
        ap.add_argument("--hm-size", type=int, default=256, help="Heightmap output size in pixels (default 256).")
        ap.add_argument("--hm-zoom", type=int, default=15, help="Mapzen Terrarium zoom (default 15).")
        ap.add_argument("--hm-norm", choices=["smart", "regular", "none"], default="smart", help="Heightmap normalization.")
        ap.add_argument("--skip-fastfuels", action="store_true", help="Skip the FastFuels CSV fetch + tree EXR bake.")
        ap.add_argument("--skip-heightmap", action="store_true", help="Skip the heightmap fetch.")
        ap.add_argument("--reuse-csv", type=Path, default=None, help="Skip CSV download, use this existing CSV instead.")
        ap.add_argument("--ue-content-path", type=str, default="/Game/Textures/ImportedData", help="UE5 Target path for import")
        
        args = ap.parse_args()
        
        roi_path = args.roi
        out_dir = args.out_dir
        sim_extent = args.sim_extent
        grid_res = args.grid
        hm_size = args.hm_size
        hm_zoom = args.hm_zoom
        hm_norm = args.hm_norm
        ue_content_path = args.ue_content_path
        skip_fastfuels = args.skip_fastfuels
        skip_heightmap = args.skip_heightmap
        reuse_csv = args.reuse_csv

    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1. Compute canonical bbox ----
    print("=" * 60)
    print("STEP 1: Compute canonical bbox")
    print("=" * 60)
    lon, lat, cx_m, cy_m = load_roi_centroid_latlon(roi_path)
    print(f"  ROI centroid: lat={lat:.6f}, lon={lon:.6f}")
    bbox_5070 = make_square_bbox_5070(cx_m, cy_m, sim_extent)

    poly_latlon = bbox_5070_to_latlon_polygon(bbox_5070, n_per_side=8)
    minx_ll, miny_ll, maxx_ll, maxy_ll = poly_latlon.bounds
    bbox_latlon = (miny_ll, minx_ll, maxy_ll, maxx_ll)
    
    extent_meta_path = out_dir / "extent_meta.txt"
    with open(extent_meta_path, "w") as f:
        f.write(f"sim_extent_m={sim_extent}\ncentroid_lat={lat}\ncentroid_lon={lon}\n")
        f.write(f"epsg5070_cx={cx_m}\nepsg5070_cy={cy_m}\n")
        f.write(f"epsg5070_xmin={bbox_5070[0]}\nepsg5070_ymin={bbox_5070[1]}\n")
        f.write(f"epsg5070_xmax={bbox_5070[2]}\nepsg5070_ymax={bbox_5070[3]}\n")

    # ---- 2. FastFuels CSV ----
    csv_5070_path = out_dir / "tree_inventory_5070.csv"
    if not skip_fastfuels:
        print("\n" + "=" * 60 + "\nSTEP 2: FastFuels tree inventory\n" + "=" * 60)
        if reuse_csv:
            csv_raw_path = reuse_csv
        else:
            csv_raw_path = out_dir / "tree_inventory_raw.csv"
            ff_bbox_poly = box(minx_ll, miny_ll, maxx_ll, maxy_ll)
            fetch_fastfuels_csv(ff_bbox_poly, csv_raw_path, FASTFUELS_API_KEY)

        utm_crs = detect_csv_utm_zone(lon)
        reproject_csv_to_5070(csv_raw_path, csv_5070_path, source_crs=utm_crs)

        print("\n" + "=" * 60 + "\nSTEP 3: Bake tree EXRs\n" + "=" * 60)
        bake_tree_exrs(csv_5070_path, out_dir, grid_res, bbox_5070)

    # ---- 3. Heightmap ----
    if not skip_heightmap:
        print("\n" + "=" * 60 + "\nSTEP 4: Fetch heightmap\n" + "=" * 60)
        arr, emin, emax = build_heightmap(bbox_latlon, hm_zoom, hm_size, normalize=hm_norm)
        hm_path = out_dir / "heightmap.png"
        write_heightmap_png(hm_path, arr)
        print(f"  Wrote {hm_path}")
        
        with open(extent_meta_path, "a") as f:
            f.write(f"hm_size_px={hm_size}\nhm_zoom={hm_zoom}\nhm_norm={hm_norm}\n")
            f.write(f"hm_min_m={emin}\nhm_max_m={emax}\nhm_span_m={emax-emin}\n")

    print("\n" + "=" * 60 + "\nDATA GENERATION DONE\n" + "=" * 60)
    print(f"All outputs in {out_dir}")

    # ---- 4. UE5 Import ----
    if _HAS_UNREAL:
        print("\n" + "=" * 60 + "\nSTEP 5: Importing Data to Unreal Engine\n" + "=" * 60)
        import_and_configure_textures(out_dir.resolve().as_posix(), ue_content_path)


if __name__ == "__main__":
    main()