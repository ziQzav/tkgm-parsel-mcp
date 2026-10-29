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
    """TKGMPARSEL_CIKTI / TKGMPARSEL_CACHE ile degistirilebilen calisma dizini."""
    value = os.environ.get(var)
    if value:
        return os.path.abspath(os.path.expanduser(value))
    return os.path.join(HERE, default)


OUT_DIR = _env_dir("TKGMPARSEL_CIKTI", "cikti")
CACHE_DIR = _env_dir("TKGMPARSEL_CACHE", "cache")

SERVER_NAME = "tkgm-parsel"
SERVER_VERSION = "1.0.0"
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
    etiketler: [{"x":, "y":, "yazi":, "yukseklik":, "aci":}]
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
        (0, "TABLE"), (2, "LAYER"), (70, "2"),
        (0, "LAYER"), (2, DXF_LINE_LAYER), (70, "0"), (62, "7"), (6, "CONTINUOUS"),
        (0, "LAYER"), (2, DXF_LABEL_LAYER), (70, "0"), (62, "3"), (6, "CONTINUOUS"),
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
            (0, "TEXT"), (8, DXF_LABEL_LAYER),
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


# ---------------------------------------------------------------- araclar

def tool_iller_listele(_args):
    iller = get_il_listesi()
    sade = [{"id": i["id"], "ad": i["ad"]} for i in iller]
    return {
        "iller": sade,
        "adet": len(sade),
        "not": "id = il plaka kodu (or. Adana 23). Bu id ilceler_listele icin kullanilir.",
    }


def tool_ilceler_listele(args):
    il_id = args.get("il_id")
    if il_id is None or str(il_id).strip() == "":
        raise ApiError("il_id zorunlu. iller_listele ile sayisal il id'sini alin.")
    il_id = str(il_id).strip()
    if il_id.startswith("{"):
        raise ApiError(
            "il_id GUID olamaz; iller_listele ciktisindaki sayisal id'yi kullanin (or. Adana = 23)."
        )
    ilceler = get_ilceler(il_id)
    return {"il_id": int(il_id) if il_id.isdigit() else il_id, "ilceler": ilceler, "adet": len(ilceler)}


def tool_mahalleler_listele(args):
    ilce_id = args.get("ilce_id")
    if ilce_id is None or str(ilce_id).strip() == "":
        raise ApiError("ilce_id zorunlu. ilceler_listele ciktisindaki id'yi kullanin.")
    ilce_id = str(ilce_id).strip()
    mahalleler = get_mahalleler(ilce_id)
    return {
        "ilce_id": int(ilce_id) if ilce_id.isdigit() else ilce_id,
        "mahalleler": mahalleler,
        "adet": len(mahalleler),
    }


def tool_parsel_sorgula(args):
    mahalle_id = args.get("mahalle_id")
    ada = args.get("ada")
    parsel = args.get("parsel")
    for name, val in (("mahalle_id", mahalle_id), ("ada", ada), ("parsel", parsel)):
        if val is None or str(val).strip() == "":
            raise ApiError("%s zorunlu." % name)
    feature = parsel_sorgula_raw(str(mahalle_id).strip(), str(ada).strip(), str(parsel).strip())
    return _parsel_ozet(feature, "parsel/%s/%s/%s" % (mahalle_id, ada, parsel))


def tool_parsel_bul(args):
    for name in ("il_adi", "ilce_adi", "mahalle_adi", "ada", "parsel"):
        if args.get(name) is None or str(args.get(name)).strip() == "":
            raise ApiError("%s zorunlu." % name)

    il = _pick(get_il_listesi(), args["il_adi"], "Il")
    ilce = _pick(get_ilceler(il["id"]), args["ilce_adi"], "Ilce")
    mahalle = _pick(get_mahalleler(ilce["id"]), args["mahalle_adi"], "Mahalle")
    feature = parsel_sorgula_raw(mahalle["id"], str(args["ada"]).strip(), str(args["parsel"]).strip())
    return _parsel_ozet(feature, "parsel_bul", {
        "cozumleme": {
            "il": il["ad"], "il_id": il["id"],
            "ilce": ilce["ad"], "ilce_id": ilce["id"],
            "mahalle": mahalle["ad"], "mahalle_id": mahalle["id"],
            "ada": str(args["ada"]).strip(), "parsel": str(args["parsel"]).strip(),
        }
    })


