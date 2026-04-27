"""
Combined ROI -> Niagara assets pipeline
========================================

Reads a single ROI GeoJSON, then in one run produces all the assets needed
to drive the Niagara fire-spread sim:

    1. Fetches the FastFuels tree inventory CSV for the ROI
       (TreeMap source, exported via the FastFuels API)
    2. Bakes the CSV into two 16-bit EXR textures sized to the sim grid:
         TreeData_RG.exr  (R = SPCD, G = DIA cm)
         TreeData_BA.exr  (R = HT m,  G = CR)
    3. Fetches Mapzen Terrarium elevation tiles for the same world rectangle
       and outputs a 16-bit PNG heightmap formatted for UE5 import.

All three outputs share the same world footprint: a square of side
`--sim-extent` meters, centered on the ROI's centroid, in EPSG:5070
(Albers Equal Area CONUS) so distances are physical meters.

Usage:
    python combined_pipeline.py roi.geojson out/ --sim-extent 512

The output directory will contain:
    tree_inventory_<domain_id>_5070.csv
    TreeData_RG.exr
    TreeData_BA.exr
    TreeData_meta.txt
    heightmap.png
    extent_meta.txt          <- canonical bbox shared by all outputs

Required Python packages:
    pip install numpy pandas geopandas shapely pyproj requests pillow OpenEXR

UE5 import settings:
    Tree EXRs:       sRGB OFF, Compression HDR, Filter Nearest, NoMipmaps, Clamp
    Heightmap PNG:   Imported as Landscape OR as Texture2D with sRGB OFF,
                     Compression HDR (Alpha), Filter Default, NoMipmaps
                     (treat the same way as your existing manticorp PNGs)
"""

import argparse
import csv
import io
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import requests

# ---- Geospatial deps (only needed for the FastFuels + projection paths) ----
try:
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point, Polygon, mapping, shape, box
    from pyproj import Transformer
    _HAS_GEO = True
except ImportError:
    _HAS_GEO = False

# ---- Image deps ----
from PIL import Image

# =====================================================================
# CONFIG
# =====================================================================

FASTFUELS_API_URL = "https://api.fastfuels.silvxlabs.com/"
FASTFUELS_API_KEY = "770a09d244dd45d38105dbaa0eb8023d"  # your personal key

# Mapzen / Nextzen Terrarium elevation tile endpoint (no auth, public).
# Each tile is 256x256 PNG; pixel RGB encodes elevation in meters via:
#     elevation = (R * 256 + G + B/256) - 32768
TERRARIUM_TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"

LIVE_ONLY = True  # filter dead trees from the FastFuels CSV by default


# =====================================================================
# 1. ROI / BBOX HANDLING
# =====================================================================

