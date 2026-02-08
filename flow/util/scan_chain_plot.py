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


def parse_def_placements(
    def_path: Path,
) -> Tuple[Dict[str, Tuple[float, float]], Dict[str, Tuple[float, float]], DieArea]:
    units_per_micron: Optional[int] = None
    diearea: Optional[DieArea] = None
    placements: Dict[str, Tuple[float, float]] = {}
    pins: Dict[str, Tuple[float, float]] = {}

    units_re = re.compile(r"^\s*UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;\s*$")
    diearea_re = re.compile(
        r"^\s*DIEAREA\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*;\s*$"
    )

    in_components = False
    in_pins = False
    cur_record: List[str] = []
    cur_record_type: Optional[str] = None

    def flush_record() -> None:
        nonlocal cur_record, cur_record_type
        if not cur_record:
            return
        record = " ".join(cur_record).strip()
        cur_record = []
        record_type = cur_record_type
        cur_record_type = None

        # "+ PLACED ( x y ) <orient>" or "+ FIXED ( x y ) <orient>"
        pm = re.search(
            r"\+\s+(PLACED|FIXED)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\S+)", record
        )
        if not pm:
            return

        if units_per_micron is None:
            raise RuntimeError(f"DEF missing UNITS (needed before placements): {def_path}")
        x_dbu = int(pm.group(2))
        y_dbu = int(pm.group(3))
        x_um = x_dbu / units_per_micron
        y_um = y_dbu / units_per_micron

        if record_type == "comp":
            # "- <inst> <master> ..."
            m = re.match(r"^\s*-\s+(\S+)\s+(\S+)\s+", record)
            if not m:
                return
            inst = _def_unescape_ident(m.group(1))
            placements[inst] = (x_um, y_um)
            return

        if record_type == "pin":
            # "- <pin> + NET ..."
            m = re.match(r"^\s*-\s+(\S+)\s+", record)
            if not m:
                return
            pin = _def_unescape_ident(m.group(1))
            pins[pin] = (x_um, y_um)
            return

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

            if line.lstrip().startswith("COMPONENTS"):
                flush_record()
                in_components = True
                in_pins = False
                continue
            if line.lstrip().startswith("END COMPONENTS"):
                flush_record()
                in_components = False
                continue

            if line.lstrip().startswith("PINS"):
                flush_record()
                in_pins = True
                in_components = False
                continue
            if line.lstrip().startswith("END PINS"):
                flush_record()
                in_pins = False
                continue

            if not in_components and not in_pins:
                continue

            if line.lstrip().startswith("-"):
                flush_record()
                cur_record = [line]
                cur_record_type = "comp" if in_components else "pin"
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

    return placements, pins, diearea


def reconstruct_chains_from_verilog(
    verilog_path: Path,
    *,
    scan_in: str,
    scan_out: str,
    auto_chains: bool,
    scan_in_prefix: str,
    scan_out_prefix: str,
    max_chain_count: Optional[int] = None,
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
    ords = sorted(
        o for o in set(scan_in_ports.keys()) & set(scan_out_ports.keys()) if o is not None
    )
    if max_chain_count is not None and max_chain_count > 0:
        ords = [o for o in ords if o is not None and o < max_chain_count]

    for idx in ords:
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


def _require_pillow():
    try:
        from PIL import Image, ImageDraw  # type: ignore[import-not-found]
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "PNG output requires Pillow. Install it (e.g. `pip install pillow`) or use `--format svg`."
        ) from e
    return Image, ImageDraw


def _draw_dashed_line_pil(
    draw,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    *,
    fill_rgba: Tuple[int, int, int, int],
    width: int,
    dash_px: int = 8,
    gap_px: int = 6,
) -> None:
    dx = x1 - x0
    dy = y1 - y0
    seg_len = math.hypot(dx, dy)
    if seg_len <= 1e-9:
        draw.line((x0, y0, x1, y1), fill=fill_rgba, width=width)
        return
    step = max(1.0, float(dash_px + gap_px))
    t = 0.0
    while t < seg_len:
        t0 = t / seg_len
        t1 = min((t + dash_px) / seg_len, 1.0)
        ax = int(round(x0 + dx * t0))
        ay = int(round(y0 + dy * t0))
        bx = int(round(x0 + dx * t1))
        by = int(round(y0 + dy * t1))
        draw.line((ax, ay, bx, by), fill=fill_rgba, width=width)
        t += step


