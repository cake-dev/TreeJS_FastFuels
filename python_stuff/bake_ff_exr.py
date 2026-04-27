"""
FastFuels CSV -> Niagara GPU Texture Bake
==========================================

Reads a FastFuels tree-point CSV and produces two 16-bit float EXR textures
sized to your fire-spread grid (e.g. 512 x 512). For each grid cell, the
largest tree (by DIA) that falls within the cell's world-space footprint is
written. Empty cells are zeroed.

Output textures (both are half-float EXR, R+G channels used):
  TreeData_RG.exr  ->  R = SPCD (raw int value as float)
                       G = DIA  (cm, as reported in CSV)
  TreeData_BA.exr  ->  R = HT   (m)
                       G = CR   (0-1 crown ratio)

Alpha is written as 1.0 when a cell contains a tree, 0.0 otherwise, so the
shader can cheaply branch on "does this cell have a tree" by sampling either
texture's alpha.

Import notes for UE5:
  - Compression:       HDR (RGBA16F or RGB16F)  -- do NOT use sRGB
  - sRGB:              OFF
  - Mip Gen Settings:  NoMipmaps
  - Filter:            Nearest  (critical: we want exact per-cell values)
  - Address X/Y:       Clamp

CSV format expected (header row required):
  CHUNK_ID,TREE_ID,PLOT_ID,SPCD,STATUSCD,DIA,HT,CR,X,Y,ROW_CHUNK,COL_CHUNK,geometry

Only SPCD, DIA, HT, CR, X, Y are read. STATUSCD==1 (live) is kept by default;
change LIVE_ONLY if you want snags/dead trees too.

Usage:
  python bake_fastfuels_to_exr.py input.csv out_dir --grid 512
  python bake_fastfuels_to_exr.py input.csv out_dir --grid 512 --bbox xmin ymin xmax ymax
"""

import argparse
import csv
import os
import sys
from pathlib import Path

import numpy as np

# OpenEXR writing via OpenImageIO if present, otherwise via imageio's EXR plugin.
# imageio + imageio-ffmpeg is usually the easiest install path.
try:
    import imageio.v3 as iio
    _HAS_IIO = True
except ImportError:
    _HAS_IIO = False


LIVE_ONLY = True  # STATUSCD == 1 only


