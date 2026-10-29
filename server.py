#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TKGM Parsel Sorgu MCP sunucusu (stdio, newline-delimited JSON-RPC 2.0).

Resmi/dokümante bir API değildir; parselsorgu.tkgm.gov.tr web arayüzünün
kullandığı cbsapi.tkgm.gov.tr uçlarını kullanır. Sadece okuma yapar.

Harici bağımlılık yok: Python 3.8+ stdlib.
"""

import hashlib
import json
import math
import os
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.request


BASE = "https://cbsapi.tkgm.gov.tr/megsiswebapi.v3.1/api"
IL_LIST_URL = "https://parselsorgu.tkgm.gov.tr/app/modules/administrativeQuery/data/ilListe.json"

HERE = os.path.dirname(os.path.abspath(__file__))


def _env_dir(var, default):
    value = os.environ.get(var)
    if value:
        return os.path.abspath(os.path.expanduser(value))
    return os.path.join(HERE, default)


OUT_DIR = _env_dir("PARSELIMAR_CIKTI", "cikti")
CACHE_DIR = _env_dir("PARSELIMAR_CACHE", "cache")

SERVER_NAME = "parsel-imar-v3"
SERVER_VERSION = "3.0.0"
PROTOCOL_VERSION = "2024-11-05"

MIN_INTERVAL = 0.40          # istekler arasi en az gecikme (sn) - siteyi yormamak icin
HTTP_TIMEOUT = 25
RETRIES = 3
LIST_TTL = 7 * 24 * 3600     # il/ilce/mahalle listeleri 7 gun onbellek
PARCEL_TTL = 3600            # parsel sorgulari 1 saat onbellek

_last_request = 0.0


# ---------------------------------------------------------------- yardimcilar

def log(msg):
    sys.stderr.write("[tkgm-parsel] %s\n" % msg)
    sys.stderr.flush()


def fold(text):
    """Turkce duyarli, aksan duyarsiz karsilastirma anahtari."""
    if text is None:
        return ""
    s = str(text).strip()
    s = s.replace("İ", "i").replace("I", "i").replace("ı", "i")
    s = s.replace("Ş", "s").replace("ş", "s")
    s = s.replace("Ğ", "g").replace("ğ", "g")
    s = s.replace("Ç", "c").replace("ç", "c")
    s = s.replace("Ö", "o").replace("ö", "o")
    s = s.replace("Ü", "u").replace("ü", "u")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s)
    return s.casefold()


def tr_lower(text):
    return fold(text)


def safe_name(text):
    s = str(text)
    s = re.sub(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşü_-]+", "_", s).strip("_")
    return s or "kayit"


def parse_area(value):
    """'1,092.00' veya '1.092,00' -> float(m2). Bilinmiyorsa None."""
    if value is None:
        return None
    s = str(value).strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def count_coords(geometry):
    if not isinstance(geometry, dict):
        return 0
    coords = geometry.get("coordinates")

    def walk(node):
        if isinstance(node, (int, float)):
            return 1
        if isinstance(node, list):
            return sum(walk(x) for x in node)
        return 0

    n = walk(coords)
    return n // 2 if n else 0


# ---------------------------------------------------------------- HTTP katmani

class ApiError(Exception):
    """Kullaniciya gosterilecek Turkce hata."""


def _ssl_context():
    ctx = ssl.create_default_context()
    # TKGM sertifika zinciri bazi ortamlarda dogrulanamiyor (bilinen durum).
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_CTX = _ssl_context()
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) mcp-tkgm-parsel/1.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9",
    "Referer": "https://parselsorgu.tkgm.gov.tr/",
    "Origin": "https://parselsorgu.tkgm.gov.tr",
}


def _cache_path(url):
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, key + ".json")


def _cache_get(url, ttl):
    try:
        with open(_cache_path(url), "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        if time.time() - blob.get("ts", 0) <= ttl:
            return blob.get("data")
    except Exception:
        pass
    return None


def _cache_put(url, data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = _cache_path(url) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ts": time.time(), "url": url, "data": data}, fh, ensure_ascii=False)
        os.replace(tmp, _cache_path(url))
    except Exception as exc:
        log("onbellek yazilamadi: %s" % exc)


def _throttle():
    global _last_request
    wait = MIN_INTERVAL - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()


def http_get_json(url, ttl=0):
    """GET + JSON. 404 -> ApiError('... bulunamadi'), ag hatasi -> 3 deneme."""
    if ttl:
        cached = _cache_get(url, ttl)
        if cached is not None:
            return cached

    last_error = None
    for attempt in range(1, RETRIES + 1):
        _throttle()
        req = urllib.request.Request(url, headers=_HEADERS)
        try:
            with urllib.request.urlopen(req, context=_CTX, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read()
            data = json.loads(raw.decode("utf-8"))
            if ttl:
                _cache_put(url, data)
            return data
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:
                pass
            text = body.decode("utf-8", "replace").strip()
            if exc.code == 404:
                msg = text
                try:
                    msg = json.loads(text).get("Message") or text
                except Exception:
                    pass
                raise ApiError("Kayit bulunamadi (HTTP 404). TKGM mesaji: %s" % (msg or "aciklama yok"))
            last_error = "HTTP %s %s" % (exc.code, text[:200])
            if exc.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = "%s: %s" % (type(exc).__name__, exc)
        except json.JSONDecodeError as exc:
            raise ApiError("TKGM beklenmeyen bir yanit dondurdu (JSON cozulemedi): %s" % exc)

        if attempt < RETRIES:
            time.sleep(0.8 * attempt)

    raise ApiError(
        "TKGM sunucusuna ulasilamadi (%d deneme). Son hata: %s. "
        "Site gecici olarak yanit vermiyor olabilir; birkac saniye sonra tekrar deneyin."
        % (RETRIES, last_error)
    )


# ---------------------------------------------------------------- veri erisimi

def _features(data):
    if isinstance(data, dict) and isinstance(data.get("features"), list):
        return data["features"]
    if isinstance(data, list):
        return data
    return []


def _props(feature):
    return (feature or {}).get("properties") or {}


def get_il_listesi():
    """[{id: sayisal plaka kodu, ad: 'Adana', guid: ...}]"""
    geo = http_get_json(BASE + "/maksIdariYapi/illiste", ttl=LIST_TTL)
    numeric = {}
    try:
        site = http_get_json(IL_LIST_URL, ttl=LIST_TTL)
        for feat in _features(site):
            p = _props(feat)
            if p.get("text") is not None and p.get("id") is not None:
                numeric[fold(p["text"])] = p["id"]
    except ApiError as exc:
        log("ilListe.json alinamadi, sadece GUID id kullanilacak: %s" % exc)

    out = []
    for feat in _features(geo):
        p = _props(feat)
        name = p.get("text")
        if not name:
            continue
        out.append({
            "id": numeric.get(fold(name), p.get("id")),
            "ad": name,
            "guid": p.get("id"),
        })
    out.sort(key=lambda x: fold(x["ad"]))
    return out


def get_ilceler(il_id):
    data = http_get_json(BASE + "/idariYapi/ilceListe/%s" % il_id, ttl=LIST_TTL)
    out = []
    for feat in _features(data):
        p = _props(feat)
        if p.get("text") is None:
            continue
        out.append({"id": p.get("id"), "ad": p["text"]})
    out.sort(key=lambda x: fold(x["ad"]))
    return out


def get_mahalleler(ilce_id):
    data = http_get_json(BASE + "/idariYapi/mahalleListe/%s" % ilce_id, ttl=LIST_TTL)
    out = []
    for feat in _features(data):
        p = _props(feat)
        if p.get("text") is None:
            continue
        out.append({"id": p.get("id"), "ad": p["text"]})
    out.sort(key=lambda x: fold(x["ad"]))
    return out


def _pick(items, name, kind):
    key = fold(name)
    exact = [i for i in items if fold(i["ad"]) == key]
    if exact:
        return exact[0]
    partial = [i for i in items if key and key in fold(i["ad"])]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise ApiError(
            "%s adi belirsiz: '%s'. Eslesenler: %s. Tam adi yazin."
            % (kind, name, ", ".join(i["ad"] for i in partial[:10]))
        )
    raise ApiError(
        "%s bulunamadi: '%s'. Mevcut adlardan bazilari: %s"
        % (kind, name, ", ".join(i["ad"] for i in items[:10]))
    )


def _parsel_ozet(feature, kaynak, ek=None):
    p = _props(feature)
    geometry = feature.get("geometry") or {}
    ozet = {
        "il": p.get("ilAd"),
        "ilce": p.get("ilceAd"),
        "mahalle": p.get("mahalleAd"),
        "mahalle_id": p.get("mahalleId"),
        "ada": p.get("adaNo"),
        "parsel": p.get("parselNo"),
        "ozet": p.get("ozet"),
        "alan_m2": parse_area(p.get("alan")),
        "alan_ham": p.get("alan"),
        "nitelik": p.get("nitelik"),
        "pafta": p.get("pafta"),
        "mevkii": p.get("mevkii"),
        "zemin_km_durum": p.get("zeminKmdurum"),
        "durum": p.get("durum"),
        "koordinat_sayisi": count_coords(geometry),
        "geometri_tipi": geometry.get("type"),
        "kaynak": kaynak,
    }
    if ek:
        ozet.update(ek)
    ozet["geojson_dosya"] = _save_geojson(feature, ozet)
    return ozet


def _save_geojson(feature, ozet):
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        ad = "%s_%s_%s.geojson" % (
            safe_name(ozet.get("mahalle") or ozet.get("mahalle_id")),
            safe_name(ozet.get("ada")),
            safe_name(ozet.get("parsel")),
        )
        path = os.path.join(OUT_DIR, ad)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(feature, fh, ensure_ascii=False)
        return path
    except Exception as exc:
        log("geojson yazilamadi: %s" % exc)
        return None


def parsel_sorgula_raw(mahalle_id, ada, parsel):
    url = "%s/parsel/%s/%s/%s" % (BASE, mahalle_id, ada, parsel)
    try:
        data = http_get_json(url, ttl=PARCEL_TTL)
    except ApiError as exc:
        if "HTTP 404" in str(exc) or "bulunamadi (HTTP 404)" in str(exc):
            raise ApiError(
                "Parsel bulunamadi: Mahalle Id = %s, Ada = %s, Parsel = %s. "
                "Ada/parsel numarasini kontrol edin veya parsel_sorgula_koordinat ile deneyin. (%s)"
                % (mahalle_id, ada, parsel, exc)
            )
        raise
    if isinstance(data, dict) and data.get("type") == "Feature":
        return data
    feats = _features(data)
    if feats:
        return feats[0]
    raise ApiError(
        "Parsel bulunamadi: Mahalle Id = %s, Ada = %s, Parsel = %s (TKGM bos yanit dondurdu)."
        % (mahalle_id, ada, parsel)
    )


# ---------------------------------------------------------------- TM projeksiyon
#
# Turkiye ulusal 3 derecelik Transverse Mercator (TUREF / ITRF96, GRS80).
# Parametreler EPSG'den alindi: lat_0=0, lon_0=3*zon, k=1, FE=500000, FN=0
# (EPSG:5253 = TM27 ... EPSG:5262 = TM54).

GRS80_A = 6378137.0
GRS80_F = 1.0 / 298.257222101
GRS80_E2 = GRS80_F * (2.0 - GRS80_F)

TM_K0 = 1.0
TM_FE = 500000.0
TM_FN = 0.0
TM_EPSG_BASE = 5253          # EPSG:5253 = TUREF / TM27 (merkez boylam 27E)
TM_EPSG_BASE_LON = 27.0


def tm_zone(lon):
    """Boylamdan 3 derecelik TM zon numarasi (merkez boylam = 3*zon)."""
    zone = int(round(float(lon) / 3.0))
    return max(9, min(18, zone))


def tm_merkez_boylam(lon):
    return 3.0 * tm_zone(lon)


def tm_epsg(lon):
    """Boylamdan TUREF/TM EPSG kodu (27E -> 5253, 42E -> 5258)."""
    return TM_EPSG_BASE + int(round((tm_merkez_boylam(lon) - TM_EPSG_BASE_LON) / 3.0))


def _meridyen_yayi(phi):
    """GRS80 elipsoidinde ekvatordan phi enlemine meridyen yay uzunlugu (m)."""
    e2 = GRS80_E2
    e4 = e2 * e2
    e6 = e4 * e2
    return GRS80_A * (
        (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
        - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
        + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
        - (35 * e6 / 3072) * math.sin(6 * phi)
    )


def tm_forward(lat, lon, lon0=None, k0=TM_K0, fe=TM_FE, fn=TM_FN):
    """WGS84/cografi (derece) -> TM (dogu, kuzey) metre. Snyder serisi (A^6)."""
    if lon0 is None:
        lon0 = tm_merkez_boylam(lon)
    phi = math.radians(float(lat))
    lam = math.radians(float(lon) - float(lon0))
    e2 = GRS80_E2
    ep2 = e2 / (1.0 - e2)
    sin_p = math.sin(phi)
    cos_p = math.cos(phi)
    tan_p = math.tan(phi)
    n = GRS80_A / math.sqrt(1.0 - e2 * sin_p * sin_p)
    t = tan_p * tan_p
    c = ep2 * cos_p * cos_p
    a = cos_p * lam
    m = _meridyen_yayi(phi)

    east = fe + k0 * n * (
        a
        + (1 - t + c) * a ** 3 / 6.0
        + (5 - 18 * t + t * t + 72 * c - 58 * ep2) * a ** 5 / 120.0
    )
    north = fn + k0 * (
        m + n * tan_p * (
            a * a / 2.0
            + (5 - t + 9 * c + 4 * c * c) * a ** 4 / 24.0
            + (61 - 58 * t + t * t + 600 * c - 330 * ep2) * a ** 6 / 720.0
        )
    )
    return east, north


def tm_inverse(east, north, lon0, k0=TM_K0, fe=TM_FE, fn=TM_FN):
    """TM (metre) -> cografi (derece). tm_forward'in tersi."""
    e2 = GRS80_E2
    ep2 = e2 / (1.0 - e2)
    m = (float(north) - fn) / k0
    mu = m / (GRS80_A * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
        + (1097 * e1 ** 4 / 512) * math.sin(8 * mu)
    )
    sin_p = math.sin(phi1)
    cos_p = math.cos(phi1)
    tan_p = math.tan(phi1)
    c1 = ep2 * cos_p * cos_p
    t1 = tan_p * tan_p
    n1 = GRS80_A / math.sqrt(1 - e2 * sin_p * sin_p)
    r1 = GRS80_A * (1 - e2) / (1 - e2 * sin_p * sin_p) ** 1.5
    d = (float(east) - fe) / (n1 * k0)

    phi = phi1 - (n1 * tan_p / r1) * (
        d * d / 2.0
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * ep2) * d ** 4 / 24.0
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * ep2 - 3 * c1 * c1) * d ** 6 / 720.0
    )
    lam = (
        d
        - (1 + 2 * t1 + c1) * d ** 3 / 6.0
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * ep2 + 24 * t1 * t1) * d ** 5 / 120.0
    ) / cos_p
    return math.degrees(phi), float(lon0) + math.degrees(lam)


