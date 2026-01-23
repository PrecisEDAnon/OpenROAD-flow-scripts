#!/usr/bin/env python3
"""
Scan-chain visualization helper (ORFS).

Generates an SVG/PNG overlay of the stitched scan chain(s) by drawing a polyline
between scan cell placements, in scan order.

Inputs:
  - Gate-level Verilog (to reconstruct stitched scan chain connectivity)
  - DEF (to get placed instance locations + die area)

Typical usage (single chain):
  python3 flow/util/scan_chain_plot.py \
    --verilog flow/results/nangate45/ibex/<variant>/6_final.v \
    --def flow/results/nangate45/ibex/<variant>/6_final.def \
    --out flow/reports/nangate45/ibex/<variant>/dft_scan_chain.png

Multi-chain usage:
  python3 flow/util/scan_chain_plot.py --auto-chains ... (writes one plot per chain)
"""

from __future__ import annotations

import argparse
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Reuse the same scan parsing + stitching reconstruction used by validation.
from scan_chain_validate import (  # type: ignore[import-not-found]
    _parse_ports_from_verilog_lines,
    _resolve_alias,
    parse_scan_cells_from_verilog,
    reconstruct_chain,
)

import struct
import zlib


@dataclass(frozen=True)
class Chain:
    scan_in: str
    scan_out: str
    cells: List[str]


@dataclass(frozen=True)
class DieArea:
    x0_um: float
    y0_um: float
    x1_um: float
    y1_um: float

    @property
    def w_um(self) -> float:
        return self.x1_um - self.x0_um

    @property
    def h_um(self) -> float:
        return self.y1_um - self.y0_um


def _def_unescape_ident(token: str) -> str:
    # DEF escapes special chars as "\<char>" inside identifiers.
    return re.sub(r"\\(.)", r"\1", token.strip())


