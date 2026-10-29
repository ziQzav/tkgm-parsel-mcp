#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Vaziyet cizimi (SVG + PNG) — hesap yok, yalnizca parsel + gecerli cekme siniri."""

import math
import os
import shutil
import subprocess

VARSAYILAN_KORIDOR = 2.0


def alan(pts):
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def ccw(pts):
    return pts if alan(pts) > 0 else list(reversed(pts))


def _kesisim(a1, a2, b1, b2):
    x1, y1 = a1
    x2, y2 = a2
    x3, y3 = b1
    x4, y4 = b2
    d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(d) < 1e-12:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / d
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / d
    return px, py


def icinde(pt, poly):
    x, y = pt
    n = len(poly)
    ic = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            ic = not ic
        j = i
    return ic


def basit_mi(poly):
    """Komsu olmayan kenarlar kesisiyor mu (offset gecerliligi)."""
    n = len(poly)

    def kes(a1, a2, b1, b2):
        def yon(p, q, r):
            return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        d1, d2 = yon(a1, a2, b1), yon(a1, a2, b2)
        d3, d4 = yon(b1, b2, a1), yon(b1, b2, a2)
        return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))

    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if kes(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n]):
                return False
    return True


def offset_polygon(pts, d):
    """Parseli d kadar iceri oteler (paralel offset). Gecersizse None."""
    p = ccw(list(pts))
    n = len(p)
    if d <= 0:
        return p
    cizgiler = []
    for i in range(n):
        x1, y1 = p[i]
        x2, y2 = p[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 1e-9:
            return None
        nx, ny = -dy / L, dx / L
        cizgiler.append(((x1 + nx * d, y1 + ny * d), (x2 + nx * d, y2 + ny * d)))
    out = []
    for i in range(n):
        a1, a2 = cizgiler[(i - 1) % n]
        b1, b2 = cizgiler[i]
        pt = _kesisim(a1, a2, b1, b2)
        if pt is None:
            return None
        out.append(pt)
    if alan(out) <= 1.0 or not all(icinde(q, p) for q in out):
        return None
    if not basit_mi(out):
        return None
    return out


def guvenli_offset(pts, d):
    for k in range(8):
        r = offset_polygon(pts, d * (0.75 ** k))
        if r:
            return r, d * (0.75 ** k)
    return None, 0.0


# ---------------------------------------------------------------- SVG/PNG

def _ascii(t):
    return (str(t).replace("ı", "i").replace("İ", "I").replace("ğ", "g").replace("Ğ", "G")
            .replace("ş", "s").replace("Ş", "S").replace("ç", "c").replace("Ç", "C")
            .replace("ö", "o").replace("Ö", "O").replace("ü", "u").replace("Ü", "U")
            .replace("²", "2").replace("—", "-").replace("·", "-"))


def _tr(v, ond=2):
    try:
        t = ("%%.%df" % ond) % float(v)
    except (TypeError, ValueError):
        return "-"
    return t.replace(".", ",")


def kuzey_oku(x, y, h):
    return [
        {"tip": "line", "katman": "CIZGI", "a": (x, y), "b": (x, y + h * 2)},
        {"tip": "line", "katman": "CIZGI", "a": (x - h * 0.35, y + h * 1.3), "b": (x, y + h * 2)},
        {"tip": "line", "katman": "CIZGI", "a": (x + h * 0.35, y + h * 1.3), "b": (x, y + h * 2)},
        {"tip": "text", "katman": "KROKI", "x": x, "y": y + h * 3.0, "yazi": "K",
         "h": h * 0.7, "aci": 0},
    ]


def olcek_cubugu(x, y, uzunluk, etiket, h):
    return [
        {"tip": "line", "katman": "CIZGI", "a": (x, y), "b": (x + uzunluk, y)},
        {"tip": "line", "katman": "CIZGI", "a": (x, y - h * 0.3), "b": (x, y + h * 0.6)},
        {"tip": "line", "katman": "CIZGI", "a": (x + uzunluk, y - h * 0.3), "b": (x + uzunluk, y + h * 0.6)},
        {"tip": "text", "katman": "OLCU", "x": x + uzunluk / 2, "y": y - h * 0.9, "yazi": etiket, "h": h},
    ]


def svg_yaz(path, entities, baslik, sinir=None):
    xs, ys = [], []
    for e in entities:
        if e.get("tip") == "polyline":
            for x, y in e.get("noktalar") or []:
                xs.append(x)
                ys.append(y)
        elif e.get("tip") == "line":
            for q in (e["a"], e["b"]):
                xs.append(q[0])
                ys.append(q[1])
        elif e.get("tip") == "text":
            xs.append(e["x"])
            ys.append(e["y"])
    if not xs:
        xs, ys = [0.0, 1.0], [0.0, 1.0]
    if sinir:
        x0, y0, x1, y1 = sinir
    else:
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
    pad = 0.0 if sinir else (max(x1 - x0, y1 - y0) * 0.08 + 0.5)
    vx, vy = x0 - pad, -(y1 + pad)
    vw, vh = (x1 - x0) + 2 * pad, (y1 - y0) + 2 * pad

    def tx(x):
        return x - vx

    def ty(y):
        return -y - vy

    parca = []
    for e in entities:
        tip, k = e.get("tip"), (e.get("katman") or "0").lower()
        if tip == "polyline":
            pts = " ".join("%.2f,%.2f" % (tx(x), ty(y)) for x, y in e.get("noktalar") or [])
            parca.append('<polygon points="%s" class="%s"/>' % (pts, k))
        elif tip == "line":
            parca.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" class="%s"/>'
                         % (tx(e["a"][0]), ty(e["a"][1]), tx(e["b"][0]), ty(e["b"][1]), k))
        elif tip == "text":
            yazi = _ascii(e.get("yazi", "")).replace("&", "&amp;").replace("<", "&lt;")
            boy = e.get("h", 1.0) * 0.9
            aci = -float(e.get("aci", 0.0))
            if e.get("arka"):
                w = 0.58 * boy * len(yazi) + boy * 0.8
                h = boy * 1.55
                parca.append(
                    '<g transform="translate(%.2f,%.2f) rotate(%.2f)">'
                    '<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="#ffffff" '
                    'fill-opacity="0.88" stroke="none"/>'
                    '<text x="0" y="0" font-size="%.2f" class="%s">%s</text></g>'
                    % (tx(e["x"]), ty(e["y"]), aci, -w / 2, -h / 2, w, h, boy, k, yazi))
            else:
                rot = (' transform="rotate(%.2f %.2f %.2f)"' % (aci, tx(e["x"]), ty(e["y"]))) if aci else ""
                parca.append('<text x="%.2f" y="%.2f" font-size="%.2f" class="%s"%s>%s</text>'
                             % (tx(e["x"]), ty(e["y"]), boy, k, rot, yazi))

    svg = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="%.0f" viewBox="0 0 %.2f %.2f">