def load_roi_centroid_latlon(geojson_path):
    """Load the ROI GeoJSON and return (lon, lat) of the centroid."""
    gdf = gpd.read_file(geojson_path)
    
    # 1. Check if the GeoDataFrame is empty to prevent ufunc errors
    if gdf.empty:
        raise ValueError(f"The GeoJSON file '{geojson_path}' contains no features.")
        
    # 2. Filter out any null/missing geometries or strictly empty geometry collections
    gdf = gdf[gdf.geometry.notnull()]
    gdf = gdf[~gdf.geometry.is_empty]
    if gdf.empty:
        raise ValueError(f"The GeoJSON file '{geojson_path}' contains no valid geometries.")

    # Centroid in whatever CRS the file is in
    if gdf.crs is None:
        # Assume WGS84 if no CRS
        gdf = gdf.set_crs("EPSG:4326")
        
    # Reproject to a metric CRS for an accurate centroid, then back to 4326
    gdf_metric = gdf.to_crs("EPSG:5070")
    
    # 3. Bypass the Shapely union bug by finding the center of the total bounds
    minx, miny, maxx, maxy = gdf_metric.total_bounds
    cx_m = (minx + maxx) / 2.0
    cy_m = (miny + maxy) / 2.0
    
    # Convert centroid back to lat/lon
    transformer = Transformer.from_crs("EPSG:5070", "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(cx_m, cy_m)
    return lon, lat, cx_m, cy_m


def make_square_bbox_5070(cx_m, cy_m, extent_m):
    """Square bbox of side `extent_m` centered at (cx_m, cy_m) in EPSG:5070."""
    half = extent_m * 0.5
    return (cx_m - half, cy_m - half, cx_m + half, cy_m + half)


def bbox_5070_to_latlon_polygon(bbox_5070, n_per_side=8):
    """
    Convert a square EPSG:5070 bbox to a lat/lon polygon. Adds extra points
    along each edge so the resulting (curved-on-globe) polygon faithfully
    represents the metric square — straight lines in 5070 aren't straight
    in lat/lon over a few hundred meters, but it matters at the few-meter
    scale we care about.
    """
    xmin, ymin, xmax, ymax = bbox_5070
    transformer = Transformer.from_crs("EPSG:5070", "EPSG:4326", always_xy=True)

    pts_5070 = []
    # bottom edge L->R
    for i in range(n_per_side):
        t = i / n_per_side
        pts_5070.append((xmin + t * (xmax - xmin), ymin))
    # right edge B->T
    for i in range(n_per_side):
        t = i / n_per_side
        pts_5070.append((xmax, ymin + t * (ymax - ymin)))
    # top edge R->L
    for i in range(n_per_side):
        t = i / n_per_side
        pts_5070.append((xmax - t * (xmax - xmin), ymax))
    # left edge T->B
    for i in range(n_per_side):
        t = i / n_per_side
        pts_5070.append((xmin, ymax - t * (ymax - ymin)))
    pts_5070.append(pts_5070[0])  # close

    pts_latlon = [transformer.transform(x, y) for (x, y) in pts_5070]
    return Polygon(pts_latlon)


# =====================================================================
# 2. FASTFUELS CSV FETCH
# =====================================================================

def fetch_fastfuels_csv(roi_polygon_latlon, out_csv_path, api_key):
    """
    Submit the polygon to FastFuels, wait for the inventory + export to
    complete, download the CSV. Returns the domain_id.
    """
    headers = {"api-key": api_key}

    # Wrap polygon in a GeoJSON FeatureCollection (the API expects this)
    feature_collection = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": mapping(roi_polygon_latlon),
        }]
    }

    print("Submitting domain to FastFuels...")
    r = requests.post(FASTFUELS_API_URL + "v1/domains",
                      json=feature_collection, headers=headers)
    r.raise_for_status()
    domain_id = r.json()["id"]
    print(f"  domain_id = {domain_id}")

    print("Requesting tree inventory (TreeMap)...")
    r = requests.post(FASTFUELS_API_URL + f"v1/domains/{domain_id}/inventories/tree",
                      json={"sources": ["TreeMap"]}, headers=headers)
    r.raise_for_status()

    print("Polling inventory status...")
    while True:
        r = requests.get(FASTFUELS_API_URL + f"v1/domains/{domain_id}/inventories/tree/",
                         headers=headers)
        r.raise_for_status()
        status = r.json()["status"]
        if status == "completed":
            break
        if status == "failed":
            raise RuntimeError(f"FastFuels inventory failed: {r.json()}")
        print(f"  status={status} ... waiting")
        time.sleep(5)

    print("Requesting CSV export...")
    r = requests.post(FASTFUELS_API_URL + f"v1/domains/{domain_id}/inventories/tree/exports/csv",
                      headers=headers)
    r.raise_for_status()

    print("Polling export status...")
    signed_url = None
    while True:
        r = requests.get(FASTFUELS_API_URL + f"v1/domains/{domain_id}/inventories/tree/exports/csv/",
                         headers=headers)
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
    """
    Read the FastFuels CSV (X/Y in source_crs, default UTM 12N which is what
    FastFuels uses for a wide swath of the western US — but you should
    confirm for your specific ROI). Reproject X/Y to EPSG:5070 and write back.

    NOTE: FastFuels doesn't always return the same UTM zone — different ROIs
    yield different zones. We try to detect the right zone from the CSV
    coordinates' magnitude, falling back to user-specified.
    """
    df = pd.read_csv(in_csv_path).dropna()
    if "X" not in df.columns or "Y" not in df.columns:
        raise ValueError("CSV missing X/Y columns")

    # Heuristic: if Y values are 6-7 digits and X values are 5-6 digits,
    # we're in UTM. EPSG:5070 X is typically -2,000,000 to 2,500,000.
    # If X is already big-negative-7-digits, the data is probably already 5070.
    sample_x = df["X"].iloc[0]
    if sample_x < -1_000_000:
        print("  CSV appears to already be in EPSG:5070 — skipping reprojection")
        df.to_csv(out_csv_path, index=False)
        return

    print(f"  Reprojecting from {source_crs} to EPSG:5070...")
    gdf = gpd.GeoDataFrame(
        df,
        geometry=[Point(x, y) for x, y in zip(df["X"], df["Y"])],
        crs=source_crs,
    )
    gdf_5070 = gdf.to_crs("EPSG:5070")
    gdf_5070["X"] = gdf_5070.geometry.x
    gdf_5070["Y"] = gdf_5070.geometry.y
    gdf_5070.drop(columns="geometry").to_csv(out_csv_path, index=False)