def parse_def_placements(def_path: Path) -> Tuple[Dict[str, Tuple[float, float]], DieArea]:
    units_per_micron: Optional[int] = None
    diearea: Optional[DieArea] = None
    placements: Dict[str, Tuple[float, float]] = {}

    units_re = re.compile(r"^\s*UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;\s*$")
    diearea_re = re.compile(
        r"^\s*DIEAREA\s+\(\s*(\d+)\s+(\d+)\s*\)\s+\(\s*(\d+)\s+(\d+)\s*\)\s*;\s*$"
    )

    in_components = False
    cur_record: List[str] = []

    def flush_record() -> None:
        nonlocal cur_record
        if not cur_record:
            return
        record = " ".join(cur_record).strip()
        cur_record = []

        # "- <inst> <master> ..."
        m = re.match(r"^\s*-\s+(\S+)\s+(\S+)\s+", record)
        if not m:
            return
        inst = _def_unescape_ident(m.group(1))

        # "+ PLACED ( x y ) <orient>" or "+ FIXED ( x y ) <orient>"
        pm = re.search(r"\+\s+(PLACED|FIXED)\s+\(\s*(\d+)\s+(\d+)\s*\)\s+(\S+)", record)
        if not pm:
            return

        if units_per_micron is None:
            raise RuntimeError(f"DEF missing UNITS (needed before COMPONENTS): {def_path}")
        x_dbu = int(pm.group(2))
        y_dbu = int(pm.group(3))
        placements[inst] = (x_dbu / units_per_micron, y_dbu / units_per_micron)

    with def_path.open("r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.rstrip("\n")

            if units_per_micron is None:
                m = units_re.match(line)
                if m:
                    units_per_micron = int(m.group(1))
                    continue

            if diearea is None:
                m = diearea_re.match(line)
                if m:
                    if units_per_micron is None:
                        raise RuntimeError(f"DEF DIEAREA found before UNITS: {def_path}")
                    x0, y0, x1, y1 = (int(m.group(i)) for i in range(1, 5))
                    diearea = DieArea(
                        x0_um=x0 / units_per_micron,
                        y0_um=y0 / units_per_micron,
                        x1_um=x1 / units_per_micron,
                        y1_um=y1 / units_per_micron,
                    )
                    continue

            if not in_components:
                if line.lstrip().startswith("COMPONENTS"):
                    in_components = True
                continue

            if line.lstrip().startswith("END COMPONENTS"):
                flush_record()
                in_components = False
                continue

            if line.lstrip().startswith("-"):
                flush_record()
                cur_record = [line]
                if ";" in line:
                    flush_record()
                continue

            if cur_record:
                cur_record.append(line)
                if ";" in line:
                    flush_record()

    flush_record()

    if units_per_micron is None:
        raise RuntimeError(f"DEF missing UNITS DISTANCE MICRONS: {def_path}")
    if diearea is None:
        raise RuntimeError(f"DEF missing DIEAREA: {def_path}")
    if not placements:
        raise RuntimeError(f"DEF had no placed components: {def_path}")

    return placements, diearea


def reconstruct_chains_from_verilog(
    verilog_path: Path,
    *,
    scan_in: str,
    scan_out: str,
    auto_chains: bool,
    scan_in_prefix: str,
    scan_out_prefix: str,
) -> Tuple[List[Chain], List[str]]:
    scan_cells, assigns, driven_by = parse_scan_cells_from_verilog(verilog_path)
    input_ports, output_ports = _parse_ports_from_verilog_lines(verilog_path.read_text().splitlines())

    errors: List[str] = []

    if not auto_chains:
        scan_out_source = _resolve_alias(assigns, scan_out)
        chain, chain_errors, _ = reconstruct_chain(
            scan_cells,
            scan_in_net=scan_in,
            scan_out_source_net=scan_out_source,
            driven_by=driven_by,
        )
        return [Chain(scan_in=scan_in, scan_out=scan_out, cells=chain)], chain_errors

    def ordinal(name: str, prefix: str) -> Optional[int]:
        if not name.startswith(prefix):
            return None
        suffix = name[len(prefix) :]
        if not suffix.isdigit():
            return None
        return int(suffix)

    scan_in_ports = {
        ordinal(name, scan_in_prefix): name for name in input_ports if ordinal(name, scan_in_prefix) is not None
    }
    scan_out_ports = {
        ordinal(name, scan_out_prefix): name
        for name in output_ports
        if ordinal(name, scan_out_prefix) is not None
    }

    if not scan_in_ports:
        errors.append(f"No scan-in ports found with prefix '{scan_in_prefix}'.")
    if not scan_out_ports:
        errors.append(f"No scan-out ports found with prefix '{scan_out_prefix}'.")

    chains: List[Chain] = []
    for idx in sorted(o for o in set(scan_in_ports.keys()) & set(scan_out_ports.keys()) if o is not None):
        scan_in_name = scan_in_ports[idx]
        scan_out_name = scan_out_ports[idx]
        scan_out_source = _resolve_alias(assigns, scan_out_name)
        chain, chain_errors, _ = reconstruct_chain(
            scan_cells,
            scan_in_net=scan_in_name,
            scan_out_source_net=scan_out_source,
            driven_by=driven_by,
        )
        errors.extend(chain_errors)
        chains.append(Chain(scan_in=scan_in_name, scan_out=scan_out_name, cells=chain))

    if not chains:
        errors.append("No scan chains reconstructed from ports.")
    return chains, errors


def _svg_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    length = struct.pack(">I", len(data))
    crc = zlib.crc32(chunk_type)
    crc = zlib.crc32(data, crc)
    return length + chunk_type + data + struct.pack(">I", crc & 0xFFFFFFFF)


def _write_png_rgba(path: Path, *, width: int, height: int, pixels_rgba: bytes) -> None:
    if len(pixels_rgba) != width * height * 4:
        raise ValueError("Bad RGBA buffer size.")

    # PNG scanlines: filter byte 0 + raw RGBA.
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        row = pixels_rgba[y * stride : (y + 1) * stride]
        raw.extend(row)

    compressed = zlib.compress(bytes(raw), level=9)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = bytearray()
    png.extend(b"\x89PNG\r\n\x1a\n")
    png.extend(_png_chunk(b"IHDR", ihdr))
    png.extend(_png_chunk(b"IDAT", compressed))
    png.extend(_png_chunk(b"IEND", b""))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(png))