# ---------------------------------------------------------------- DXF yazici
#
# AutoCAD R12 (AC1009) ASCII. POLYLINE/VERTEX/SEQEND + TEXT en genis uyumlu
# ikilidir; tum CAD programlari (AutoCAD, LibreCAD, QCAD, BricsCAD, DWG TrueView)
# R12 DXF okur.

DXF_LINE_LAYER = "PARSEL"
DXF_LABEL_LAYER = "PARSEL_ETIKET"
DXF_IMAR_LAYER = "IMAR"


def _dxf_num(value, nd=3):
    """DXF gercek sayisi: nd ondalik, -0.0 temizligi."""
    v = round(float(value), nd)
    if v == 0:
        v = 0.0
    return ("%.*f" % (nd, v)).rstrip("0").rstrip(".") or "0.0"


def _dxf_pairs(*pairs):
    return "".join("%s\n%s\n" % (code, value) for code, value in pairs)


def _polygon_rings(geometry):
    """GeoJSON Polygon/MultiPolygon -> dis halkalar listesi [(x, y), ...]."""
    if not isinstance(geometry, dict):
        return []
    gtype = geometry.get("type")
    coords = geometry.get("coordinates") or []
    rings = []

    def ring_from(raw, dim):
        out = []
        for pt in raw or []:
            if not isinstance(pt, (list, tuple)) or len(pt) < dim:
                continue
            out.append((float(pt[0]), float(pt[1])))
        return out

    if gtype == "Polygon":
        if coords:
            rings.append(ring_from(coords[0], 2))
    elif gtype == "MultiPolygon":
        for poly in coords:
            if poly:
                rings.append(ring_from(poly[0], 2))
    elif gtype == "Point":
        rings.append(ring_from([coords], 2))
    return [r for r in rings if len(r) >= 3]