def tool_parsel_sorgula_koordinat(args):
    try:
        lat = float(str(args.get("lat")).replace(",", "."))
        lon = float(str(args.get("lon")).replace(",", "."))
    except (TypeError, ValueError):
        raise ApiError("lat ve lon sayisal olmali (WGS84, or. lat=39.9075, lon=32.8620).")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ApiError("lat/lon araligi gecersiz (lat -90..90, lon -180..180).")
    url = "%s/parsel/%.6f/%.6f/" % (BASE, lat, lon)
    try:
        data = http_get_json(url, ttl=PARCEL_TTL)
    except ApiError as exc:
        if "HTTP 404" in str(exc):
            raise ApiError(
                "Bu koordinatta parsel bulunamadi: lat=%.6f, lon=%.6f. "
                "Nokta kadastro parselleri disinda (yol, deniz, bos alan) olabilir. (%s)" % (lat, lon, exc)
            )
        raise
    if isinstance(data, dict) and data.get("type") == "Feature":
        feature = data
    else:
        feats = _features(data)
        if not feats:
            raise ApiError("Bu koordinatta parsel bulunamadi: lat=%.6f, lon=%.6f." % (lat, lon))
        feature = feats[0]
    return _parsel_ozet(feature, "parsel/%.6f/%.6f" % (lat, lon), {"sorgu_koordinat": {"lat": lat, "lon": lon}})


def _parsel_coz(args):
    """parsel_dxf girdisini cozer: (feature, cozumleme). mahalle_id veya adlar."""
    ada = args.get("ada")
    parsel = args.get("parsel")
    for name, val in (("ada", ada), ("parsel", parsel)):
        if val is None or str(val).strip() == "":
            raise ApiError("%s zorunlu." % name)
    ada = str(ada).strip()
    parsel = str(parsel).strip()

    mahalle_id = args.get("mahalle_id")
    if mahalle_id is not None and str(mahalle_id).strip() != "":
        mahalle_id = str(mahalle_id).strip()
        feature = parsel_sorgula_raw(mahalle_id, ada, parsel)
        p = _props(feature)
        return feature, {
            "il": p.get("ilAd"), "il_id": p.get("ilId"),
            "ilce": p.get("ilceAd"), "ilce_id": p.get("ilceId"),
            "mahalle": p.get("mahalleAd"), "mahalle_id": p.get("mahalleId") or mahalle_id,
            "ada": ada, "parsel": parsel,
        }

    for name in ("il_adi", "ilce_adi", "mahalle_adi"):
        if args.get(name) is None or str(args.get(name)).strip() == "":
            raise ApiError(
                "%s zorunlu (ya mahalle_id + ada + parsel, ya il_adi/ilce_adi/mahalle_adi + ada + parsel)." % name
            )
    il = _pick(get_il_listesi(), args["il_adi"], "Il")
    ilce = _pick(get_ilceler(il["id"]), args["ilce_adi"], "Ilce")
    mahalle = _pick(get_mahalleler(ilce["id"]), args["mahalle_adi"], "Mahalle")
    feature = parsel_sorgula_raw(mahalle["id"], ada, parsel)
    return feature, {
        "il": il["ad"], "il_id": il["id"],
        "ilce": ilce["ad"], "ilce_id": ilce["id"],
        "mahalle": mahalle["ad"], "mahalle_id": mahalle["id"],
        "ada": ada, "parsel": parsel,
    }