class _Raster:
    __slots__ = ("w", "h", "buf")

    def __init__(self, w: int, h: int) -> None:
        self.w = w
        self.h = h
        self.buf = bytearray([255, 255, 255, 255]) * (w * h)

    def _blend_pixel(self, x: int, y: int, r: int, g: int, b: int, a: int) -> None:
        if x < 0 or y < 0 or x >= self.w or y >= self.h:
            return
        if a <= 0:
            return
        idx = (y * self.w + x) * 4
        if a >= 255:
            self.buf[idx : idx + 4] = bytes((r, g, b, 255))
            return
        inv = 255 - a
        br, bg, bb, ba = self.buf[idx], self.buf[idx + 1], self.buf[idx + 2], self.buf[idx + 3]
        # Alpha-over, assuming existing alpha is 255 (opaque).
        nr = (r * a + br * inv) // 255
        ng = (g * a + bg * inv) // 255
        nb = (b * a + bb * inv) // 255
        na = min(255, ba + a)
        self.buf[idx : idx + 4] = bytes((nr, ng, nb, na))

    def draw_circle(self, cx: int, cy: int, radius: int, *, color: Tuple[int, int, int], alpha: int = 255) -> None:
        r, g, b = color
        rr = radius * radius
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy <= rr:
                    self._blend_pixel(cx + dx, cy + dy, r, g, b, alpha)

    def draw_line(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        *,
        color: Tuple[int, int, int],
        alpha: int = 255,
        width: int = 1,
    ) -> None:
        r, g, b = color
        dx = x1 - x0
        dy = y1 - y0
        steps = int(max(abs(dx), abs(dy), 1))
        rad = max(0, width // 2)
        for i in range(steps + 1):
            t = i / steps
            x = int(round(x0 + dx * t))
            y = int(round(y0 + dy * t))
            if rad == 0:
                self._blend_pixel(x, y, r, g, b, alpha)
            else:
                self.draw_circle(x, y, rad, color=color, alpha=alpha)


def _write_svg_chain(
    out_path: Path,
    *,
    chain: Chain,
    placements_um: Dict[str, Tuple[float, float]],
    diearea: DieArea,
    canvas_px: int,
    highlight_top_k: int,
) -> None:
    pad = 30
    w = canvas_px
    h = canvas_px

    die_w = max(diearea.w_um, 1e-9)
    die_h = max(diearea.h_um, 1e-9)
    scale = min((w - 2 * pad) / die_w, (h - 2 * pad) / die_h)

    def to_px(pt_um: Tuple[float, float]) -> Tuple[float, float]:
        x_um, y_um = pt_um
        x = pad + (x_um - diearea.x0_um) * scale
        y = h - pad - (y_um - diearea.y0_um) * scale
        return x, y

    pts_px: List[Tuple[float, float]] = []
    missing: List[str] = []
    for inst in chain.cells:
        loc = placements_um.get(inst)
        if not loc:
            missing.append(inst)
            continue
        pts_px.append(to_px(loc))

    if len(pts_px) < 2:
        raise RuntimeError(
            f"Not enough placed scan cells to plot (have {len(pts_px)} points; missing {len(missing)})."
        )

    # Segment lengths (in px) for highlighting.
    seg_lens: List[Tuple[int, float]] = []
    for i in range(len(pts_px) - 1):
        x0, y0 = pts_px[i]
        x1, y1 = pts_px[i + 1]
        seg_lens.append((i, math.hypot(x1 - x0, y1 - y0)))
    seg_lens.sort(key=lambda t: t[1], reverse=True)
    highlight = {idx for idx, _ in seg_lens[: max(0, highlight_top_k)] if len(seg_lens) > 0}

    points_attr = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts_px)
    title = f"{chain.scan_in} → {chain.scan_out} ({len(chain.cells)} cells)"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}">\n'
        )
        f.write('<rect x="0" y="0" width="100%" height="100%" fill="white"/>\n')

        # Die outline.
        die_x0, die_y1 = to_px((diearea.x0_um, diearea.y0_um))
        die_x1, die_y0 = to_px((diearea.x1_um, diearea.y1_um))
        die_w_px = die_x1 - die_x0
        die_h_px = die_y1 - die_y0
        f.write(
            f'<rect x="{die_x0:.2f}" y="{die_y0:.2f}" width="{die_w_px:.2f}" height="{die_h_px:.2f}" '
            'fill="none" stroke="#111827" stroke-width="1"/>\n'
        )

        # Base chain path.
        f.write(
            f'<polyline points="{points_attr}" fill="none" '
            'stroke="#2563eb" stroke-opacity="0.20" stroke-width="1"/>\n'
        )

        # Highlight longest "jumps".
        for i in sorted(highlight):
            x0, y0 = pts_px[i]
            x1, y1 = pts_px[i + 1]
            f.write(
                f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}" '
                'stroke="#dc2626" stroke-width="2" stroke-opacity="0.80"/>\n'
            )

        # Endpoints.
        sx, sy = pts_px[0]
        ex, ey = pts_px[-1]
        f.write(f'<circle cx="{sx:.2f}" cy="{sy:.2f}" r="3" fill="#16a34a"/>\n')
        f.write(f'<circle cx="{ex:.2f}" cy="{ey:.2f}" r="3" fill="#7c3aed"/>\n')

        # Legend.
        legend = [
            f"Scan chain: {title}",
            f"Highlight: top {min(highlight_top_k, len(seg_lens))} longest segments",
            f"Missing placed instances: {len(missing)}",
        ]
        lx = 10
        ly = 18
        f.write(
            '<g font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace" '
            'font-size="12">\n'
        )
        for i, line in enumerate(legend):
            f.write(f'<text x="{lx}" y="{ly + 14*i}" fill="#111827">{_svg_escape(line)}</text>\n')
        f.write("</g>\n")

        f.write("</svg>\n")