def _ring_alan(ring):
    """Shoelace alani (mutlak, birim^2)."""
    s = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _ring_merkez(ring):
    """Alan agirlikli merkez (etiket yerlestirmek icin)."""
    a = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        f = x1 * y2 - x2 * y1
        a += f
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    if a == 0:
        n = float(len(ring))
        return sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n
    return cx / (3.0 * a), cy / (3.0 * a)


def yaz_dxf(path, halkalar, etiketler=None, ondalik=3):
    """Kapali POLYLINE halkalari + TEXT etiketleri iceren R12 ASCII DXF yazar.

    halkalar : [[(x, y), ...], ...]  (kapali kabul edilir, son nokta tekrari atilir)
    etiketler: [{"x":, "y":, "yazi":, "yukseklik":, "aci":, "katman":}]
    ondalik  : koordinat ondalik basamak sayisi (metre=3, derece=8)
    """
    def f(v):
        return _dxf_num(v, ondalik)

    xs = []
    ys = []
    for ring in halkalar:
        for x, y in ring:
            xs.append(x)
            ys.append(y)
    for et in etiketler or []:
        xs.append(float(et.get("x", 0.0)))
        ys.append(float(et.get("y", 0.0)))
    if not xs:
        raise ApiError("DXF icin geometri yok.")
    extmin = (min(xs), min(ys))
    extmax = (max(xs), max(ys))
    pad = max((extmax[1] - extmin[1]) * 0.05, 1e-6)

    parts = []
    # --- HEADER
    parts.append(_dxf_pairs(
        (0, "SECTION"), (2, "HEADER"),
        (9, "$ACADVER"), (1, "AC1009"),
        (9, "$DWGCODEPAGE"), (3, "ANSI_1254"),
        (9, "$INSBASE"), (10, "0.0"), (20, "0.0"), (30, "0.0"),
        (9, "$EXTMIN"), (10, f(extmin[0])), (20, f(extmin[1] - pad)), (30, "0.0"),
        (9, "$EXTMAX"), (10, f(extmax[0])), (20, f(extmax[1] + pad)), (30, "0.0"),
        (9, "$LTSCALE"), (40, "1.0"),
        (0, "ENDSEC"),
    ))
    # --- TABLES
    parts.append(_dxf_pairs(
        (0, "SECTION"), (2, "TABLES"),
        (0, "TABLE"), (2, "LTYPE"), (70, "1"),
        (0, "LTYPE"), (2, "CONTINUOUS"), (70, "0"), (3, "Solid line"),
        (72, "65"), (73, "0"), (40, "0.0"),
        (0, "ENDTAB"),
        (0, "TABLE"), (2, "LAYER"), (70, "3"),
        (0, "LAYER"), (2, DXF_LINE_LAYER), (70, "0"), (62, "7"), (6, "CONTINUOUS"),
        (0, "LAYER"), (2, DXF_LABEL_LAYER), (70, "0"), (62, "3"), (6, "CONTINUOUS"),
        (0, "LAYER"), (2, DXF_IMAR_LAYER), (70, "0"), (62, "1"), (6, "CONTINUOUS"),
        (0, "ENDTAB"),
        (0, "ENDSEC"),
    ))
    # --- BLOCKS (bos)
    parts.append(_dxf_pairs((0, "SECTION"), (2, "BLOCKS"), (0, "ENDSEC")))
    # --- ENTITIES
    ent = [(0, "SECTION"), (2, "ENTITIES")]
    for ring in halkalar:
        pts = list(ring)
        if len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) < 1e-9 and abs(pts[0][1] - pts[-1][1]) < 1e-9:
            pts.pop()
        ent += [
            (0, "POLYLINE"), (8, DXF_LINE_LAYER), (66, "1"), (70, "1"),
            (10, "0.0"), (20, "0.0"), (30, "0.0"),
        ]
        for x, y in pts:
            ent += [
                (0, "VERTEX"), (8, DXF_LINE_LAYER),
                (10, f(x)), (20, f(y)), (30, "0.0"), (70, "0"),
            ]
        ent += [(0, "SEQEND"), (8, DXF_LINE_LAYER)]

    for label in etiketler or []:
        yazi = str(label.get("yazi") or "")
        yazi = yazi.replace("\r", " ").replace("\n", " ")
        # R12 DXF ASCII guvenli: ASCII disi karakterleri yumusak Turkce'ye cevir
        yazi = (yazi.replace("ı", "i").replace("İ", "I").replace("ğ", "g")
                     .replace("Ğ", "G").replace("ş", "s").replace("Ş", "S")
                     .replace("ç", "c").replace("Ç", "C").replace("ö", "o")
                     .replace("Ö", "O").replace("ü", "u").replace("Ü", "U")
                     .replace("²", "2").replace("—", "-"))
        ent += [
            (0, "TEXT"), (8, label.get("katman") or DXF_LABEL_LAYER),
            (10, f(label.get("x", 0.0))), (20, f(label.get("y", 0.0))), (30, "0.0"),
            (40, f(label.get("yukseklik", 1.0))),
            (1, yazi), (50, _dxf_num(label.get("aci", 0.0))),
            (72, "1"), (11, f(label.get("x", 0.0))),
            (21, f(label.get("y", 0.0))), (31, "0.0"),
            (73, "2"),
        ]
    ent.append((0, "ENDSEC"))
    parts.append(_dxf_pairs(*ent))
    parts.append(_dxf_pairs((0, "EOF")))

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="ascii", errors="replace", newline="\n") as fh:
        fh.write("".join(parts))
    return path


