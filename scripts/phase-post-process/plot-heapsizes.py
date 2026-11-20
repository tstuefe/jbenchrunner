#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or (at your option)
# any later version. See LICENSE.txt in the repository root.

#
# Plot heap size over time from a heapsizes.txt produced by extract-heapsizes.py.
#
# Usage: plot-heapsizes.py <heapsizes.txt> [-o heapsizes.svg]

import sys
import os
import re
import argparse

_COMMON_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "common"))
if _COMMON_DIR not in sys.path:
    sys.path.insert(0, _COMMON_DIR)

from gnuplot_utils import gnuplot_quote, gnuplot_dquote, plot_title, temp_dat_files, run_gnuplot


def resolve_heapsizes_file(path):
    """
    Validate that path is a heapsizes.txt file. Exits with error otherwise.
    """
    if not os.path.isfile(path):
        print(f"Error: {path}: no such file", file=sys.stderr)
        sys.exit(1)
    return path


def parse(txt_path):
    combined       = []
    after          = []
    full_gc_after  = []
    summary        = {}

    summary_re = re.compile(r'^(.+?):\s+([\d.]+)\s*$')

    with open(txt_path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip()
            if line.lstrip().startswith("Elapsed") or set(line.strip()) == {'-'}:
                continue
            m = summary_re.match(line)
            if m:
                summary[m.group(1).strip()] = float(m.group(2))
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                elapsed = float(parts[0])
                used    = float(parts[-1])
            except ValueError:
                continue
            phase_s = parts[-2]
            combined.append((elapsed, used))
            if phase_s == "AFTER":
                after.append((elapsed, used))
                if len(parts) == 5 and parts[1] in ("FULL", "GLOBAL"):
                    full_gc_after.append((elapsed, used))

    combined.sort(key=lambda x: x[0])
    after.sort(key=lambda x: x[0])
    full_gc_after.sort(key=lambda x: x[0])
    max_after_point = max(after, key=lambda p: p[1]) if after else None
    max_full_gc_after_point = max(full_gc_after, key=lambda p: p[1]) if full_gc_after else None
    return combined, after, full_gc_after, max_after_point, max_full_gc_after_point, summary


def main():
    ap = argparse.ArgumentParser(
        description="Plot heap size over time from a heapsizes.txt file")
    ap.add_argument("input", metavar="<heapsizes.txt>",
                    help="heap size file produced by extract-heapsizes.py")
    ap.add_argument("-o", "--output", default="heapsizes.svg",
                    metavar="FILE", help="output file (default: heapsizes.svg)")
    args = ap.parse_args()

    txt_path = resolve_heapsizes_file(args.input)
    out_svg = args.output

    result_dir = os.path.dirname(os.path.abspath(txt_path))
    title = plot_title("Heap Size", result_dir)

    combined, after, full_gc_after, max_after_point, max_full_gc_after_point, summary = parse(txt_path)

    if not combined:
        print(f"{args.input}: ignored (no data in heapsizes.txt)")
        return

    with temp_dat_files() as make_dat:
        combined_path      = make_dat(combined)
        after_path         = make_dat(after)          if after          else None
        full_gc_after_path = make_dat(full_gc_after)  if full_gc_after  else None

        data_max = max(used for _, used in combined)
        gp = build_gnuplot_script(
            combined_path, after_path, full_gc_after_path,
            out_svg, title, summary, data_max, max_after_point, max_full_gc_after_point)
        if not run_gnuplot(gp):
            sys.exit(1)

    print(f"Written: {out_svg}")


def clamp_label_position(point_y, ymax_hint, initial_offset=0.03):
    """
    Calculate label Y position ensuring it's fully visible within plot area.
    
    Args:
        point_y: Y coordinate of the data point
        ymax_hint: Maximum Y value in the plot
        initial_offset: Initial offset from point as fraction of ymax_hint
    
    Returns:
        Y coordinate for label placement
    """
    max_y = ymax_hint * 1.05  # Plot area top boundary
    label_height_estimate = ymax_hint * 0.04  # Approximate label height
    
    # Try positioning above the point
    y_above = point_y + ymax_hint * initial_offset
    
    # Check if label would extend beyond plot area
    if y_above + label_height_estimate > max_y:
        # Position label below the point instead
        y_below = point_y - ymax_hint * initial_offset - label_height_estimate
        # If still not fitting (point too low), clamp to safe position near bottom
        if y_below < 0:
            return ymax_hint * 0.02
        return y_below
    
    return y_above


def build_gnuplot_script(combined_path, after_path, full_gc_after_path,
                         out_svg, title, summary, data_max, max_after_point=None, max_full_gc_after_point=None):
    max_heap_mb  = summary.get("Max heap size (MB)")

    ymax_hint = max(max_after_point[1] if max_after_point else 0,
                    max_full_gc_after_point[1] if max_full_gc_after_point else 0,
                    max_heap_mb or 0, data_max)

    plot_items = [
        f"{gnuplot_quote(combined_path)} using 1:2 with lines lw 1 lc rgb '#ffaa55' title 'Heap size'",
    ]
    if after_path:
        plot_items.append(
            f"{gnuplot_quote(after_path)} using 1:2 with lines lw 2 lc rgb '#cc0000' title 'After GC'"
        )
    if full_gc_after_path:
        plot_items.append(
            f"{gnuplot_quote(full_gc_after_path)} using 1:2 with points pt 6 ps 1 lc rgb '#cc0000' title 'Full GC'"
        )

    plot_cmd = "plot " + ", \\\n     ".join(plot_items)

    yrange = f"[0:{ymax_hint * 1.05:.0f}]" if ymax_hint > 0 else "[0:*]"

    label_cmds = []
    if max_after_point is not None:
        pt, pv = max_after_point
        y_pos = clamp_label_position(pv, ymax_hint, initial_offset=0.03)
        label_cmds.append(
            f"set label 'Max After GC: {pv:.0f} MB at {pt:.0f}s' "
            f"at first {pt:.3f}, first {y_pos:.1f} front right tc rgb '#cc0000' boxed"
        )
    
    if max_full_gc_after_point is not None:
        pt, pv = max_full_gc_after_point
        # Use larger initial offset if both labels exist and are close
        initial_offset = 0.03
        if max_after_point is not None and abs(max_after_point[0] - pt) < (ymax_hint * 0.1):
            initial_offset = 0.07  # Offset more to avoid overlap
        
        y_pos = clamp_label_position(pv, ymax_hint, initial_offset=initial_offset)
        label_cmds.append(
            f"set label 'Max After Full GC: {pv:.0f} MB at {pt:.0f}s' "
            f"at first {pt:.3f}, first {y_pos:.1f} front right tc rgb '#cc0000' boxed"
        )
    
    label_cmd = ""
    if label_cmds:
        label_cmd = "set style textbox opaque fillcolor rgb 'white' border lc rgb '#888888' lw 0.5\n"
        label_cmd += "\n".join(label_cmds)

    script = f"""\
set terminal svg size 1400,600 enhanced font 'Sans,10' background 'white'
set output {gnuplot_quote(out_svg)}

set title {gnuplot_dquote(title)} noenhanced
set xlabel 'Elapsed (s)'
set ylabel 'Used heap (MB)'
set yrange {yrange}
set grid
set key top left
set style data lines

{label_cmd}
{plot_cmd}
"""
    return script


if __name__ == "__main__":
    import signal
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except AttributeError:
        pass
    main()