<style>
  polygon {stroke-linejoin:round}
  .parsel {fill:#dcfce7;fill-opacity:0.35;stroke:#15803d;stroke-width:0.12}
  .cekme {fill:none;stroke:#d97706;stroke-width:0.07;stroke-dasharray:0.6 0.4}
  .olcu {fill:#b91c1c;stroke:none;font-family:sans-serif;text-anchor:middle}
  .cizgi {fill:none;stroke:#111827;stroke-width:0.09}
  .kroki {fill:#111827;stroke:none;font-family:sans-serif;text-anchor:middle}
  .olculine {stroke:#dc2626;stroke-width:0.05}
  text {dominant-baseline:middle;text-anchor:middle}
</style>
<rect x="0" y="0" width="%.2f" height="%.2f" fill="#ffffff"/>
<title>%s</title>
%s
</svg>
""" % (1400.0 * vh / vw if vw else 900, vw, vh, vw, vh, _ascii(baslik), "\n".join(parca))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    return path


def png_uret(svg_path, png_path):
    try:
        import cairosvg
        cairosvg.svg2png(url=svg_path, write_to=png_path, output_width=1600)
        return "cairosvg"
    except Exception:
        pass
    for cmd in (["rsvg-convert", "-w", "1600", "-o", png_path, svg_path],
                ["convert", "-density", "200", svg_path, png_path]):
        exe = shutil.which(cmd[0])
        if exe:
            try:
                subprocess.run([exe] + cmd[1:], check=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=60)
                if os.path.exists(png_path):
                    return cmd[0]
            except Exception:
                continue
    return None


def _metin_kutu(x, y, yazi, h, aci):
    """Metin icin yaklasik eksen-hizali kutu (dunya koordinati)."""
    w = 0.58 * h * len(str(yazi)) + 0.6 * h
    hh = 1.55 * h
    a = math.radians(aci)
    ca, sa = abs(math.cos(a)), abs(math.sin(a))
    ew, eh = w * ca + hh * sa, w * sa + hh * ca
    return (x - ew / 2, y - eh / 2, x + ew / 2, y + eh / 2)


def _kutu_birlestir(kutular):
    return (min(k[0] for k in kutular), min(k[1] for k in kutular),
            max(k[2] for k in kutular), max(k[3] for k in kutular))


def vaziyet_ciz(parse, cekme_poly, kimlik, alan_m2, cekme_degerleri, out_dir, baslik="VAZIYET 1/500"):
    """Parsel + cekme + olcular; alt %12'lik sabit baslik bandi, icerik 'contain'."""
    xs = [p[0] for p in parse]
    ys = [p[1] for p in parse]
    g = max(max(xs) - min(xs), max(ys) - min(ys))
    h_txt = max(0.8, g * 0.022)
    ent = [{"tip": "polyline", "katman": "PARSEL", "noktalar": parse}]
    if cekme_poly:
        ent.append({"tip": "polyline", "katman": "CEKME", "noktalar": cekme_poly})

    kutular = [(min(xs), min(ys), max(xs), max(ys))]
    yazilan = []
    liderler = []
    h_e = h_txt * 0.8            # etiket yazi boyu
    for i in range(len(parse)):
        a, b = parse[i], parse[(i + 1) % len(parse)]
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        nx, ny = dy / L, -dx / L
        if icinde((mx + nx * (max(2.5, g * 0.05) + 0.6), my + ny * (max(2.5, g * 0.05) + 0.6)), parse):
            nx, ny = -nx, -ny
        kisa = L < 3.0
        ofs = min(8.0, 4.0) if kisa else max(2.5, g * 0.05)
        if kisa:
            ofs = min(8.0, max(3.0, 0.9 * L + 1.2))
        px, py = mx + nx * ofs, my + ny * ofs
        # cok yakin iki etiket (or. ayni girintinin iki 0,84 m kenari) ust uste yazilmasin
        if any(math.hypot(px - q[0], py - q[1]) < 1.4 * h_txt for q in yazilan):
            continue
        yazilan.append((px, py))
        aci = math.degrees(math.atan2(dy, dx))
        if aci > 90:
            aci -= 180
        elif aci < -90:
            aci += 180
        if kisa:
            aci = 0.0                       # kisa kenar: yatay yaz
        # lider: kenar orta noktasindan (poligon uzerinde) etiket kutusunun kenarina kadar
        lider_gerekli = kisa or ofs > 0.6 * L
        if lider_gerekli:
            bosluk = 0.55 * h_e + 0.15 * h_txt
            ex, ey = px - nx * bosluk, py - ny * bosluk
            ent.append({"tip": "line", "katman": "CIZGI", "a": (mx, my), "b": (ex, ey)})
            liderler.append({"kenar_m": round(L, 2), "baslangic": [round(mx, 2), round(my, 2)],
                             "bitis": [round(ex, 2), round(ey, 2)],
                             "baslangic_kenar_mesafe_m": 0.0})
        yazi = "%s m" % _tr(L)
        ent.append({"tip": "text", "katman": "OLCU", "x": px, "y": py, "yazi": yazi,
                    "h": h_e, "aci": aci, "arka": True})
        kutular.append(_metin_kutu(px, py, yazi, h_e, aci))

    kb = _kutu_birlestir(kutular)
    # kuzey oku ve olcek cubugu icerik kutusunun disinda, banda degmeyecek sekilde
    kuzey = kuzey_oku(kb[2] + h_txt * 1.6, kb[3] - h_txt, h_txt * 0.8)
    olcek = olcek_cubugu(kb[0], kb[1] - h_txt * 2.6, 20.0, "20 m", h_txt * 0.7)
    ent += kuzey + olcek
    for e in kuzey + olcek:
        if e.get("tip") == "text":
            kutular.append(_metin_kutu(e["x"], e["y"], e["yazi"], e["h"], 0))
        elif e.get("tip") == "line":
            kutular.append((min(e["a"][0], e["b"][0]), min(e["a"][1], e["b"][1]),
                            max(e["a"][0], e["b"][0]), max(e["a"][1], e["b"][1])))
    kb = _kutu_birlestir(kutular)
    ic_w, ic_h = kb[2] - kb[0], kb[3] - kb[1]
    pad = 0.03 * max(ic_w, ic_h)

    # baslik bandi: tuval yuksekliginin %12'si (en az 3 satir)
    gap = max(2.2 * h_txt, 0.05 * ic_h)
    satirlar = ["ADA/PARSEL: %s" % kimlik,
                "ALAN: %s m2 | OLCEK 1/500" % _tr(alan_m2)]
    if cekme_poly:
        satirlar.append("CEKME: %s/%s/%s m" % (_tr(cekme_degerleri[0], 1),
                                               _tr(cekme_degerleri[1], 1),
                                               _tr(cekme_degerleri[2], 1)))
    else:
        satirlar.append("CEKME SINIRI CIZILEMEDI (offset gecersiz)")
    # tuval: icerik %88, baslik bandi %12 (banda gore yazi boyu)
    H = (ic_h + gap) / 0.88
    band = H - (ic_h + gap)
    h_baslik = min(h_txt * 0.8, band / (len(satirlar) * 1.45 + 0.6))
    y_alt = kb[1] - gap - band

    x_orta = (kb[0] + kb[2]) / 2.0
    y = kb[1] - gap - 0.75 * h_baslik
    for s_ in satirlar:
        ent.append({"tip": "text", "katman": "KROKI", "x": x_orta, "y": y,
                    "yazi": s_, "h": h_baslik})
        y -= 1.45 * h_baslik

    sinir = (kb[0] - pad, y_alt - pad, kb[2] + pad, kb[3] + pad)
    os.makedirs(out_dir, exist_ok=True)
    svg = svg_yaz(os.path.join(out_dir, "parsel.svg"), ent, "%s - %s" % (baslik, kimlik), sinir=sinir)
    png = os.path.join(out_dir, "parsel.png")
    arac = png_uret(svg, png)
    return {"svg": svg, "png": png if arac else None, "png_arac": arac,
            "band_orani": round(band / (sinir[3] - sinir[1]), 3),
            "band_ust": round(y_alt, 3),
            "liderler": liderler,
            "sinir": [round(v, 2) for v in sinir]}