def _dxf_etiketleri(halkalar, kimlik, alan_m2, birim, katsayi=1.0):
    """Her halka icin kose numaralari + merkezde alan etiketi."""
    etiketler = []
    for ring in halkalar:
        pts = list(ring)
        if len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) < 1e-9 and abs(pts[0][1] - pts[-1][1]) < 1e-9:
            pts.pop()
        cx, cy = _ring_merkez(pts)
        cap = abs((max(p[0] for p in pts) - min(p[0] for p in pts)) *
                  (max(p[1] for p in pts) - min(p[1] for p in pts)))
        yukseklik = max(math.sqrt(cap) / 22.0, 0.4 if birim == "metre" else 0.00002)
        for i, (x, y) in enumerate(pts, 1):
            etiketler.append({"x": x, "y": y, "yazi": str(i),
                              "yukseklik": yukseklik * 0.7})
        alan_yazi = ("%.2f m2" % alan_m2) if alan_m2 is not None else ""
        etiketler.append({"x": cx, "y": cy, "yukseklik": yukseklik,
                          "yazi": ("%s  %s" % (kimlik, alan_yazi)).strip()})
    return etiketler

def _imar_etiketleri(halkalar, imar_kayit, birim):
    """DXF 'IMAR' katmani: emsal/TAKS/KAKS + plan adi (hesap yok)."""
    if not imar_kayit or imar_kayit.get("durum") != "ok":
        return []
    xs = [p[0] for r in halkalar for p in r]
    ys = [p[1] for r in halkalar for p in r]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    h = max(span * 0.022, 0.5) if birim == "metre" else max(span * 0.022, 0.00002)

    def fmt(v, son=""):
        if v is None:
            return "-"
        if isinstance(v, float):
            txt = ("%.2f" % v).rstrip("0").rstrip(".")
        else:
            txt = str(v)
        return txt + son

    satirlar = [
        "IMAR: Emsal %s | TAKS %s | KAKS %s" % (fmt(imar_kayit.get("emsal")),
                                                fmt(imar_kayit.get("taks")),
                                                fmt(imar_kayit.get("kaks"))),
        "Plan: %s" % (imar_kayit.get("plan_adi") or "-"),
        "Kullanim: %s" % (imar_kayit.get("kullanim_karari") or "-"),
    ]
    etiketler = []
    y = min(ys) - h * 2.0
    x_orta = (min(xs) + max(xs)) / 2.0
    for satir in satirlar:
        etiketler.append({"x": x_orta, "y": y, "yazi": satir,
                          "yukseklik": h, "katman": DXF_IMAR_LAYER})
        y -= h * 1.6
    return etiketler


def parsel_dxf_uret(feature, cozum, crs="tm", out_dir=None, imar_kayit=None):
    """Cozulmus bir parsel Feature'indan DXF uretir (ag erisimi yok)."""
    if crs not in ("tm", "wgs84", "both"):
        raise ApiError("crs 'tm', 'wgs84' veya 'both' olmali (gelen: %s)." % crs)
    hedef = out_dir or OUT_DIR
    halkalar_ll = _polygon_rings(feature.get("geometry") or {})
    if not halkalar_ll:
        raise ApiError("Parsel geometrisi cizilebilir bir poligon degil (geometri tipi: %s)."
                       % (feature.get("geometry") or {}).get("type"))

    p = _props(feature)
    alan_m2 = parse_area(p.get("alan"))
    koordinat_sayisi = count_coords(feature.get("geometry") or {})
    kimlik = "%s/%s" % (cozum.get("ada"), cozum.get("parsel"))
    dosya_temel = "%s_%s_%s" % (safe_name(cozum.get("mahalle")), safe_name(cozum.get("ada")),
                                safe_name(cozum.get("parsel")))
    os.makedirs(hedef, exist_ok=True)

    tum_noktalar = [pt for ring in halkalar_ll for pt in ring]
    lon_ort = sum(pt[0] for pt in tum_noktalar) / float(len(tum_noktalar))
    lon0 = tm_merkez_boylam(lon_ort)
    epsg = tm_epsg(lon_ort)

    dosyalar = []
    istenen = ["tm", "wgs84"] if crs == "both" else [crs]

    for secim in istenen:
        if secim == "tm":
            halkalar = [[tm_forward(lat, lon, lon0) for lon, lat in ring] for ring in halkalar_ll]
            birim = "metre"
            crs_adi = "TUREF / TM%d (ITRF96, 3 derece Gauss-Kruger)" % int(lon0)
            epsg_kodu = epsg
            ek = {"merkez_boylam": lon0, "olcek_faktoru": TM_K0, "false_easting": TM_FE}
        else:
            halkalar = [[(lon, lat) for lon, lat in ring] for ring in halkalar_ll]
            birim = "derece"
            crs_adi = "WGS84 (cografi, EPSG:4326)"
            epsg_kodu = 4326
            ek = {"uyari": "WGS84 DXF'te 1 birim = 1 derece; olcekli/metrik cizim icin crs='tm' kullanin."}

        etiketler = _dxf_etiketleri(halkalar, kimlik, alan_m2, birim)
        imar_etiket = _imar_etiketleri(halkalar, imar_kayit, birim)
        etiketler.extend(imar_etiket)
        path = os.path.join(hedef, "%s_%s.dxf" % (dosya_temel, secim))
        yaz_dxf(path, halkalar, etiketler, ondalik=3 if birim == "metre" else 8)

        kayit = {
            "dosya": path,
            "crs": crs_adi,
            "epsg": epsg_kodu,
            "birim": birim,
            "koordinat_sayisi": sum(len(r) for r in halkalar),
            "halka_sayisi": len(halkalar),
            "kapali": True,
            "etiket_sayisi": len(etiketler),
            "imar_katmani": bool(imar_etiket),
        }
        if birim == "metre":
            kayit["alan_hesap_m2"] = round(sum(_ring_alan(r) for r in halkalar), 2)
        kayit.update(ek)
        dosyalar.append(kayit)

    return {
        "parsel": cozum,
        "alan_m2": alan_m2,
        "koordinat_sayisi": koordinat_sayisi,
        "geometri_tipi": (feature.get("geometry") or {}).get("type"),
        "dosyalar": dosyalar,
        "not": ("DXF = AutoCAD R12 (AC1009) ASCII, katmanlar: %s (kapali POLYLINE), %s (kose/alan "
                "TEXT), %s (imar etiketleri). DWG uretilmez; DXF tum CAD programlarinda acilir."
                % (DXF_LINE_LAYER, DXF_LABEL_LAYER, DXF_IMAR_LAYER)),
    }

# ---------------------------------------------------------------- v3 araclari

import imar as imar_mod
import cizim
import kaynak
import rapor

SERVER_NAME = "parsel-imar-v3"
SERVER_VERSION = "3.0.0"
_last_error = {"zaman": None, "arac": None, "mesaj": None}


def _ts():
    return imar_mod.now_tr()