def parsel_dxf_uret(feature, cozum, crs="tm", out_dir=None):
    """Cozulmus bir parsel Feature'indan DXF uretir (ag erisimi yok).

    feature : TKGM GeoJSON Feature (geometri WGS84)
    cozum   : {"il","ilce","mahalle","mahalle_id","ada","parsel"} sozlugu
    crs     : "tm" | "wgs84" | "both"
    out_dir : varsayilan OUT_DIR
    """
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

    # TUM koordinatlarin ortalama boylami -> zon (parsel tek zon icinde kalir)
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
            olcek = 1.0
            ek = {"merkez_boylam": lon0, "olcek_faktoru": TM_K0, "false_easting": TM_FE}
        else:
            halkalar = [[(lon, lat) for lon, lat in ring] for ring in halkalar_ll]
            birim = "derece"
            crs_adi = "WGS84 (cografi, EPSG:4326)"
            epsg_kodu = 4326
            olcek = None
            ek = {"uyari": "WGS84 DXF'te 1 birim = 1 derece; olcekli/metrik cizim icin crs='tm' kullanin."}

        etiketler = _dxf_etiketleri(halkalar, kimlik, alan_m2, birim)
        path = os.path.join(hedef, "%s_%s.dxf" % (dosya_temel, secim))
        yaz_dxf(path, halkalar, etiketler, ondalik=3 if birim == "metre" else 8)

        if birim == "metre":
            hesaplanan = round(sum(_ring_alan(r) for r in halkalar), 2)
        else:
            hesaplanan = None

        kayit = {
            "dosya": path,
            "crs": crs_adi,
            "epsg": epsg_kodu,
            "birim": birim,
            "koordinat_sayisi": sum(len(r) for r in halkalar),
            "halka_sayisi": len(halkalar),
            "kapali": True,
            "etiket_sayisi": len(etiketler),
        }
        if hesaplanan is not None:
            kayit["alan_hesap_m2"] = hesaplanan
        kayit.update(ek)
        dosyalar.append(kayit)

    return {
        "parsel": cozum,
        "alan_m2": alan_m2,
        "koordinat_sayisi": koordinat_sayisi,
        "geometri_tipi": (feature.get("geometry") or {}).get("type"),
        "dosyalar": dosyalar,
        "not": ("DXF = AutoCAD R12 (AC1009) ASCII, katmanlar: %s (kapali POLYLINE), %s (TEXT). "
                "DWG uretilmez; DXF tum CAD programlarinda acilir."
                % (DXF_LINE_LAYER, DXF_LABEL_LAYER)),
    }


def tool_parsel_dxf(args):
    crs = str(args.get("crs") or "tm").strip().lower()
    if crs not in ("tm", "wgs84", "both"):
        raise ApiError("crs 'tm', 'wgs84' veya 'both' olmali (gelen: %s)." % crs)
    feature, cozum = _parsel_coz(args)
    return parsel_dxf_uret(feature, cozum, crs)


