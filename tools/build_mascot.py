"""Compose local raster brand assets with already-installed system libraries.

No downloads or network. The core CLI does not require these optional libraries.
"""

import base64
import ctypes as C
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def bind(library, name, result, args):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = args
    return function


def render_svg(data, target, width, height, *, white=False):
    import xml.etree.ElementTree as ET
    for element in ET.fromstring(data).iter():
        for key, value in element.attrib.items():
            if key.rsplit("}", 1)[-1] == "href" and not value.startswith("data:image/png;base64,"):
                raise ValueError("external image references are forbidden")
    cairo = C.CDLL("libcairo.so.2")
    rsvg = C.CDLL("librsvg-2.so.2")
    gobject = C.CDLL("libgobject-2.0.so.0")
    pointer = C.c_void_p
    surface_new = bind(cairo, "cairo_image_surface_create", pointer, [C.c_int, C.c_int, C.c_int])
    context_new = bind(cairo, "cairo_create", pointer, [pointer])
    set_rgb = bind(cairo, "cairo_set_source_rgb", None, [pointer, C.c_double, C.c_double, C.c_double])
    paint = bind(cairo, "cairo_paint", None, [pointer])
    write_png = bind(cairo, "cairo_surface_write_to_png", C.c_int, [pointer, C.c_char_p])
    destroy = bind(cairo, "cairo_destroy", None, [pointer])
    destroy_surface = bind(cairo, "cairo_surface_destroy", None, [pointer])
    from_data = bind(rsvg, "rsvg_handle_new_from_data", pointer, [C.c_char_p, C.c_size_t, pointer])
    unref = bind(gobject, "g_object_unref", None, [pointer])

    class Rectangle(C.Structure):
        _fields_ = [(field, C.c_double) for field in ("x", "y", "width", "height")]

    render = bind(rsvg, "rsvg_handle_render_document", C.c_int, [pointer, pointer, C.POINTER(Rectangle), pointer])
    # Embedded local PNG data is permitted; external file/URL resources are not.
    if b"<!ENTITY" in data or b"<script" in data or b"url(" in data:
        raise ValueError("mascot must be self-contained")
    handle = from_data(data, len(data), None)
    if not handle:
        raise RuntimeError("could not load SVG")
    try:
        surface = surface_new(0, width, height)
        context = context_new(surface)
        try:
            if white:
                set_rgb(context, 1, 1, 1)
                paint(context)
            viewport = Rectangle(0, 0, width, height)
            if not render(handle, context, C.byref(viewport), None):
                raise RuntimeError("could not render SVG")
            if write_png(surface, str(target).encode()) != 0:
                raise RuntimeError("could not write PNG")
        finally:
            destroy(context)
            destroy_surface(surface)
    finally:
        unref(handle)


def main():
    # Exact, locally stored Micro 5 glyphs; no system font fallback. SVG is only
    # the in-memory layout engine. All brand deliverables are raster PNGs.
    import hashlib
    import json
    from fontTools.ttLib import TTFont
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.boundsPen import BoundsPen

    assets = ROOT / "assets"
    output = assets / "brand"
    output.mkdir(exist_ok=True)
    font_path = assets / "fonts/Micro5-Regular.ttf"
    font = TTFont(font_path)
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()
    paths, cursor, bounds = [], 0, []
    for char in "flint":
        name = cmap[ord(char)]
        pen, box = SVGPathPen(glyphs), BoundsPen(glyphs)
        glyphs[name].draw(pen)
        glyphs[name].draw(box)
        paths.append(f'<path transform="translate({cursor} 0)" d="{pen.getCommands()}"/>')
        if box.bounds:
            x0, y0, x1, y1 = box.bounds
            bounds.append((cursor + x0, y0, cursor + x1, y1))
        cursor += font["hmtx"][name][0]
    x0, y0 = min(b[0] for b in bounds), min(b[1] for b in bounds)
    x1, y1 = max(b[2] for b in bounds), max(b[3] for b in bounds)
    font.close()
    outlines = "".join(paths)
    logo = (assets / "logo.png").read_bytes()
    if len(logo) > 4 * 1024**2:
        raise ValueError("logo exceeds render allowance")
    source = base64.b64encode(logo).decode()

    def face(x, y, height):
        # Crop only the layout viewport, preserving the approved source bytes.
        width = height * 760 / 950
        return (f'<svg x="{x}" y="{y}" width="{width}" height="{height}" viewBox="240 150 760 950">'
                f'<image width="1254" height="1254" href="data:image/png;base64,{source}"/></svg>')

    def word(x, y, height, color="#111111"):
        scale = height / (y1 - y0)
        return (f'<g fill="{color}" transform="translate({x} {y + height}) scale({scale} {-scale}) '
                f'translate({-x0} {-y0})">{outlines}</g>')

    def save(name, width, height, body, white=True):
        data = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">{body}</svg>').encode()
        render_svg(data, output / name, width, height, white=white)

    save("flint-horizontal.png", 1600, 600, face(238, 95, 410) + word(660, 172, 270))
    save("flint-stacked.png", 960, 1100, face(240, 76, 600) + word(129, 772, 270))
    save("flint-wordmark.png", 884, 380, word(52, 40, 300), white=False)
    save("flint-wordmark-white.png", 884, 380, word(52, 40, 300, "#ffffff"), white=False)
    save("flint-avatar.png", 512, 512, face(80, 36, 440))
    save("favicon.png", 64, 64, face(9.6, 4, 56))
    provenance = {
        "name": "Flint", "wordmark": "flint", "font": "Micro 5 Regular",
        "font_source": "https://github.com/google/fonts/tree/main/ofl/micro5",
        "font_sha256": hashlib.sha256(font_path.read_bytes()).hexdigest(),
        "font_license": "SIL Open Font License 1.1; ../fonts/OFL.txt",
        "mascot_source": "../logo.png", "mascot_sha256": hashlib.sha256(logo).hexdigest(),
        "method": "Unmodified local font outlines and approved mascot, rendered to PNG; source mascot unchanged",
        "outputs": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.glob("*.png"))},
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    import zipfile
    members = [assets / "preview.html", assets / "logo.png", *sorted((assets / "fonts").iterdir()), *sorted(output.iterdir())]
    with zipfile.ZipFile(assets / "flint-brand-kit.zip", "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in members:
            if path.is_file():
                member = "flint-brand/" + path.relative_to(assets).as_posix()
                if path.name == "preview.html":
                    html = path.read_text().replace('<a class="label" href="flint-brand-kit.zip" download>Scarica il brand kit ↗</a>', '<span class="label">Brand kit / 01</span>')
                    bundle.writestr(member, html)
                else:
                    bundle.write(path, member)
    print("Rendered six local Micro 5 brand PNGs and ready-to-use ZIP; approved source mascot unchanged.")


if __name__ == "__main__":
    main()