def _json_yaz(yol, veri):
    os.makedirs(os.path.dirname(yol) or ".", exist_ok=True)
    with open(yol, "w", encoding="utf-8") as fh:
        json.dump(veri, fh, ensure_ascii=False, indent=2)
    return yol


def _parsel_veri(feature, cozum):
    p = _props(feature)
    return {
        "il": cozum["il"], "ilce": cozum["ilce"], "mahalle": cozum["mahalle"],
        "ada": cozum["ada"], "parsel": cozum["parsel"],
        "alan_m2": parse_area(p.get("alan")),
        "nitelik": p.get("nitelik"), "pafta": p.get("pafta"), "mevkii": p.get("mevkii"),
        "zemin_km": p.get("zeminKmdurum"), "durum": p.get("durum"),
        "koordinat_sayisi": count_coords(feature.get("geometry") or {}),
        "geometri_tipi": (feature.get("geometry") or {}).get("type"),
        "geojson_dosya": _save_geojson(feature, {
            "mahalle": cozum["mahalle"], "ada": cozum["ada"], "parsel": cozum["parsel"]}),
    }


def _kaynak_cekim(il, ilce, mahalle, ada, parsel, out_dir, tkgm=False):
    """Iki URL: KEOS imar.aspx (parsel id ile) ve opsiyonel TKGM sayfasi."""
    adaylar = imar_mod.parsel_adaylari(il, ada, parsel)
    aday = imar_mod._sec(adaylar, mahalle)
    cfg = imar_mod.ILLER.get(il) or {}
    url = (cfg.get("base") or "") + "imar.aspx?parselid=%s" % aday["parsel_id"]
    sonuc = kaynak.kaynak_sayfa(url, out_dir, "kaynak_imar", yatay=True)
    sonuc["parsel_id"] = aday["parsel_id"]
    sonuc["tapu_mahalle"] = aday.get("tapu_mahalle")
    if tkgm:
        sonuc["tkgm_not"] = ("TKGM sayfasi otomatik alinmadi (kullanim kosullari modali); "
                             "TKGM verisi ozet tabloda yer aliyor.")
    return sonuc


def _tkgm_coz(il, ilce, mahalle, ada, parsel):
    for ad, val in (("il", il), ("ilce", ilce), ("mahalle", mahalle), ("ada", ada), ("parsel", parsel)):
        if val is None or str(val).strip() == "":
            raise ApiError("%s zorunlu (TKGM parsel bilgisi icin)." % ad)
    il_k = _pick(get_il_listesi(), il, "Il")
    ilce_k = _pick(get_ilceler(il_k["id"]), ilce, "Ilce")
    mah_k = _pick(get_mahalleler(ilce_k["id"]), mahalle, "Mahalle")
    ada, parsel = str(ada).strip(), str(parsel).strip()
    feature = parsel_sorgula_raw(mah_k["id"], ada, parsel)
    return feature, {"il": il_k["ad"], "il_id": il_k["id"], "ilce": ilce_k["ad"],
                     "ilce_id": ilce_k["id"], "mahalle": mah_k["ad"], "mahalle_id": mah_k["id"],
                     "ada": ada, "parsel": parsel}


def tool_parsel(args):
    il, ilce = args.get("il"), args.get("ilce")
    mahalle, ada, parsel = args.get("mahalle"), args.get("ada"), args.get("parsel")
    lat, lon = args.get("lat"), args.get("lon")
    if lat is not None and lon is not None:
        try:
            lat = float(str(lat).replace(",", "."))
            lon = float(str(lon).replace(",", "."))
        except ValueError:
            raise ApiError("lat/lon sayisal olmali.")
        url = "%s/parsel/%.6f/%.6f/" % (BASE, lat, lon)
        try:
            data = http_get_json(url, ttl=PARCEL_TTL)
        except ApiError as exc:
            raise ApiError("Bu koordinatta parsel bulunamadi (lat=%.6f, lon=%.6f)." % (lat, lon),
                           "bulunamadi")
        feature = data if isinstance(data, dict) and data.get("type") == "Feature" else (_features(data) or [None])[0]
        if not feature:
            raise ApiError("Bu koordinatta parsel bulunamadi.")
        p = _props(feature)
        cozum = {"il": p.get("ilAd"), "ilce": p.get("ilceAd"), "mahalle": p.get("mahalleAd"),
                 "ada": p.get("adaNo"), "parsel": p.get("parselNo")}
        veri = _parsel_veri(feature, cozum)
        veri["sorgu_koordinat"] = {"lat": lat, "lon": lon}
        veri["zaman"] = _ts()
        return veri

    feature, cozum = _tkgm_coz(il, ilce, mahalle, ada, parsel)
    veri = _parsel_veri(feature, cozum)
    veri["zaman"] = _ts()
    return veri


def tool_imar_sorgula(args):
    il, ilce = args.get("il"), args.get("ilce")
    mahalle, ada, parsel = args.get("mahalle"), args.get("ada"), args.get("parsel")
    kayit = imar_mod.imar_sorgula(il, ilce, mahalle, ada, parsel)
    kayit["zaman"] = _ts()
    return kayit


def tool_imar_kapsam(_args):
    iller = []
    for ad in sorted(imar_mod.ILLER, key=imar_mod.fold):
        cfg = imar_mod.ILLER[ad]
        iller.append({"il": ad, "durum": cfg.get("durum"),
                      "portal": cfg.get("base") or cfg.get("url"), "not": cfg.get("not")})
    aktif = [i for i in iller if i["durum"] == "aktif"]
    return {"adet": len(iller), "aktif_adet": len(aktif), "iller": iller,
            "durum_aciklama": {"aktif": "ada/parsel ile imar sorgusu calisiyor",
                               "giris_gerekli": "portal giris istiyor", "captcha": "captcha var",
                               "bakimda": "portal bakimda", "modul_yok": "imar modulu 404",
                               "erisilemedi": "portal yanit vermiyor",
                               "zaman_asimi": "imar sayfasi yanit vermiyor"},
            "zaman": _ts()}


def tool_liste(args):
    tur = str(args.get("tur") or "il").strip().lower()
    if tur == "il":
        return {"tur": tur, "iller": [{"id": i["id"], "ad": i["ad"]} for i in get_il_listesi()],
                "zaman": _ts()}
    if tur == "ilce":
        il_id = args.get("il_id")
        if il_id is None:
            raise ApiError("ilce listesi icin il_id gerekli.")
        return {"tur": tur, "il_id": il_id, "ilceler": get_ilceler(str(il_id)), "zaman": _ts()}
    if tur == "mahalle":
        ilce_id = args.get("ilce_id")
        if ilce_id is None:
            raise ApiError("mahalle listesi icin ilce_id gerekli.")
        return {"tur": tur, "ilce_id": ilce_id, "mahalleler": get_mahalleler(str(ilce_id)),
                "zaman": _ts()}
    if tur == "imar_il":
        return {"tur": tur, "iller": sorted(imar_mod.ILLER), "zaman": _ts()}
    if tur == "imar_ilce":
        ad, ilceler = imar_mod.get_ilceler(args.get("il"))
        return {"tur": tur, "il": ad, "ilceler": ilceler, "zaman": _ts()}
    if tur == "imar_mahalle":
        il, ilce = args.get("il"), args.get("ilce")
        ilce_id = None
        if ilce:
            ad, ilceler = imar_mod.get_ilceler(il)
            es = [i for i in ilceler if imar_mod.fold(i["ad"]) == imar_mod.fold(ilce)]
            if not es:
                raise ApiError("%s icin ilce bulunamadi: %s" % (ad, ilce), "bulunamadi")
            ilce_id = es[0]["id"]
        ad, mahalleler = imar_mod.get_mahalleler(il, ilce_id)
        _, tapu = imar_mod.get_tapu_mahalleler(il, ilce_id)
        return {"tur": tur, "il": ad, "ilce": ilce, "mahalleler": mahalleler,
                "tapu_mahalleler": tapu, "zaman": _ts()}
    raise ApiError("tur 'il','ilce','mahalle','imar_il','imar_ilce','imar_mahalle' olmali.")