def _chain_palette_rgb() -> List[Tuple[int, int, int]]:
    # Tableau-like palette tuned for readability on white backgrounds.
    return [
        (37, 99, 235),  # blue
        (234, 88, 12),  # orange
        (22, 163, 74),  # green
        (124, 58, 237),  # purple
        (220, 38, 38),  # red
        (14, 116, 144),  # teal
        (217, 70, 239),  # magenta
        (2, 132, 199),  # sky
        (132, 204, 22),  # lime
        (245, 158, 11),  # amber
    ]


def _write_svg_multi(
    out_path: Path,
    *,
    chains: Sequence[Chain],
    placements_um: Dict[str, Tuple[float, float]],
    pins_um: Dict[str, Tuple[float, float]],
    diearea: DieArea,
    canvas_px: int,
    highlight_top_k: int,
    show_io_edges: bool,
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

    palette = _chain_palette_rgb()

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

        total_cells = 0
        for idx, chain in enumerate(chains):
            if not chain.cells:
                continue
            pts_px: List[Tuple[float, float]] = []
            for inst in chain.cells:
                loc = placements_um.get(inst)
                if not loc:
                    continue
                pts_px.append(to_px(loc))
            if len(pts_px) < 2:
                continue

            total_cells += len(chain.cells)

            # Segment lengths (in px) for highlighting.
            seg_lens: List[Tuple[int, float]] = []
            for i in range(len(pts_px) - 1):
                x0, y0 = pts_px[i]
                x1, y1 = pts_px[i + 1]
                seg_lens.append((i, math.hypot(x1 - x0, y1 - y0)))
            seg_lens.sort(key=lambda t: t[1], reverse=True)
            auto_k = max(5, min(50, int(round(0.02 * max(0, len(seg_lens))))))
            k = auto_k if highlight_top_k < 0 else max(0, highlight_top_k)
            highlight = {i for i, _ in seg_lens[:k]}

            r, g, b = palette[idx % len(palette)]
            color = f"#{r:02x}{g:02x}{b:02x}"

            # Optional IO dashed edges.
            if show_io_edges:
                in_loc = pins_um.get(chain.scan_in)
                out_loc = pins_um.get(chain.scan_out)
                if in_loc is not None:
                    x0, y0 = to_px(in_loc)
                    x1, y1 = pts_px[0]
                    f.write(
                        f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}" '
                        'stroke="#111827" stroke-width="1" stroke-opacity="0.80" stroke-dasharray="4,4"/>\n'
                    )
                if out_loc is not None:
                    x0, y0 = to_px(out_loc)
                    x1, y1 = pts_px[-1]
                    f.write(
                        f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x0:.2f}" y2="{y0:.2f}" '
                        'stroke="#111827" stroke-width="1" stroke-opacity="0.80" stroke-dasharray="4,4"/>\n'
                    )

            # Base chain path.
            points_attr = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts_px)
            f.write(
                f'<polyline points="{points_attr}" fill="none" '
                f'stroke="{color}" stroke-opacity="0.22" stroke-width="1"/>\n'
            )

            # Highlight longest segments (same chain color, thicker).
            for i in sorted(highlight):
                x0, y0 = pts_px[i]
                x1, y1 = pts_px[i + 1]
                f.write(
                    f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}" '
                    f'stroke="{color}" stroke-width="2" stroke-opacity="0.85"/>\n'
                )

        # Legend (minimal).
        legend = [
            f"Scan chains: {sum(1 for c in chains if c.cells)}",
            f"Total scan cells (from netlist reconstruction): {total_cells}",
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


def _write_png_multi(
    out_path: Path,
    *,
    chains: Sequence[Chain],
    placements_um: Dict[str, Tuple[float, float]],
    pins_um: Dict[str, Tuple[float, float]],
    diearea: DieArea,
    canvas_px: int,
    highlight_top_k: int,
    show_io_edges: bool,
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

    palette = _chain_palette_rgb()
    Image, ImageDraw = _require_pillow()
    img = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img, "RGBA")

    # Die outline.
    die_x0, die_y1 = to_px_int((diearea.x0_um, diearea.y0_um))
    die_x1, die_y0 = to_px_int((diearea.x1_um, diearea.y1_um))
    draw.rectangle((die_x0, die_y0, die_x1, die_y1), outline=(17, 24, 39, 255), width=1)

    for idx, chain in enumerate(chains):
        if not chain.cells:
            continue
        pts_px: List[Tuple[int, int]] = []
        for inst in chain.cells:
            loc = placements_um.get(inst)
            if not loc:
                continue
            pts_px.append(to_px_int(loc))
        if len(pts_px) < 2:
            continue

        # Segment lengths (in px) for highlighting.
        seg_lens: List[Tuple[int, float]] = []
        for i in range(len(pts_px) - 1):
            x0, y0 = pts_px[i]
            x1, y1 = pts_px[i + 1]
            seg_lens.append((i, math.hypot(x1 - x0, y1 - y0)))
        seg_lens.sort(key=lambda t: t[1], reverse=True)
        auto_k = max(5, min(50, int(round(0.02 * max(0, len(seg_lens))))))
        k = auto_k if highlight_top_k < 0 else max(0, highlight_top_k)
        highlight = {i for i, _ in seg_lens[:k]}

        color = palette[idx % len(palette)]
        color_rgba_base = (color[0], color[1], color[2], 60)
        color_rgba_hi = (color[0], color[1], color[2], 235)

        # Base chain path.
        for (x0, y0), (x1, y1) in zip(pts_px[:-1], pts_px[1:]):
            draw.line((x0, y0, x1, y1), fill=color_rgba_base, width=1)

        # Optional IO dashed edges (black).
        if show_io_edges:
            if chain.scan_in in pins_um:
                sx, sy = to_px_int(pins_um[chain.scan_in])
                x0, y0 = pts_px[0]
                _draw_dashed_line_pil(
                    draw, sx, sy, x0, y0, fill_rgba=(17, 24, 39, 220), width=2
                )
                draw.ellipse((sx - 3, sy - 3, sx + 3, sy + 3), fill=(17, 24, 39, 255))
            if chain.scan_out in pins_um:
                ex, ey = to_px_int(pins_um[chain.scan_out])
                x1, y1 = pts_px[-1]
                _draw_dashed_line_pil(
                    draw, x1, y1, ex, ey, fill_rgba=(17, 24, 39, 220), width=2
                )
                draw.ellipse((ex - 3, ey - 3, ex + 3, ey + 3), fill=(17, 24, 39, 255))

        # Highlight longest segments (same chain color).
        for i in sorted(highlight):
            x0, y0 = pts_px[i]
            x1, y1 = pts_px[i + 1]
            draw.line((x0, y0, x1, y1), fill=color_rgba_hi, width=3)

        # Chain endpoints.
        sx, sy = pts_px[0]
        ex, ey = pts_px[-1]
        draw.ellipse((sx - 3, sy - 3, sx + 3, sy + 3), fill=(color[0], color[1], color[2], 255))
        draw.ellipse((ex - 3, ey - 3, ex + 3, ey + 3), fill=(color[0], color[1], color[2], 255))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


def _write_svg_chain(
    out_path: Path,
    *,
    chain: Chain,
    placements_um: Dict[str, Tuple[float, float]],
    pins_um: Dict[str, Tuple[float, float]],
    diearea: DieArea,
    canvas_px: int,
    highlight_top_k: int,
    show_io_edges: bool,
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

        # Optional: show scan_in/scan_out endpoints (DEF pins) and connect them
        # to the first/last scan cells.
        if show_io_edges:
            in_loc = pins_um.get(chain.scan_in)
            out_loc = pins_um.get(chain.scan_out)
            if in_loc is not None:
                x0, y0 = to_px(in_loc)
                x1, y1 = pts_px[0]
                f.write(
                    f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}" '
                    'stroke="#111827" stroke-width="1" stroke-opacity="0.80" stroke-dasharray="4,4"/>\n'
                )
                f.write(
                    f'<circle cx="{x0:.2f}" cy="{y0:.2f}" r="3" fill="white" stroke="#111827" stroke-width="1"/>\n'
                )
            if out_loc is not None:
                x0, y0 = to_px(out_loc)
                x1, y1 = pts_px[-1]
                f.write(
                    f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x0:.2f}" y2="{y0:.2f}" '
                    'stroke="#111827" stroke-width="1" stroke-opacity="0.80" stroke-dasharray="4,4"/>\n'
                )
                f.write(
                    f'<circle cx="{x0:.2f}" cy="{y0:.2f}" r="3" fill="white" stroke="#111827" stroke-width="1"/>\n'
                )

        # Base chain path (between scan cells).
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
    pins_um: Dict[str, Tuple[float, float]],
    diearea: DieArea,
    canvas_px: int,
    highlight_top_k: int,
    show_io_edges: bool,
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

    Image, ImageDraw = _require_pillow()
    img = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img, "RGBA")

    # Die outline.
    die_x0, die_y1 = to_px_int((diearea.x0_um, diearea.y0_um))
    die_x1, die_y0 = to_px_int((diearea.x1_um, diearea.y1_um))
    draw.rectangle((die_x0, die_y0, die_x1, die_y1), outline=(17, 24, 39, 255), width=1)

    # Base chain path: light blue.
    for (x0, y0), (x1, y1) in zip(pts_px[:-1], pts_px[1:]):
        draw.line((x0, y0, x1, y1), fill=(37, 99, 235, 60), width=1)

    # Optional: show scan_in/scan_out endpoints (DEF pins) and connect them to
    # the first/last scan cells.
    if show_io_edges:
        if chain.scan_in in pins_um:
            sx, sy = to_px_int(pins_um[chain.scan_in])
            x0, y0 = pts_px[0]
            _draw_dashed_line_pil(
                draw, sx, sy, x0, y0, fill_rgba=(17, 24, 39, 220), width=2
            )
            draw.ellipse((sx - 4, sy - 4, sx + 4, sy + 4), fill=(17, 24, 39, 255))
        if chain.scan_out in pins_um:
            ex, ey = to_px_int(pins_um[chain.scan_out])
            x1, y1 = pts_px[-1]
            _draw_dashed_line_pil(
                draw, x1, y1, ex, ey, fill_rgba=(17, 24, 39, 220), width=2
            )
            draw.ellipse((ex - 4, ey - 4, ex + 4, ey + 4), fill=(17, 24, 39, 255))

    # Highlight longest segments.
    for i in sorted(highlight):
        x0, y0 = pts_px[i]
        x1, y1 = pts_px[i + 1]
        draw.line((x0, y0, x1, y1), fill=(220, 38, 38, 220), width=3)

    # Endpoints.
    sx, sy = pts_px[0]
    ex, ey = pts_px[-1]
    draw.ellipse((sx - 4, sy - 4, sx + 4, sy + 4), fill=(22, 163, 74, 255))
    draw.ellipse((ex - 4, ey - 4, ex + 4, ey + 4), fill=(124, 58, 237, 255))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


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
    ap.add_argument(
        "--max-chain-count",
        type=int,
        default=None,
        help="When --auto-chains is set, only consider ordinals < max-chain-count.",
    )
    ap.add_argument(
        "--combined",
        action="store_true",
        help="When multiple chains are present, draw them all into one plot.",
    )
    ap.add_argument("--canvas", type=int, default=1400, help="Canvas size in pixels (square).")
    ap.add_argument(
        "--no-io-edges",
        action="store_true",
        help="Disable dashed edges from scan_in/out ports (DEF PINS) to first/last scan cell.",
    )
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

    out = args.out.resolve()
    out_is_dir = out.exists() and out.is_dir()
    if out.suffix.lower() == "" or out_is_dir:
        out.mkdir(parents=True, exist_ok=True)
        out_is_dir = True

    # Default behavior: if multiple scan chains are present and the user did not
    # explicitly specify a non-default chain endpoint, prefer plotting all
    # chains rather than silently showing only chain 0.
    auto_chains = args.auto_chains
    combined = args.combined
    if not auto_chains and args.scan_in == "scan_in_0" and args.scan_out == "scan_out_0":
        input_ports, output_ports = _parse_ports_from_verilog_lines(verilog_path.read_text().splitlines())

        def ordinal(name: str, prefix: str) -> Optional[int]:
            if not name.startswith(prefix):
                return None
            suffix = name[len(prefix) :]
            if not suffix.isdigit():
                return None
            return int(suffix)

        scan_in_ports = {
            ordinal(name, args.scan_in_prefix): name
            for name in input_ports
            if ordinal(name, args.scan_in_prefix) is not None
        }
        scan_out_ports = {
            ordinal(name, args.scan_out_prefix): name
            for name in output_ports
            if ordinal(name, args.scan_out_prefix) is not None
        }
        ords = sorted(
            o for o in set(scan_in_ports.keys()) & set(scan_out_ports.keys()) if o is not None
        )
        if args.max_chain_count is not None and args.max_chain_count > 0:
            ords = [o for o in ords if o is not None and o < args.max_chain_count]
        if len(ords) > 1:
            auto_chains = True
            if not out_is_dir:
                combined = True
                print(
                    f"INFO: detected {len(ords)} scan chains; plotting all chains into one image "
                    "(override with --scan-in/--scan-out, or use --auto-chains explicitly)."
                )

    placements_um, pins_um, diearea = parse_def_placements(def_path)
    chains, errors = reconstruct_chains_from_verilog(
        verilog_path,
        scan_in=args.scan_in,
        scan_out=args.scan_out,
        auto_chains=auto_chains,
        scan_in_prefix=args.scan_in_prefix,
        scan_out_prefix=args.scan_out_prefix,
        max_chain_count=args.max_chain_count,
    )
    if errors:
        # Still try to write plots when possible, but report errors.
        for e in errors[:25]:
            print(f"WARNING: {e}")

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
                pins_um=pins_um,
                diearea=diearea,
                canvas_px=args.canvas,
                highlight_top_k=highlight_k,
                show_io_edges=not args.no_io_edges,
            )
        else:
            _write_svg_chain(
                out_path,
                chain=chain,
                placements_um=placements_um,
                pins_um=pins_um,
                diearea=diearea,
                canvas_px=args.canvas,
                highlight_top_k=highlight_k,
                show_io_edges=not args.no_io_edges,
            )

    if len(chains) == 1 and not auto_chains:
        auto_k = max(5, min(50, int(round(0.02 * max(0, len(chains[0].cells) - 1)))))
        highlight_k = auto_k if args.highlight_top_k < 0 else max(0, args.highlight_top_k)
        default_name = f"dft_scan_chain.{fmt}"
        out_path = out / default_name if out_is_dir else out
        write_one(out_path, chains[0], highlight_k)
        written.append(out_path)
    elif combined:
        out_path = out
        if out_is_dir:
            out_path = out / f"dft_scan_chains_combined.{fmt}"
        if fmt == "png":
            _write_png_multi(
                out_path,
                chains=chains,
                placements_um=placements_um,
                pins_um=pins_um,
                diearea=diearea,
                canvas_px=args.canvas,
                highlight_top_k=args.highlight_top_k,
                show_io_edges=not args.no_io_edges,
            )
        else:
            _write_svg_multi(
                out_path,
                chains=chains,
                placements_um=placements_um,
                pins_um=pins_um,
                diearea=diearea,
                canvas_px=args.canvas,
                highlight_top_k=args.highlight_top_k,
                show_io_edges=not args.no_io_edges,
            )
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