def parse_csv(csv_path, live_only=True):
    """Yield (x, y, spcd, dia, ht, cr) tuples from the CSV."""
    with open(csv_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        required = {"SPCD", "STATUSCD", "DIA", "HT", "CR", "X", "Y"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV missing required columns: {missing}")

        for row in reader:
            try:
                status = int(float(row["STATUSCD"]))
                if live_only and status != 1:
                    continue
                x = float(row["X"])
                y = float(row["Y"])
                spcd = int(float(row["SPCD"]))
                dia = float(row["DIA"])
                ht = float(row["HT"])
                cr = float(row["CR"])
            except (ValueError, KeyError):
                # Skip malformed rows silently; report count at the end if needed.
                continue
            yield x, y, spcd, dia, ht, cr


def compute_bbox(trees):
    """Compute axis-aligned bounding box of all tree points."""
    xs = [t[0] for t in trees]
    ys = [t[1] for t in trees]
    if not xs:
        raise ValueError("No trees parsed from CSV.")
    return min(xs), min(ys), max(xs), max(ys)


def rasterize(trees, grid_res, bbox, pad_frac=0.01):
    """
    Resolve largest-tree-per-cell on a grid_res x grid_res grid spanning bbox.

    Returns four arrays shaped (grid_res, grid_res) in [row, col] = [y, x]
    order, where row 0 is the SOUTH edge (min Y) to match typical UV.y=0 at
    bottom conventions. We'll flip vertically on write if needed to match
    your TerrainHeightRT orientation.

        spcd, dia, ht, cr  (all float32, zero where empty)
    """
    xmin, ymin, xmax, ymax = bbox
    # Small pad so trees exactly on the max edge don't fall out of the grid
    pad_x = (xmax - xmin) * pad_frac
    pad_y = (ymax - ymin) * pad_frac
    xmin -= pad_x
    ymin -= pad_y
    xmax += pad_x
    ymax += pad_y

    width = xmax - xmin
    height = ymax - ymin
    if width <= 0 or height <= 0:
        raise ValueError(f"Degenerate bbox: {bbox}")

    spcd_grid = np.zeros((grid_res, grid_res), dtype=np.float32)
    dia_grid = np.zeros((grid_res, grid_res), dtype=np.float32)
    ht_grid = np.zeros((grid_res, grid_res), dtype=np.float32)
    cr_grid = np.zeros((grid_res, grid_res), dtype=np.float32)

    cell_w = width / grid_res
    cell_h = height / grid_res

    kept = 0
    dropped = 0
    for x, y, spcd, dia, ht, cr in trees:
        # Map world -> grid cell
        col = int((x - xmin) / cell_w)
        row = int((y - ymin) / cell_h)
        if col < 0 or col >= grid_res or row < 0 or row >= grid_res:
            dropped += 1
            continue
        # Largest-wins by DIA
        if dia > dia_grid[row, col]:
            spcd_grid[row, col] = float(spcd)
            dia_grid[row, col] = dia
            ht_grid[row, col] = ht
            cr_grid[row, col] = cr
            kept += 1

    print(f"  Rasterized: {kept} cell-updates, {dropped} trees outside bbox")
    occupied = int(np.count_nonzero(dia_grid))
    total = grid_res * grid_res
    print(f"  Occupancy:  {occupied}/{total} cells ({100.0*occupied/total:.1f}%)")
    print(f"  Cell size:  {cell_w:.3f} x {cell_h:.3f} (world units)")
    print(f"  BBox used:  X[{xmin:.2f}, {xmax:.2f}]  Y[{ymin:.2f}, {ymax:.2f}]")

    return spcd_grid, dia_grid, ht_grid, cr_grid, (xmin, ymin, xmax, ymax, cell_w, cell_h)


def write_exr_rgba(path, r, g, b, a):
    """
    Write a 4-channel half-float EXR. Tries several backends in order because
    imageio's EXR support depends on which plugin is installed (OpenCV often
    ships without EXR; OpenImageIO or the `OpenEXR` package are more reliable).
    """
    # Stack into HxWx4
    img = np.stack([r, g, b, a], axis=-1).astype(np.float16)
    h, w, _ = img.shape
    errors = []

    # Attempt 1: OpenEXR package (most reliable for UE-compatible EXR)
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

    # Attempt 2: OpenImageIO
    try:
        import OpenImageIO as oiio
        spec = oiio.ImageSpec(w, h, 4, "half")
        out = oiio.ImageOutput.create(str(path))
        if out is None:
            raise RuntimeError("OIIO could not create writer for .exr")
        out.open(str(path), spec)
        out.write_image(img)
        out.close()
        return
    except Exception as e:
        errors.append(f"OpenImageIO: {e}")

    # Attempt 3: imageio (if it has a working EXR plugin)
    if _HAS_IIO:
        try:
            iio.imwrite(path, img, extension=".exr")
            return
        except Exception as e:
            errors.append(f"imageio: {e}")

    raise RuntimeError(
        "Could not write EXR via any available backend. Install one of:\n"
        "  pip install OpenEXR           (recommended, most reliable)\n"
        "  pip install openimageio\n"
        "  pip install imageio[freeimage]\n\n"
        "Backend errors:\n  " + "\n  ".join(errors)
    )


def write_png_preview(path_stem, spcd, dia, ht, cr, mask):
    """Optional sanity-check PNGs: 8-bit-normalized previews of each channel."""
    try:
        import imageio.v3 as iio_local
    except ImportError:
        print("  (Skipping PNG preview -- imageio not installed)")
        return

    def norm8(arr):
        m = arr.max()
        if m <= 0:
            return np.zeros_like(arr, dtype=np.uint8)
        return np.clip(arr / m * 255.0, 0, 255).astype(np.uint8)

    iio_local.imwrite(f"{path_stem}_preview_SPCD.png", norm8(spcd))
    iio_local.imwrite(f"{path_stem}_preview_DIA.png", norm8(dia))
    iio_local.imwrite(f"{path_stem}_preview_HT.png", norm8(ht))
    iio_local.imwrite(f"{path_stem}_preview_CR.png", norm8(cr * 255.0))
    iio_local.imwrite(f"{path_stem}_preview_mask.png", (mask * 255).astype(np.uint8))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", type=Path, help="Path to FastFuels tree CSV")
    ap.add_argument("out_dir", type=Path, help="Output directory for EXR textures")
    ap.add_argument("--grid", type=int, default=512, help="Grid resolution (default 512)")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
                    help="Explicit bbox. If omitted, computed from CSV extents.")
    ap.add_argument("--flip-v", action="store_true", default=True,
                    help="Flip vertically so row 0 = top (UE texture convention). Default on.")
    ap.add_argument("--no-flip-v", action="store_false", dest="flip_v")
    ap.add_argument("--include-dead", action="store_true",
                    help="Include non-live trees (STATUSCD != 1)")
    ap.add_argument("--preview", action="store_true",
                    help="Also write 8-bit PNG preview of each channel for visual check")
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading: {args.csv}")
    trees = list(parse_csv(args.csv, live_only=not args.include_dead))
    print(f"  Parsed {len(trees)} live trees" if not args.include_dead
          else f"  Parsed {len(trees)} trees (all status)")

    if not trees:
        print("ERROR: no trees parsed.", file=sys.stderr)
        sys.exit(1)

    if args.bbox:
        bbox = tuple(args.bbox)
        print(f"Using explicit bbox: {bbox}")
    else:
        bbox = compute_bbox(trees)
        print(f"Computed bbox: {bbox}")

    print(f"Rasterizing to {args.grid}x{args.grid}...")
    spcd, dia, ht, cr, bbox_used = rasterize(trees, args.grid, bbox)

    # Alpha mask: 1 where a tree exists
    mask = (dia > 0.0).astype(np.float32)

    if args.flip_v:
        # Flip so row 0 is north (top of texture in UE). Matches UV.y=0 at top.
        spcd = np.flipud(spcd)
        dia = np.flipud(dia)
        ht = np.flipud(ht)
        cr = np.flipud(cr)
        mask = np.flipud(mask)

    # Write two EXRs, each using R+G (plus B=0, A=mask).
    # Packing with a shared alpha mask means either texture alone answers
    # "does this cell have a tree?".
    out_rg = args.out_dir / "TreeData_RG.exr"
    out_ba = args.out_dir / "TreeData_BA.exr"

    print(f"Writing: {out_rg}  (R=SPCD, G=DIA cm, A=mask)")
    write_exr_rgba(out_rg, spcd, dia, np.zeros_like(spcd), mask)

    print(f"Writing: {out_ba}  (R=HT m,  G=CR,     A=mask)")
    write_exr_rgba(out_ba, ht, cr, np.zeros_like(ht), mask)

    # Also drop a sidecar text file with the bbox metadata so you can keep
    # the sim's world-space footprint straight later.
    meta_path = args.out_dir / "TreeData_meta.txt"
    xmin, ymin, xmax, ymax, cell_w, cell_h = bbox_used
    with open(meta_path, "w") as f:
        f.write(f"grid_res={args.grid}\n")
        f.write(f"xmin={xmin}\nymin={ymin}\nxmax={xmax}\nymax={ymax}\n")
        f.write(f"cell_w={cell_w}\ncell_h={cell_h}\n")
        f.write(f"width_m={xmax-xmin}\nheight_m={ymax-ymin}\n")
    print(f"Wrote metadata sidecar: {meta_path}")

    if args.preview:
        stem = str(args.out_dir / "TreeData")
        print("Writing PNG previews...")
        write_png_preview(stem, spcd, dia, ht, cr, mask)

    print("Done.")


if __name__ == "__main__":
    main()