def _proj(feature):
    halkalar = _polygon_rings(feature.get("geometry") or {})
    if not halkalar:
        raise ApiError("Parsel geometrisi poligon degil.")
    pts = halkalar[0]
    lon0 = tm_merkez_boylam(sum(p[0] for p in pts) / float(len(pts)))
    tm = [tm_forward(lat, lon, lon0) for lon, lat in pts]
    if tm[0] == tm[-1]:
        tm = tm[:-1]
    return tm, lon0, tm_epsg(pts[0][0])


def tool_cizim(args):
    il, ilce = args.get("il"), args.get("ilce")
    mahalle, ada, parsel = args.get("mahalle"), args.get("ada"), args.get("parsel")
    crs = str(args.get("crs") or "tm").strip().lower()
    if crs not in ("tm", "wgs84", "both"):
        raise ApiError("crs 'tm', 'wgs84' veya 'both' olmali.")
    feature, cozum = _tkgm_coz(il, ilce, mahalle, ada, parsel)
    imar_kayit = None
    try:
        imar_kayit = imar_mod.imar_sorgula(il, ilce, mahalle, ada, parsel)
    except imar_mod.ApiError:
        imar_kayit = None

    klasor = "%s_%s_%s" % (safe_name(cozum["mahalle"]), safe_name(cozum["ada"]), safe_name(cozum["parsel"]))
    out_dir = os.path.join(OUT_DIR, "v3", klasor)
    os.makedirs(out_dir, exist_ok=True)
    dxf = parsel_dxf_uret(feature, cozum, crs, out_dir=out_dir, imar_kayit=imar_kayit)
    kaynak_dxf = (dxf.get("dosyalar") or [{}])[0].get("dosya")
    hedef = os.path.join(out_dir, "parsel.dxf")
    if kaynak_dxf and os.path.abspath(kaynak_dxf) != os.path.abspath(hedef):
        os.replace(kaynak_dxf, hedef)

    tm, lon0, epsg = _proj(feature)
    cekme = (imar_kayit or {}).get("cekme_mesafeleri") or {}
    deger = (cekme.get("on"), cekme.get("yan"), cekme.get("arka"))
    d = max([x for x in deger if x] or [0])
    cekme_poly, uygulanan = cizim.guvenli_offset(tm, d) if d else (None, 0.0)
    resim = cizim.vaziyet_ciz(tm, cekme_poly, "%s/%s" % (cozum["ada"], cozum["parsel"]),
                              parse_area(_props(feature).get("alan")), deger, out_dir)
    return {"parsel": cozum, "klasor": out_dir,
            "dxf": hedef, "png": resim.get("png"), "svg": resim.get("svg"),
            "png_arac": resim.get("png_arac"),
            "cekim": {"uygulanan_m": round(uygulanan, 2), "limit_m": d,
                      "cizildi": bool(cekme_poly)},
            "epsg": epsg, "merkez_boylam": lon0,
            "uyarilar": ([] if cekme_poly else ["Çekme sınırı çizilemedi (offset geçersiz)."]),
            "zaman": _ts()}


def tool_kaynak_sayfa(args):
    il, ilce = args.get("il"), args.get("ilce")
    mahalle, ada, parsel = args.get("mahalle"), args.get("ada"), args.get("parsel")
    klasor = "%s_%s_%s" % (safe_name(mahalle), safe_name(ada), safe_name(parsel))
    out_dir = os.path.join(OUT_DIR, "v3", klasor)
    os.makedirs(out_dir, exist_ok=True)
    sonuc = _kaynak_cekim(il, ilce, mahalle, ada, parsel, out_dir,
                          tkgm=bool(args.get("tkgm", False)))
    sonuc["zaman"] = _ts()
    return sonuc