def detect_csv_utm_zone(roi_centroid_lon):
    """Return the EPSG code for the UTM zone covering the given longitude."""
    zone = int((roi_centroid_lon + 180) / 6) + 1
    # Northern hemisphere only (CONUS use case)
    return f"EPSG:{32600 + zone}"


# =====================================================================
# 3. CSV -> TREE EXR BAKE (lifted from bake_fastfuels_to_exr_v2.py)
# =====================================================================

def parse_csv_5070(csv_path, live_only=True):
    with open(csv_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                status = int(float(row["STATUSCD"]))
                if live_only and status != 1:
                    continue
                yield (
                    float(row["X"]),
                    float(row["Y"]),
                    int(float(row["SPCD"])),
                    float(row["DIA"]),
                    float(row["HT"]),
                    float(row["CR"]),
                )
            except (ValueError, KeyError):
                continue


def rasterize_trees(trees, grid_res, bbox_5070):
    """Largest-tree-per-cell rasterization. Returns spcd, dia, ht, cr arrays."""
    xmin, ymin, xmax, ymax = bbox_5070
    width = xmax - xmin
    height = ymax - ymin

    spcd_grid = np.zeros((grid_res, grid_res), dtype=np.float32)
    dia_grid = np.zeros((grid_res, grid_res), dtype=np.float32)
    ht_grid = np.zeros((grid_res, grid_res), dtype=np.float32)
    cr_grid = np.zeros((grid_res, grid_res), dtype=np.float32)

    cell_w = width / grid_res
    cell_h = height / grid_res
    kept = 0
    dropped = 0
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
    print(f"  Cell size: {cell_w:.3f} x {cell_h:.3f} m")
    return spcd_grid, dia_grid, ht_grid, cr_grid


def write_exr_rgba(path, r, g, b, a):
    """Write a 4-channel half-float EXR via OpenEXR or OpenImageIO."""
    img = np.stack([r, g, b, a], axis=-1).astype(np.float16)
    h, w, _ = img.shape
    errors = []

    try:
        import OpenEXR
        import Imath
        header = OpenEXR.Header(w, h)
        half = Imath.PixelType(Imath.PixelType.HALF)
        header["channels"] = {
            "R": Imath.Channel(half),
            "G": Imath.Channel(half),
            "B": Imath.Channel(half),
            "A": Imath.Channel(half),
        }
        out = OpenEXR.OutputFile(str(path), header)
        out.writePixels({
            "R": img[..., 0].tobytes(),
            "G": img[..., 1].tobytes(),
            "B": img[..., 2].tobytes(),
            "A": img[..., 3].tobytes(),
        })
        out.close()
        return
    except Exception as e:
        errors.append(f"OpenEXR: {e}")

    try:
        import OpenImageIO as oiio
        spec = oiio.ImageSpec(w, h, 4, "half")
        out = oiio.ImageOutput.create(str(path))
        if out is None:
            raise RuntimeError("OIIO could not create EXR writer")
        out.open(str(path), spec)
        out.write_image(img)
        out.close()
        return
    except Exception as e:
        errors.append(f"OpenImageIO: {e}")

    raise RuntimeError(
        "Could not write EXR. Install OpenEXR or openimageio.\n"
        + "\n".join(errors)
    )


def bake_tree_exrs(csv_path, out_dir, grid_res, bbox_5070):
    print(f"Reading {csv_path}...")
    trees = list(parse_csv_5070(csv_path, live_only=LIVE_ONLY))
    print(f"  {len(trees)} live trees")

    if not trees:
        raise RuntimeError("No trees to rasterize.")

    print(f"Rasterizing to {grid_res}x{grid_res}...")
    spcd, dia, ht, cr = rasterize_trees(trees, grid_res, bbox_5070)

    # Flip vertically: row 0 should be top of texture in UE convention
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
    cell_w = (xmax - xmin) / grid_res
    cell_h = (ymax - ymin) / grid_res
    with open(meta_path, "w") as f:
        f.write(f"grid_res={grid_res}\n")
        f.write(f"xmin={xmin}\nymin={ymin}\nxmax={xmax}\nymax={ymax}\n")
        f.write(f"cell_w={cell_w}\ncell_h={cell_h}\n")
        f.write(f"width_m={xmax-xmin}\nheight_m={ymax-ymin}\n")
    return meta_path


# =====================================================================
# 4. HEIGHTMAP FETCH (Mapzen Terrarium tiles)
# =====================================================================

def latlon_to_tile_xy(lat, lon, zoom):
    """Slippy-map tile coords (float) for a lat/lon at zoom level."""
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def tile_xy_to_latlon(x, y, zoom):
    """Top-left lat/lon of the tile at integer (x, y, zoom)."""
    n = 2.0 ** zoom
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def decode_terrarium(rgb):
    """Decode Mapzen Terrarium RGB pixels to elevation in meters."""
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
    """
    Fetch the Terrarium tiles covering bbox_latlon, mosaic, decode to meters,
    crop precisely to bbox, resample to out_size_px x out_size_px, normalize
    to 16-bit, return (uint16_array, min_m, max_m).

    bbox_latlon : (south, west, north, east) in degrees
    zoom        : Terrarium zoom level (15 = ~5m/px at temperate latitudes)
    out_size_px : output square size, e.g. 256 to match the manticorp default
    normalize   : 'smart' | 'regular' | 'none' (matches Manticorp options)
    """
    south, west, north, east = bbox_latlon

    # Tile range covering the bbox (note tile y increases downward = southward)
    tx_min_f, ty_max_f = latlon_to_tile_xy(south, west, zoom)
    tx_max_f, ty_min_f = latlon_to_tile_xy(north, east, zoom)
    tx_min = int(math.floor(tx_min_f))
    tx_max = int(math.floor(tx_max_f))
    ty_min = int(math.floor(ty_min_f))
    ty_max = int(math.floor(ty_max_f))

    nx = tx_max - tx_min + 1
    ny = ty_max - ty_min + 1
    print(f"  Fetching {nx}x{ny} = {nx*ny} terrarium tiles at zoom {zoom}...")

    session = requests.Session()
    mosaic = np.zeros((ny * 256, nx * 256, 3), dtype=np.uint8)
    for ix, tx in enumerate(range(tx_min, tx_max + 1)):
        for iy, ty in enumerate(range(ty_min, ty_max + 1)):
            tile = fetch_terrarium_tile(tx, ty, zoom, session)
            mosaic[iy*256:(iy+1)*256, ix*256:(ix+1)*256, :] = tile

    # Decode entire mosaic to meters
    elev_m = decode_terrarium(mosaic)

    # Find pixel coords of our bbox within the mosaic.
    # Mosaic origin (top-left) is at (tx_min, ty_min) in tile space.
    # Our bbox NW corner = (north, west), SE corner = (south, east).
    nw_x_tile, nw_y_tile = latlon_to_tile_xy(north, west, zoom)
    se_x_tile, se_y_tile = latlon_to_tile_xy(south, east, zoom)
    px_left = (nw_x_tile - tx_min) * 256.0
    px_right = (se_x_tile - tx_min) * 256.0
    px_top = (nw_y_tile - ty_min) * 256.0
    px_bottom = (se_y_tile - ty_min) * 256.0

    # Crop with bilinear-ish (use array slicing on integer bounds, then
    # PIL resize the cropped block to the output size).
    crop_left = int(math.floor(px_left))
    crop_right = int(math.ceil(px_right))
    crop_top = int(math.floor(px_top))
    crop_bottom = int(math.ceil(px_bottom))
    crop_left = max(0, crop_left)
    crop_top = max(0, crop_top)
    crop_right = min(mosaic.shape[1], crop_right)
    crop_bottom = min(mosaic.shape[0], crop_bottom)

    cropped = elev_m[crop_top:crop_bottom, crop_left:crop_right]
    print(f"  Cropped elevation patch: {cropped.shape}, "
          f"range {cropped.min():.1f} - {cropped.max():.1f} m")

    # Resample to out_size_px x out_size_px using PIL's high-quality resizer.
    # We resample the float array via PIL by treating it as a 32F image.
    pil_img = Image.fromarray(cropped, mode="F")
    pil_img = pil_img.resize((out_size_px, out_size_px), Image.LANCZOS)
    elev_resampled = np.asarray(pil_img, dtype=np.float32)

    # Normalize to uint16
    if normalize == "none":
        # Clamp negatives to 0, store actual meters as integer (limits at 65535m)
        clamped = np.clip(elev_resampled, 0, 65535).astype(np.uint16)
        return clamped, float(elev_resampled.min()), float(elev_resampled.max())
    elif normalize == "regular":
        emin = float(elev_resampled.min())
        emax = float(elev_resampled.max())
    elif normalize == "smart":
        # 99.9% window, falls back to true min/max if they're close
        flat = elev_resampled.flatten()
        lo, hi = np.percentile(flat, [0.05, 99.95])
        true_min = float(flat.min())
        true_max = float(flat.max())
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
    """Write a 16-bit grayscale PNG suitable for UE5 import."""
    img = Image.fromarray(arr_u16, mode="I;16")
    img.save(str(path), format="PNG")


# =====================================================================
# 5. MAIN
# =====================================================================

def main():
    if not _HAS_GEO:
        print("ERROR: missing geopandas / shapely / pyproj.\n"
              "Install with: pip install geopandas shapely pyproj pandas",
              file=sys.stderr)
        sys.exit(1)

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roi", type=Path, help="ROI GeoJSON (lat/lon polygon)")
    ap.add_argument("out_dir", type=Path, help="Output directory")
    ap.add_argument("--sim-extent", type=float, default=512.0,
                    help="Square footprint side length in meters. Default 512.")
    ap.add_argument("--grid", type=int, default=512,
                    help="Tree EXR grid resolution (default 512 to match sim).")
    ap.add_argument("--hm-size", type=int, default=256,
                    help="Heightmap output size in pixels (default 256).")
    ap.add_argument("--hm-zoom", type=int, default=15,
                    help="Mapzen Terrarium zoom (default 15, ~5m/px temperate).")
    ap.add_argument("--hm-norm", choices=["smart", "regular", "none"],
                    default="smart", help="Heightmap normalization (default smart).")
    ap.add_argument("--skip-fastfuels", action="store_true",
                    help="Skip the FastFuels CSV fetch + tree EXR bake.")
    ap.add_argument("--skip-heightmap", action="store_true",
                    help="Skip the heightmap fetch.")
    ap.add_argument("--reuse-csv", type=Path, default=None,
                    help="Skip CSV download, use this existing CSV instead.")
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1. Compute canonical bbox ----
    print("=" * 60)
    print("STEP 1: Compute canonical bbox")
    print("=" * 60)
    lon, lat, cx_m, cy_m = load_roi_centroid_latlon(args.roi)
    print(f"  ROI centroid: lat={lat:.6f}, lon={lon:.6f}")
    print(f"  EPSG:5070 centroid: ({cx_m:.2f}, {cy_m:.2f})")
    bbox_5070 = make_square_bbox_5070(cx_m, cy_m, args.sim_extent)
    print(f"  EPSG:5070 bbox ({args.sim_extent}m square): {bbox_5070}")

    # Compute lat/lon corners (for both heightmap fetch and FastFuels query)
    poly_latlon = bbox_5070_to_latlon_polygon(bbox_5070, n_per_side=8)
    minx_ll, miny_ll, maxx_ll, maxy_ll = poly_latlon.bounds
    bbox_latlon = (miny_ll, minx_ll, maxy_ll, maxx_ll)  # S, W, N, E
    print(f"  Lat/lon bounds: S={miny_ll:.6f} W={minx_ll:.6f} N={maxy_ll:.6f} E={maxx_ll:.6f}")

    # Write extent metadata BEFORE doing any I/O so it's there even if
    # downstream steps fail.
    extent_meta_path = args.out_dir / "extent_meta.txt"
    with open(extent_meta_path, "w") as f:
        f.write(f"sim_extent_m={args.sim_extent}\n")
        f.write(f"centroid_lat={lat}\ncentroid_lon={lon}\n")
        f.write(f"epsg5070_cx={cx_m}\nepsg5070_cy={cy_m}\n")
        f.write(f"epsg5070_xmin={bbox_5070[0]}\nepsg5070_ymin={bbox_5070[1]}\n")
        f.write(f"epsg5070_xmax={bbox_5070[2]}\nepsg5070_ymax={bbox_5070[3]}\n")
        f.write(f"latlon_S={miny_ll}\nlatlon_W={minx_ll}\n")
        f.write(f"latlon_N={maxy_ll}\nlatlon_E={maxx_ll}\n")
    print(f"  Wrote {extent_meta_path}")

    # ---- 2. FastFuels CSV ----
    csv_5070_path = args.out_dir / "tree_inventory_5070.csv"
    if not args.skip_fastfuels:
        print()
        print("=" * 60)
        print("STEP 2: FastFuels tree inventory")
        print("=" * 60)
        if args.reuse_csv:
            print(f"  Reusing existing CSV: {args.reuse_csv}")
            csv_raw_path = args.reuse_csv
        else:
            csv_raw_path = args.out_dir / "tree_inventory_raw.csv"
            
            # Create a simple 4-corner bounding box polygon for the FastFuels request
            # using the previously computed min/max lat/lon extents.
            ff_bbox_poly = box(minx_ll, miny_ll, maxx_ll, maxy_ll)
            fetch_fastfuels_csv(ff_bbox_poly, csv_raw_path, FASTFUELS_API_KEY)

        print(f"Reprojecting CSV -> EPSG:5070...")
        utm_crs = detect_csv_utm_zone(lon)
        print(f"  Assumed source CRS: {utm_crs}")
        reproject_csv_to_5070(csv_raw_path, csv_5070_path, source_crs=utm_crs)

        print()
        print("=" * 60)
        print("STEP 3: Bake tree EXRs")
        print("=" * 60)
        bake_tree_exrs(csv_5070_path, args.out_dir, args.grid, bbox_5070)

    # ---- 3. Heightmap ----
    if not args.skip_heightmap:
        print()
        print("=" * 60)
        print("STEP 4: Fetch heightmap")
        print("=" * 60)
        arr, emin, emax = build_heightmap(
            bbox_latlon, args.hm_zoom, args.hm_size, normalize=args.hm_norm)
        hm_path = args.out_dir / "heightmap.png"
        write_heightmap_png(hm_path, arr)
        print(f"  Wrote {hm_path}")
        print(f"  Elevation range: {emin:.1f} - {emax:.1f} m "
              f"(span {emax-emin:.1f} m)")
        # Append heightmap metadata to extent_meta
        with open(extent_meta_path, "a") as f:
            f.write(f"hm_size_px={args.hm_size}\n")
            f.write(f"hm_zoom={args.hm_zoom}\n")
            f.write(f"hm_norm={args.hm_norm}\n")
            f.write(f"hm_min_m={emin}\nhm_max_m={emax}\n")
            f.write(f"hm_span_m={emax-emin}\n")

    print()
    print("=" * 60)
    print("DONE")
    print("=" * 60)
    print(f"All outputs in {args.out_dir}")


if __name__ == "__main__":
    main()