TOOLS = [
    {
        "name": "iller_listele",
        "description": "Turkiye'nin 81 ilini listeler. id alani il plaka kodudur (or. Adana=23) ve ilceler_listele icin kullanilir.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": tool_iller_listele,
    },
    {
        "name": "ilceler_listele",
        "description": "Bir ile bagli ilceleri listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {"il_id": {"type": "integer", "description": "iller_listele ciktisindaki sayisal il id'si (plaka kodu)"}},
            "required": ["il_id"],
            "additionalProperties": False,
        },
        "handler": tool_ilceler_listele,
    },
    {
        "name": "mahalleler_listele",
        "description": "Bir ilceye bagli mahalleleri listeler.",
        "inputSchema": {
            "type": "object",
            "properties": {"ilce_id": {"type": "integer", "description": "ilceler_listele ciktisindaki ilce id'si"}},
            "required": ["ilce_id"],
            "additionalProperties": False,
        },
        "handler": tool_mahalleler_listele,
    },
    {
        "name": "parsel_sorgula",
        "description": "Mahalle id + ada + parsel ile kadastral parsel sorgular. Ozet (il/ilce/mahalle, alan m2, nitelik, koordinat sayisi) dondurur ve ham GeoJSON'u cikti/ klasorune yazar.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mahalle_id": {"type": "integer", "description": "mahalleler_listele ciktisindaki mahalle id'si"},
                "ada": {"type": "string", "description": "Ada numarasi"},
                "parsel": {"type": "string", "description": "Parsel numarasi"},
            },
            "required": ["mahalle_id", "ada", "parsel"],
            "additionalProperties": False,
        },
        "handler": tool_parsel_sorgula,
    },
    {
        "name": "parsel_bul",
        "description": "Il/ilce/mahalle ADINDAN id'leri cozer ve parseli sorgular. Kullanicilar en cok bunu kullanir. Adlar Turkce karakterli veya ASCII olabilir.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "il_adi": {"type": "string", "description": "Il adi, or. 'Ankara'"},
                "ilce_adi": {"type": "string", "description": "Ilce adi, or. 'Cankaya'"},
                "mahalle_adi": {"type": "string", "description": "Mahalle adi, or. 'Kucukesat'"},
                "ada": {"type": "string", "description": "Ada numarasi"},
                "parsel": {"type": "string", "description": "Parsel numarasi"},
            },
            "required": ["il_adi", "ilce_adi", "mahalle_adi", "ada", "parsel"],
            "additionalProperties": False,
        },
        "handler": tool_parsel_bul,
    },
    {
        "name": "parsel_sorgula_koordinat",
        "description": "WGS84 enlem/boylam ile o noktadaki kadastral parseli bulur (site arayuzunun cografi sorgusu).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lat": {"type": "number", "description": "Enlem (WGS84), or. 39.9075"},
                "lon": {"type": "number", "description": "Boylam (WGS84), or. 32.8620"},
            },
            "required": ["lat", "lon"],
            "additionalProperties": False,
        },
        "handler": tool_parsel_sorgula_koordinat,
    },
    {
        "name": "parsel_dxf",
        "description": "Parseli CAD icin DXF olarak disa aktarir (AutoCAD R12 ASCII, kapali POLYLINE + kose numaralari/alan etiketi). Girdi: il/ilce/mahalle adi veya mahalle_id + ada + parsel. crs: 'tm' (varsayilan, TUREF/ITRF96 3 derece TM, metre), 'wgs84' (derece) veya 'both' (iki dosya). Dosyalar cikti/ altina yazilir.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "il_adi": {"type": "string", "description": "Il adi, or. 'Rize' (mahalle_id verilmezse gerekli)"},
                "ilce_adi": {"type": "string", "description": "Ilce adi, or. 'Merkez'"},
                "mahalle_adi": {"type": "string", "description": "Mahalle adi, or. 'Hamidiye'"},
                "mahalle_id": {"type": "integer", "description": "Alternatif: mahalleler_listele ciktisindaki mahalle id'si (adlar yerine)"},
                "ada": {"type": "string", "description": "Ada numarasi"},
                "parsel": {"type": "string", "description": "Parsel numarasi"},
                "crs": {"type": "string", "enum": ["tm", "wgs84", "both"], "description": "Koordinat sistemi: tm (varsayilan, metre), wgs84 (derece), both (iki dosya)"},
            },
            "required": ["ada", "parsel"],
            "additionalProperties": False,
        },
        "handler": tool_parsel_dxf,
    },
]

TOOL_MAP = {t["name"]: t for t in TOOLS}


# ---------------------------------------------------------------- JSON-RPC

def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


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
                "TKGM parsel sorgu araclari (salt okunur). Il/ilce/mahalle adindan gitmek icin "
                "parsel_bul kullanin; id ile gitmek icin iller_listele -> ilceler_listele -> "
                "mahalleler_listele -> parsel_sorgula zincirini izleyin. Koordinatla sorgu: "
                "parsel_sorgula_koordinat. CAD ciktisi (DXF): parsel_dxf (crs: tm | wgs84 | both)."
            ),
        })

    if method in ("notifications/initialized", "initialized"):
        return None

    if method == "ping":
        return _result(msg_id, {})

    if method == "tools/list":
        return _result(msg_id, {"tools": [
            {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
            for t in TOOLS
        ]})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        tool = TOOL_MAP.get(name)
        if tool is None:
            return _error(msg_id, -32602, "Bilinmeyen arac: %s" % name,
                          {"bilinen_araclar": list(TOOL_MAP)})
        if not isinstance(arguments, dict):
            return _error(msg_id, -32602, "arguments bir nesne olmali.")
        try:
            data = tool["handler"](arguments)
            text = json.dumps(data, ensure_ascii=False, indent=2)
            return _result(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except ApiError as exc:
            return _result(msg_id, {
                "content": [{"type": "text", "text": "HATA: %s" % exc}],
                "isError": True,
            })
        except Exception as exc:  # beklenmeyen
            log("beklenmeyen hata (%s): %r" % (name, exc))
            return _result(msg_id, {
                "content": [{"type": "text", "text": "HATA: beklenmeyen sunucu hatasi: %s" % exc}],
                "isError": True,
            })

    if msg_id is None:
        return None  # bilinmeyen bildirim: yoksay
    return _error(msg_id, -32601, "Desteklenmeyen method: %s" % method)


def main():
    log("baslatildi (pid=%d), cikti=%s" % (os.getpid(), OUT_DIR))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
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