def tool_paket(args):
    il, ilce = args.get("il"), args.get("ilce")
    mahalle, ada, parsel = args.get("mahalle"), args.get("ada"), args.get("parsel")
    kaynak_dahil = bool(args.get("kaynak_dahil", True))
    tkgm = bool(args.get("kaynak_sayfa_tkgm", False))

    feature, cozum = _tkgm_coz(il, ilce, mahalle, ada, parsel)
    veri = {"sorgu": {"il": cozum["il"], "ilce": cozum["ilce"], "mahalle": cozum["mahalle"],
                      "ada": cozum["ada"], "parsel": cozum["parsel"], "zaman": _ts()}}
    veri["parsel"] = _parsel_veri(feature, cozum)
    imar_hata = None
    try:
        veri["imar"] = imar_mod.imar_sorgula(il, ilce, mahalle, ada, parsel)
    except imar_mod.ApiError as exc:
        veri["imar"] = {}
        imar_hata = exc.as_dict()
    veri["imar_hata"] = imar_hata
    veri["kaynak"] = {
        "tkgm": "https://cbsapi.tkgm.gov.tr/megsiswebapi.v3.1/api/parsel/%s/%s/%s"
                % (cozum["mahalle_id"], cozum["ada"], cozum["parsel"]),
        "imar": (veri["imar"].get("kaynak") or {}).get("sorgu_url"),
    }
    veri["zaman"] = _ts()

    klasor = "%s_%s_%s" % (safe_name(cozum["mahalle"]), safe_name(cozum["ada"]), safe_name(cozum["parsel"]))
    out_dir = os.path.join(OUT_DIR, "v3", klasor)
    os.makedirs(out_dir, exist_ok=True)
    dosyalar = {}

    # 1) cizim (dxf + png)
    dxf = parsel_dxf_uret(feature, cozum, "tm", out_dir=out_dir, imar_kayit=veri["imar"] or None)
    kdxf = (dxf.get("dosyalar") or [{}])[0].get("dosya")
    hedef = os.path.join(out_dir, "parsel.dxf")
    if kdxf and os.path.abspath(kdxf) != os.path.abspath(hedef):
        os.replace(kdxf, hedef)
    dosyalar["parsel.dxf"] = hedef

    tm, lon0, epsg = _proj(feature)
    cekme = (veri["imar"] or {}).get("cekme_mesafeleri") or {}
    deger = (cekme.get("on"), cekme.get("yan"), cekme.get("arka"))
    d = max([x for x in deger if x] or [0])
    cekme_poly, uygulanan = cizim.guvenli_offset(tm, d) if d else (None, 0.0)
    resim = cizim.vaziyet_ciz(tm, cekme_poly, "%s/%s" % (cozum["ada"], cozum["parsel"]),
                              veri["parsel"].get("alan_m2"), deger, out_dir)
    dosyalar["parsel.png"] = resim.get("png")
    if resim.get("svg"):
        dosyalar["parsel.svg"] = resim.get("svg")
    gj = os.path.join(out_dir, "parsel.geojson")
    gj_kaynak = veri["parsel"].get("geojson_dosya")
    if gj_kaynak and os.path.exists(gj_kaynak):
        if os.path.abspath(gj_kaynak) != os.path.abspath(gj):
            os.replace(gj_kaynak, gj)
        dosyalar["parsel.geojson"] = gj
        veri["parsel"]["geojson_dosya"] = gj
    veri["cizim"] = {"dxf": hedef, "png": resim.get("png"), "epsg": epsg,
                     "cekim": {"uygulanan_m": round(uygulanan, 2), "cizildi": bool(cekme_poly)}}
    veri["uyarilar"] = [] if cekme_poly else ["Çekme sınırı çizilemedi (offset geçersiz)."]

    # 2) kaynak sayfa (imar.aspx)
    kaynak_sonuc = {"durum": "istenmedi"}
    if kaynak_dahil:
        try:
            kaynak_sonuc = _kaynak_cekim(il, ilce, mahalle, ada, parsel, out_dir, tkgm=tkgm)
        except imar_mod.ApiError as exc:
            kaynak_sonuc = {"durum": "hata", "hata": exc.mesaj, "hata_tipi": exc.tip}
        veri["kaynak_sayfa"] = {k: kaynak_sonuc.get(k) for k in
                                ("durum", "pdf", "png", "url", "sure_sn", "plan_pdf", "hata")}
        if kaynak_sonuc.get("pdf"):
            dosyalar["kaynak_imar.pdf"] = kaynak_sonuc["pdf"]
        if kaynak_sonuc.get("png"):
            dosyalar["kaynak_imar.png"] = kaynak_sonuc["png"]
        if kaynak_sonuc.get("plan_pdf"):
            dosyalar["imar_plani.pdf"] = kaynak_sonuc["plan_pdf"]

    # 3) ozet PDF (3 sayfa)
    ozet = os.path.join(out_dir, "ozet.pdf")
    ozet_meta = rapor.ozet_pdf(ozet, veri, dosyalar.get("parsel.png"))
    if ozet_meta.get("yazildi"):
        dosyalar["ozet.pdf"] = ozet

    # 4) birlestirme
    if ozet_meta.get("yazildi"):
        if kaynak_sonuc.get("pdf"):
            bir = rapor.birlestir(ozet, kaynak_sonuc["pdf"], os.path.join(out_dir, "paket.pdf"))
            if bir.get("durum") == "ok":
                dosyalar["paket.pdf"] = bir["yol"]
                veri["paket_pdf"] = bir
            else:
                veri["birlestirme"] = bir
        else:
            dosyalar["paket.pdf"] = ozet
            veri["birlestirme"] = {"durum": "kaynak_yok",
                                   "not": "Kaynak sayfa alinamadi; paket.pdf = ozet."}

    # 5) json/md
    veri["dosyalar"] = dosyalar
    _json_yaz(os.path.join(out_dir, "rapor.json"), veri)
    with open(os.path.join(out_dir, "rapor.md"), "w", encoding="utf-8") as fh:
        fh.write(rapor.rapor_md(veri))
    dosyalar["rapor.json"] = os.path.join(out_dir, "rapor.json")
    dosyalar["rapor.md"] = os.path.join(out_dir, "rapor.md")

    # 6) zip
    import zipfile
    zip_yolu = os.path.join(out_dir, "paket.zip")
    icerik = []
    with zipfile.ZipFile(zip_yolu, "w", zipfile.ZIP_DEFLATED) as zf:
        for ad, yol in dosyalar.items():
            if yol and os.path.exists(yol):
                zf.write(yol, os.path.basename(yol))
                icerik.append(os.path.basename(yol))
    dosyalar["paket.zip"] = zip_yolu

    return {"parsel": cozum, "klasor": out_dir, "dosyalar": dosyalar,
            "ozet_pdf": ozet_meta, "kaynak_sayfa": veri.get("kaynak_sayfa"),
            "paket_pdf": veri.get("paket_pdf"), "birlestirme": veri.get("birlestirme"),
            "imar": {k: veri["imar"].get(k) for k in
                     ("plan_adi", "emsal", "taks", "kaks", "bina_yuksekligi_m",
                      "kat_sayisi", "yapi_nizami", "kullanim_karari")} if veri["imar"] else None,
            "imar_hata": imar_hata, "uyarilar": veri.get("uyarilar"),
            "zip_icerik": sorted(icerik), "zaman": _ts()}


def tool_durum(_args):
    d = kaynak.durum()
    try:
        import cairosvg  # noqa: F401
        svg = True
    except Exception:
        svg = False
    try:
        import cairocffi  # noqa: F401
        pdf = True
    except Exception:
        pdf = False
    try:
        import pypdf  # noqa: F401
        merge = True
    except Exception:
        merge = False
    return {"sunucu": SERVER_NAME, "surum": SERVER_VERSION, "arac_sayisi": len(TOOLS),
            "bagimliliklar": {"playwright": d["playwright"], "chromium": d["chromium"],
                              "chromium_yolu": d["chromium_yolu"], "cairocffi": pdf,
                              "cairosvg": svg, "pypdf": merge},
            "cache": {"dizin": CACHE_DIR,
                      "dosya": len(os.listdir(CACHE_DIR)) if os.path.isdir(CACHE_DIR) else 0},
            "cikti_dizini": OUT_DIR,
            "imar_iller": {"toplam": len(imar_mod.ILLER),
                           "aktif": sum(1 for c in imar_mod.ILLER.values() if c.get("durum") == "aktif")},
            "son_hata": _last_error, "zaman": _ts()}