def _write_png_chain(
    out_path: Path,
    *,
    chain: Chain,
    placements_um: Dict[str, Tuple[float, float]],
    diearea: DieArea,
    canvas_px: int,
    highlight_top_k: int,
) -> None:
    pad = 30
    w = canvas_px
    h = canvas_px

    die_w = max(diearea.w_um, 1e-9)
    die_h = max(diearea.h_um, 1e-9)
    scale = min((w - 2 * pad) / die_w, (h - 2 * pad) / die_h)

    def to_px_int(pt_um: Tuple[float, float]) -> Tuple[int, int]:
        x_um, y_um = pt_um
        x = pad + (x_um - diearea.x0_um) * scale
        y = h - pad - (y_um - diearea.y0_um) * scale
        return int(round(x)), int(round(y))

    pts_px: List[Tuple[int, int]] = []
    for inst in chain.cells:
        loc = placements_um.get(inst)
        if not loc:
            continue
        pts_px.append(to_px_int(loc))

    if len(pts_px) < 2:
        raise RuntimeError(f"Not enough placed scan cells to plot (have {len(pts_px)} points).")

    # Segment lengths (in px) for highlighting.
    seg_lens: List[Tuple[int, float]] = []
    for i in range(len(pts_px) - 1):
        x0, y0 = pts_px[i]
        x1, y1 = pts_px[i + 1]
        seg_lens.append((i, math.hypot(x1 - x0, y1 - y0)))
    seg_lens.sort(key=lambda t: t[1], reverse=True)
    highlight = {idx for idx, _ in seg_lens[: max(0, highlight_top_k)] if len(seg_lens) > 0}

    img = _Raster(w, h)

    # Die outline.
    die_x0, die_y1 = to_px_int((diearea.x0_um, diearea.y0_um))
    die_x1, die_y0 = to_px_int((diearea.x1_um, diearea.y1_um))
    img.draw_line(die_x0, die_y0, die_x1, die_y0, color=(17, 24, 39), width=1)
    img.draw_line(die_x1, die_y0, die_x1, die_y1, color=(17, 24, 39), width=1)
    img.draw_line(die_x1, die_y1, die_x0, die_y1, color=(17, 24, 39), width=1)
    img.draw_line(die_x0, die_y1, die_x0, die_y0, color=(17, 24, 39), width=1)

    # Base chain path: light blue.
    for (x0, y0), (x1, y1) in zip(pts_px[:-1], pts_px[1:]):
        img.draw_line(x0, y0, x1, y1, color=(37, 99, 235), alpha=60, width=1)

    # Highlight longest segments.
    for i in sorted(highlight):
        x0, y0 = pts_px[i]
        x1, y1 = pts_px[i + 1]
        img.draw_line(x0, y0, x1, y1, color=(220, 38, 38), alpha=220, width=3)

    # Endpoints.
    sx, sy = pts_px[0]
    ex, ey = pts_px[-1]
    img.draw_circle(sx, sy, 4, color=(22, 163, 74), alpha=255)
    img.draw_circle(ex, ey, 4, color=(124, 58, 237), alpha=255)

    _write_png_rgba(out_path, width=w, height=h, pixels_rgba=bytes(img.buf))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Generate scan-chain plot (SVG/PNG) from DEF+Verilog.")
    ap.add_argument("--verilog", required=True, type=Path)
    ap.add_argument("--def", dest="def_path", required=True, type=Path)
    ap.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output path (SVG/PNG file, or directory for per-chain output).",
    )
    ap.add_argument(
        "--format",
        choices=("svg", "png"),
        default=None,
        help="Force output format (otherwise inferred from --out extension).",
    )
    ap.add_argument("--scan-in", default="scan_in_0")
    ap.add_argument("--scan-out", default="scan_out_0")
    ap.add_argument("--auto-chains", action="store_true")
    ap.add_argument("--scan-in-prefix", default="scan_in_")
    ap.add_argument("--scan-out-prefix", default="scan_out_")
    ap.add_argument("--canvas", type=int, default=1400, help="Canvas size in pixels (square).")
    ap.add_argument(
        "--highlight-top-k",
        type=int,
        default=-1,
        help=(
            "Highlight the top K longest segments (approximate 'abrupt jumps'). "
            "Use -1 for an automatic heuristic (~2%% of segments, min 5, max 50)."
        ),
    )
    args = ap.parse_args(argv)

    verilog_path = args.verilog.resolve()
    def_path = args.def_path.resolve()
    if not verilog_path.exists():
        raise FileNotFoundError(verilog_path)
    if not def_path.exists():
        raise FileNotFoundError(def_path)

    placements_um, diearea = parse_def_placements(def_path)
    chains, errors = reconstruct_chains_from_verilog(
        verilog_path,
        scan_in=args.scan_in,
        scan_out=args.scan_out,
        auto_chains=args.auto_chains,
        scan_in_prefix=args.scan_in_prefix,
        scan_out_prefix=args.scan_out_prefix,
    )
    if errors:
        # Still try to write plots when possible, but report errors.
        for e in errors[:25]:
            print(f"WARNING: {e}")

    out = args.out.resolve()
    out_is_dir = out.exists() and out.is_dir()
    if out.suffix.lower() == "" or out_is_dir:
        out.mkdir(parents=True, exist_ok=True)
        out_is_dir = True

    written: List[Path] = []
    fmt = args.format
    if fmt is None:
        if out_is_dir:
            fmt = "png"
        else:
            ext = out.suffix.lower()
            fmt = "png" if ext == ".png" else "svg"

    def write_one(out_path: Path, chain: Chain, highlight_k: int) -> None:
        if fmt == "png":
            _write_png_chain(
                out_path,
                chain=chain,
                placements_um=placements_um,
                diearea=diearea,
                canvas_px=args.canvas,
                highlight_top_k=highlight_k,
            )
        else:
            _write_svg_chain(
                out_path,
                chain=chain,
                placements_um=placements_um,
                diearea=diearea,
                canvas_px=args.canvas,
                highlight_top_k=highlight_k,
            )

    if len(chains) == 1 and not args.auto_chains:
        auto_k = max(5, min(50, int(round(0.02 * max(0, len(chains[0].cells) - 1)))))
        highlight_k = auto_k if args.highlight_top_k < 0 else max(0, args.highlight_top_k)
        default_name = f"dft_scan_chain.{fmt}"
        out_path = out / default_name if out_is_dir else out
        write_one(out_path, chains[0], highlight_k)
        written.append(out_path)
    else:
        for idx, chain in enumerate(chains):
            if not chain.cells:
                continue
            auto_k = max(5, min(50, int(round(0.02 * max(0, len(chain.cells) - 1)))))
            highlight_k = auto_k if args.highlight_top_k < 0 else max(0, args.highlight_top_k)
            if out_is_dir:
                out_path = out / f"dft_scan_chain_{idx}.{fmt}"
            else:
                out_path = out.with_name(f"{out.stem}_chain_{idx}{out.suffix}")
            write_one(out_path, chain, highlight_k)
            written.append(out_path)

    if not written:
        raise RuntimeError("No scan-chain plots were written (no reconstructed chains).")

    for p in written:
        print(f"WROTE: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