TOOLS = [
    {"name": "parsel", "description": "TKGM parsel sorgusu: ada/parsel veya lat/lon. Alan, nitelik, pafta, mevkii, zemin/KM, geometri ve GeoJSON dosyasi doner.",
     "inputSchema": {"type": "object", "properties": {
         "il": {"type": "string"}, "ilce": {"type": "string"}, "mahalle": {"type": "string"},
         "ada": {"type": "string"}, "parsel": {"type": "string"},
         "lat": {"type": "number"}, "lon": {"type": "number"}},
         "additionalProperties": False}, "handler": tool_parsel},
    {"name": "imar_sorgula", "description": "KEOS e-Imar: emsal, TAKS, KAKS, H, kat, nizam, cekme, plan adi, kullanim, plan notlari, kaynak URL ve zaman. Ada/parsel birden fazla mahallede varsa mahalle zorunlu.",
     "inputSchema": {"type": "object", "properties": {
         "il": {"type": "string"}, "ilce": {"type": "string"}, "mahalle": {"type": "string"},
         "ada": {"type": "string"}, "parsel": {"type": "string"}},
         "required": ["il", "ada", "parsel"], "additionalProperties": False}, "handler": tool_imar_sorgula},
    {"name": "imar_kapsam", "description": "Imar destegi olan 20 ili, portal adresini, durumunu (aktif / giris / captcha / bakim / 404 / 503 / zaman asimi) listeler.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "handler": tool_imar_kapsam},
    {"name": "liste", "description": "Idari listeler: tur=il|ilce|mahalle (TKGM id'leri) veya imar_il|imar_ilce|imar_mahalle (portal listeleri).",
     "inputSchema": {"type": "object", "properties": {
         "tur": {"type": "string", "enum": ["il", "ilce", "mahalle", "imar_il", "imar_ilce", "imar_mahalle"]},
         "il": {"type": "string"}, "il_id": {"type": ["integer", "string"]},
         "ilce": {"type": "string"}, "ilce_id": {"type": ["integer", "string"]}},
         "additionalProperties": False}, "handler": tool_liste},
    {"name": "cizim", "description": "Parsel DXF (R12, PARSEL + IMAR etiket katmani) ve 1/500 PNG vaziyet (cevre mesafeleri, olcu, kuzey, olcek cubugu). Hesap yapmaz.",
     "inputSchema": {"type": "object", "properties": {
         "il": {"type": "string"}, "ilce": {"type": "string"}, "mahalle": {"type": "string"},
         "ada": {"type": "string"}, "parsel": {"type": "string"},
         "crs": {"type": "string", "enum": ["tm", "wgs84", "both"]}},
         "required": ["il", "ilce", "mahalle", "ada", "parsel"], "additionalProperties": False},
     "handler": tool_cizim},
    {"name": "kaynak_sayfa", "description": "Portalın KENDI sonuc sayfasini (imar.aspx; harita + tablo) headless Chromium ile print-PDF ve tam sayfa PNG olarak alir; varsa imar plani PDF'ini indirir. Playwright/chromium yoksa 'bagimlilik_yok' doner.",
     "inputSchema": {"type": "object", "properties": {
         "il": {"type": "string"}, "ilce": {"type": "string"}, "mahalle": {"type": "string"},
         "ada": {"type": "string"}, "parsel": {"type": "string"},
         "tkgm": {"type": "boolean", "description": "TKGM sayfasi da alinsin (deneysel)"}},
         "required": ["il", "ilce", "mahalle", "ada", "parsel"], "additionalProperties": False},
     "handler": tool_kaynak_sayfa},
    {"name": "paket", "description": "Tek cagri: parsel + imar + cizim (DXF/PNG) + kaynak sayfa (imar.aspx PDF/PNG) + 3 sayfalik ozet PDF -> paket.pdf (ozet + kaynak birebir) + ZIP.",
     "inputSchema": {"type": "object", "properties": {
         "il": {"type": "string"}, "ilce": {"type": "string"}, "mahalle": {"type": "string"},
         "ada": {"type": "string"}, "parsel": {"type": "string"},
         "kaynak_dahil": {"type": "boolean"}, "kaynak_sayfa_tkgm": {"type": "boolean"}},
         "required": ["il", "ilce", "mahalle", "ada", "parsel"], "additionalProperties": False},
     "handler": tool_paket},
    {"name": "durum", "description": "Sunucu surumu, arac sayisi, bagimlilik durumu (playwright/chromium/cairocffi/cairosvg/pypdf), cache ve imar kapsami.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "handler": tool_durum},
]

TOOL_MAP = {t["name"]: t for t in TOOLS}

# v2 -> v3 geri uyumluluk (tools/list'te gorunmez)
ALIASES = {
    "parsel_bul": "parsel", "parsel_sorgula": "parsel", "parsel_sorgula_koordinat": "parsel",
    "parsel_dxf": "cizim", "parsel_imar": "paket", "dosya_olustur": "paket",
    "imar_iller_listele": "imar_kapsam",
}
KALDIRILDI = {
    "on_proje": "v3'te kaldirildi: hesap/maliyet yok (yalnizca parsel+imar+cizim+kaynak sayfa).",
    "kural_listesi": "v3'te kaldirildi: kural motoru yok.",
    "kural_kontrol": "v3'te kaldirildi: kural motoru yok.",
    "kural_ozeti": "v3'te kaldirildi: kural motoru yok.",
    "kural_guncelle": "v3'te kaldirildi: kural motoru yok.",
    "iller_listele": "v3'te 'liste(tur=il)' kullanin.",
    "ilceler_listele": "v3'te 'liste(tur=ilce, il_id=...)' kullanin.",
    "mahalleler_listele": "v3'te 'liste(tur=mahalle, ilce_id=...)' kullanin.",
    "imar_ilceler_listele": "v3'te 'liste(tur=imar_ilce, il=...)' kullanin.",
    "imar_mahalleler_listele": "v3'te 'liste(tur=imar_mahalle, il=..., ilce=...)' kullanin.",
}

# ---------------------------------------------------------------- JSON-RPC

def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def _hata_dict(mesaj, tip="hata", ek=None):
    d = {"durum": "hata", "hata_tipi": tip, "mesaj": mesaj}
    d.update(ek or {})
    return d


def handle(msg):
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _result(msg_id, {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "Parsel + imar + cizim + kaynak sayfa (v3). Hesap/maliyet YOK. "
                "Akis: parsel -> imar_sorgula -> cizim -> kaynak_sayfa -> paket. "
                "Kaynak sayfa, portalın kendi imar.aspx ciktisidir (harita + tablo)."
            ),
        })

    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": [
            {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
            for t in TOOLS]})

    if method == "tools/call":
        ad = params.get("name")
        arguments = params.get("arguments") or {}
        if ad in KALDIRILDI:
            return _result(msg_id, {"content": [{"type": "text", "text": json.dumps(
                _hata_dict(KALDIRILDI[ad], "kaldirildi"), ensure_ascii=False)}], "isError": True})
        hedef = ALIASES.get(ad, ad)
        if hedef != ad:
            _m = {"il_adi": "il", "ilce_adi": "ilce", "mahalle_adi": "mahalle"}
            arguments = {_m.get(k, k): v for k, v in arguments.items()}
        tool = TOOL_MAP.get(hedef)
        if tool is None:
            return _error(msg_id, -32602, "Bilinmeyen arac: %s" % ad, {"bilinen_araclar": list(TOOL_MAP)})
        if not isinstance(arguments, dict):
            return _error(msg_id, -32602, "arguments bir nesne olmali.")
        try:
            data = tool["handler"](arguments)
            return _result(msg_id, {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}],
                                    "isError": False})
        except imar_mod.ApiError as exc:
            _last_error.update({"zaman": _ts(), "arac": ad, "mesaj": exc.mesaj})
            return _result(msg_id, {"content": [{"type": "text", "text": json.dumps(exc.as_dict(), ensure_ascii=False, indent=2)}],
                                    "isError": True})
        except ApiError as exc:
            _last_error.update({"zaman": _ts(), "arac": ad, "mesaj": str(exc)})
            return _result(msg_id, {"content": [{"type": "text", "text": json.dumps(
                _hata_dict(str(exc)), ensure_ascii=False)}], "isError": True})
        except Exception as exc:
            _last_error.update({"zaman": _ts(), "arac": ad, "mesaj": "%s: %s" % (type(exc).__name__, exc)})
            log("beklenmeyen hata (%s): %r" % (ad, exc))
            return _result(msg_id, {"content": [{"type": "text", "text": json.dumps(
                _hata_dict("beklenmeyen sunucu hatasi: %s" % exc, "sunucu_hatasi"), ensure_ascii=False)}],
                "isError": True})

    if msg_id is None:
        return None
    return _error(msg_id, -32601, "Desteklenmeyen method: %s" % method)


def main():
    log("baslatildi (pid=%d), cikti=%s" % (os.getpid(), OUT_DIR))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError as exc:
            sys.stdout.write(json.dumps(_error(None, -32700, "JSON cozulemedi: %s" % exc)) + "\n")
            sys.stdout.flush()
            continue
        if not isinstance(msg, dict):
            sys.stdout.write(json.dumps(_error(None, -32600, "Gecersiz istek.")) + "\n")
            sys.stdout.flush()
            continue
        response = handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except BrokenPipeError:
        pass
