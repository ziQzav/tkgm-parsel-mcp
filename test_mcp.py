#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tkgm-parsel MCP sunucusu testleri.

Kullanim:
    python3 test_mcp.py              # varsayilan: --offline (ag erisimi yok)
    python3 test_mcp.py --offline    # ayni: sema + projeksiyon + DXF (fixture ile)
    python3 test_mcp.py --online     # gercek TKGM sorgulari (ag gerekir)

Offline testler aga hic cikmaz: araclarin semasini, TM projeksiyonunu ve
DXF uretimini depodaki ornek parsel (tests/fixtures) ile dogrular.
"""

import json
import math
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")
FIXTURE = os.path.join(HERE, "tests", "fixtures", "parsel_rize_1895_4.geojson")

# Testler depo klasorunu kirletmesin: cikti/cache gecici dizine yazilir.
TMP = tempfile.mkdtemp(prefix="tkgm-parsel-test-")
os.environ.setdefault("TKGMPARSEL_CIKTI", os.path.join(TMP, "cikti"))
os.environ.setdefault("TKGMPARSEL_CACHE", os.path.join(TMP, "cache"))

sys.path.insert(0, HERE)
import server as srv  # noqa: E402  (projeksiyon/DXF fonksiyonlari icin)

OK = 0
FAIL = 0


def check(label, cond, detail=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("PASS  %s" % label)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (label, detail))


# ---------------------------------------------------------------- DXF okuyucu

def dxf_entities(path):
    """R12 ASCII DXF -> [{'type': ..., 'tags': [(code, value), ...]}]"""
    with open(path, "r", encoding="ascii") as fh:
        lines = [l.rstrip("\r\n") for l in fh]
    ents, cur = [], None
    for i in range(0, len(lines) - 1, 2):
        code, val = lines[i].strip(), lines[i + 1].strip()
        if code == "0":
            cur = {"type": val, "tags": []}
            ents.append(cur)
        elif cur is not None:
            cur["tags"].append((code, val))
    return ents


def dxf_tag(ent, code, default=None):
    for c, v in ent["tags"]:
        if c == code:
            return v
    return default


def dxf_ozet(path):
    ents = dxf_entities(path)
    polys = [e for e in ents if e["type"] == "POLYLINE"]
    verts = [e for e in ents if e["type"] == "VERTEX"]
    texts = [e for e in ents if e["type"] == "TEXT"]
    pts = [(float(dxf_tag(v, "10")), float(dxf_tag(v, "20"))) for v in verts]
    with open(path, "r", encoding="ascii") as fh:
        raw = fh.read()
    return {
        "poliline": len(polys),
        "vertex": len(verts),
        "text": len(texts),
        "kapali": bool(polys) and all(int(dxf_tag(p, "70", "0")) & 1 for p in polys),
        "noktalar": pts,
        "yazilar": [dxf_tag(t, "1") for t in texts],
        "katmanlar": sorted({dxf_tag(e, "8") for e in ents
                             if dxf_tag(e, "8") and e["type"] in ("POLYLINE", "VERTEX", "TEXT")}),
        "acadver": "$ACADVER" in raw and "AC1009" in raw,
    }


def shoelace(pts):
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def kuresel_alan(ring):
    """Kure uzerinde alan (m2). Halka kapali varsayilir."""
    r = 6371000.0
    s = 0.0
    n = len(ring)
    for i in range(n):
        lon1, lat1 = math.radians(ring[i][0]), math.radians(ring[i][1])
        lon2, lat2 = math.radians(ring[(i + 1) % n][0]), math.radians(ring[(i + 1) % n][1])
        s += (lon2 - lon1) * (2 + math.sin(lat1) + math.sin(lat2))
    return abs(s) * r * r / 2.0


# ---------------------------------------------------------------- offline

BEKLENEN_ARACLAR = [
    "iller_listele",
    "ilceler_listele",
    "mahalleler_listele",
    "parsel_sorgula",
    "parsel_bul",
    "parsel_sorgula_koordinat",
    "parsel_dxf",
]


def test_sema():
    init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                  "clientInfo": {"name": "test", "version": "1"}}})
    info = (init.get("result") or {}).get("serverInfo") or {}
    check("initialize -> serverInfo tkgm-parsel", info.get("name") == "tkgm-parsel", info)
    check("initialize -> protocolVersion", bool((init.get("result") or {}).get("protocolVersion")), init)

    tl = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = (tl.get("result") or {}).get("tools") or []
    isimler = [t.get("name") for t in tools]
    check("tools/list 7 arac", len(tools) == 7, isimler)
    check("arac adlari beklenen", isimler == BEKLENEN_ARACLAR, isimler)
    check("her arac gecerli inputSchema",
          all(isinstance(t.get("inputSchema"), dict) and t["inputSchema"].get("type") == "object"
              and isinstance(t.get("description"), str) and t["description"] for t in tools),
          [t.get("name") for t in tools])
    dxf = [t for t in tools if t.get("name") == "parsel_dxf"][0]
    crs = dxf["inputSchema"]["properties"]["crs"]
    check("parsel_dxf crs enum tm/wgs84/both", crs.get("enum") == ["tm", "wgs84", "both"], crs)
    check("parsel_dxf ada/parsel zorunlu", sorted(dxf["inputSchema"].get("required", [])) == ["ada", "parsel"],
          dxf["inputSchema"].get("required"))

    err = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "yok_boyle_arac", "arguments": {}}})
    check("bilinmeyen arac -> -32602", (err.get("error") or {}).get("code") == -32602, err)
    print("      araclar:", ", ".join(isimler))


def test_projeksiyon():
    tablo = [(27.0, 5253), (30.0, 5254), (33.0, 5255), (36.0, 5256), (39.0, 5257),
             (42.0, 5258), (45.0, 5259), (48.0, 5260), (51.0, 5261), (54.0, 5262)]
    hatalar = [(lon, srv.tm_epsg(lon), epsg) for lon, epsg in tablo
               if srv.tm_epsg(lon) != epsg or srv.tm_merkez_boylam(lon) != lon]
    check("EPSG 5253-5262 eslemesi (CM 27..54)", not hatalar, hatalar)

    check("Rize 40.61E -> TM42 / EPSG:5258", srv.tm_epsg(40.61055) == 5258 and
          srv.tm_merkez_boylam(40.61055) == 42.0)
    check("Ankara 32.86E -> TM33 / EPSG:5255", srv.tm_epsg(32.86188) == 5255)

    e, n = srv.tm_forward(41.04855, 40.61055)
    check("Rize TM koordinati (383182.591, 4546895.252)",
          abs(e - 383182.591) < 0.01 and abs(n - 4546895.252) < 0.01, (e, n))

    lat, lon = srv.tm_inverse(e, n, 42.0)
    check("TM -> WGS84 ters donusum (1e-9 derece)",
          abs(lat - 41.04855) < 1e-9 and abs(lon - 40.61055) < 1e-9, (lat, lon))

    en_buyuk = 0.0
    for lat in (36.0, 37.5, 39.0, 41.0, 42.0):
        for d in (-1.5, -0.75, 0.0, 0.75, 1.5):
            lon = 32.86 + d
            lon0 = srv.tm_merkez_boylam(lon)
            e, n = srv.tm_forward(lat, lon, lon0)
            bl, bn = srv.tm_inverse(e, n, lon0)
            en_buyuk = max(en_buyuk, math.hypot((bl - lat) * 110574.0,
                                                (bn - lon) * 111320.0 * math.cos(math.radians(lat))))
    check("WGS84->TM->WGS84 round-trip < 1 mm (%.4f mm)" % (en_buyuk * 1000), en_buyuk < 0.001, en_buyuk)


def test_dxf_offline():
    feature = json.load(open(FIXTURE, encoding="utf-8"))
    cozum = {"il": "Rize", "il_id": 75, "ilce": "Merkez", "ilce_id": 786,
             "mahalle": "Hamidiye Köyü", "mahalle_id": 123992, "ada": "1895", "parsel": "4"}
    sonuc = srv.parsel_dxf_uret(feature, cozum, "both")
    dosyalar = sonuc["dosyalar"]
    check("parsel_dxf_uret(both) -> 2 dosya", len(dosyalar) == 2, dosyalar)
    check("DXF dosyalari diskte", all(os.path.exists(d["dosya"]) for d in dosyalar), dosyalar)
    check("alan_m2 kayit degeri 2372.25", sonuc.get("alan_m2") == 2372.25, sonuc.get("alan_m2"))
    check("koordinat_sayisi 9", sonuc.get("koordinat_sayisi") == 9, sonuc.get("koordinat_sayisi"))

    tm = [d for d in dosyalar if d["birim"] == "metre"][0]
    wgs = [d for d in dosyalar if d["birim"] == "derece"][0]
    check("TM dosyasi EPSG:5258 / TM42", tm["epsg"] == 5258 and tm["merkez_boylam"] == 42.0, tm)
    check("WGS84 dosyasi EPSG:4326", wgs["epsg"] == 4326, wgs)
    check("dosya adi deseni <Mahalle>_<Ada>_<Parsel>_<crs>.dxf",
          os.path.basename(tm["dosya"]) == "Hamidiye_Köyü_1895_4_tm.dxf", tm["dosya"])

    t = dxf_ozet(tm["dosya"])
    check("DXF R12 (AC1009)", t["acadver"], t["acadver"])
    check("DXF 1 kapali POLYLINE", t["poliline"] == 1 and t["kapali"], t)
    check("DXF vertex = 8 (9 nokta - kapanis tekrari)", t["vertex"] == 8, t["vertex"])
    check("DXF katmanlari PARSEL + PARSEL_ETIKET",
          t["katmanlar"] == ["PARSEL", "PARSEL_ETIKET"], t["katmanlar"])
    check("DXF 9 TEXT (8 kose no + 1 alan)", t["text"] == 9, t["text"])
    check("DXF alan etiketi metni", any("1895/4" in y and "2372.25" in y for y in t["yazilar"]), t["yazilar"])
    check("DXF TM koordinatlari Turkiye sinirlarinda",
          all(200000 <= x <= 800000 and 3800000 <= y <= 4700000 for x, y in t["noktalar"]),
          t["noktalar"][:2])

    try:
        with open(tm["dosya"], "r", encoding="ascii") as fh:
            fh.read()
        ascii_ok = True
    except UnicodeDecodeError:
        ascii_ok = False
    check("DXF dosyasi saf ASCII", ascii_ok)

    tm_alan = shoelace(t["noktalar"])
    kuresel = kuresel_alan(json.load(open(FIXTURE, encoding="utf-8"))["geometry"]["coordinates"][0])
    fark = abs(tm_alan - kuresel) / kuresel
    check("TM alani kuresel alana yakin (%.2f%% < 1%%)" % (fark * 100), fark < 0.01, (tm_alan, kuresel))
    print("      TM alan %.2f m2 | kuresel %.2f m2 | kayit %s m2" % (tm_alan, kuresel, sonuc["alan_m2"]))

    w = dxf_ozet(wgs["dosya"])
    ilk = json.load(open(FIXTURE, encoding="utf-8"))["geometry"]["coordinates"][0][0]
    check("WGS84 DXF koordinatlari bozulmadan (3 ondaliga yuvarlanmamis)",
          abs(w["noktalar"][0][0] - ilk[0]) < 1e-9 and abs(w["noktalar"][0][1] - ilk[1]) < 1e-9,
          (w["noktalar"][0], ilk))
    check("WGS84 DXF koordinatlari derece araliginda (36-42K, 26-45D)",
          all(26 <= x <= 45 and 36 <= y <= 42 for x, y in w["noktalar"]), w["noktalar"][:2])

    try:
        srv.parsel_dxf_uret(feature, cozum, "utm")
        hata = ""
    except srv.ApiError as exc:
        hata = str(exc)
    check("gecersiz crs -> Turkce ApiError", "crs" in hata and "tm" in hata, hata)

    try:
        srv._parsel_coz({"parsel": "4"})
        hata = ""
    except srv.ApiError as exc:
        hata = str(exc)
    check("eksik ada -> 'ada zorunlu' (ag cagrisi yok)", "ada zorunlu" in hata, hata)


def test_env_override():
    hedef = os.path.join(TMP, "env-test-cikti")
    env = dict(os.environ)
    env["TKGMPARSEL_CIKTI"] = hedef
    out = subprocess.check_output(
        [sys.executable, "-c", "import server; print(server.OUT_DIR)"],
        cwd=HERE, env=env, universal_newlines=True).strip()
    check("TKGMPARSEL_CIKTI env ile OUT_DIR degisiyor", out == hedef, out)


def run_offline():
    print("MOD: offline (ag erisimi yok)\n")
    test_sema()
    test_projeksiyon()
    test_dxf_offline()
    test_env_override()


# ---------------------------------------------------------------- online

def run_online():
    print("MOD: online (gercek TKGM sorgulari)\n")
    proc = subprocess.Popen(
        [sys.executable, SERVER],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, bufsize=1,
    )
    sayac = [0]

    def rpc(method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            sayac[0] += 1
            msg["id"] = sayac[0]
        proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        proc.stdin.flush()
        if notify:
            return None
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("sunucu yanit vermeden kapandi; stderr: %s" % proc.stderr.read())
        return json.loads(line)

    def call(name, args):
        r = rpc("tools/call", {"name": name, "arguments": args})
        res = r.get("result", {})
        text = (res.get("content") or [{}])[0].get("text", "")
        return res.get("isError", False), text

    try:
        init = rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                  "clientInfo": {"name": "test", "version": "1"}})
        check("initialize", (init.get("result") or {}).get("serverInfo", {}).get("name") == "tkgm-parsel", init)
        rpc("notifications/initialized", {}, notify=True)

        tl = rpc("tools/list")
        isimler = [t["name"] for t in tl.get("result", {}).get("tools", [])]
        check("tools/list 7 arac", isimler == BEKLENEN_ARACLAR, isimler)

        err, text = call("iller_listele", {})
        data = json.loads(text) if not err else {}
        check("iller_listele 81 il", not err and data.get("adet") == 81, text[:300])
        print("      ilk 3:", (data.get("iller") or [])[:3])

        err, text = call("ilceler_listele", {"il_id": 23})
        data = json.loads(text) if not err else {}
        check("ilceler_listele(23) -> 15 ilce", not err and data.get("adet") == 15, text[:300])
        print("      ilk 3:", (data.get("ilceler") or [])[:3])

        err, text = call("mahalleler_listele", {"ilce_id": 104})
        data = json.loads(text) if not err else {}
        check("mahalleler_listele(104) -> 31 mahalle", not err and data.get("adet") == 31, text[:300])

        err, text = call("parsel_sorgula", {"mahalle_id": 55834, "ada": "2531", "parsel": "7"})
        data = json.loads(text) if not err else {}
        check("parsel_sorgula 55834/2531/7",
              not err and data.get("ada") == "2531" and data.get("parsel") == "7", text[:400])
        check("geojson dosyasi yazildi",
              bool(data.get("geojson_dosya")) and os.path.exists(data.get("geojson_dosya", "")),
              data.get("geojson_dosya"))
        print("      ozet:", json.dumps({k: data.get(k) for k in
              ("il", "ilce", "mahalle", "alan_m2", "koordinat_sayisi")}, ensure_ascii=False))

        err, text = call("parsel_bul", {"il_adi": "Ankara", "ilce_adi": "Çankaya",
                                        "mahalle_adi": "Küçükesat", "ada": "2531", "parsel": "7"})
        data = json.loads(text) if not err else {}
        check("parsel_bul (isimden)", not err and
              (data.get("cozumleme") or {}).get("mahalle_id") == 55834, text[:400])

        err, text = call("parsel_sorgula_koordinat", {"lat": 39.9075, "lon": 32.8620})
        data = json.loads(text) if not err else {}
        check("parsel_sorgula_koordinat", not err and data.get("ada") == "2531", text[:400])

        err, text = call("parsel_sorgula", {"mahalle_id": 10007, "ada": "1", "parsel": "1"})
        check("404 -> Turkce hata", err and "bulunamadi" in text.lower(), text[:200])

        err, text = call("parsel_dxf", {"il_adi": "Rize", "ilce_adi": "Merkez",
                                        "mahalle_adi": "Hamidiye", "ada": "1895", "parsel": "4",
                                        "crs": "both"})
        rize = json.loads(text) if not err else {}
        dosyalar = rize.get("dosyalar") or []
        check("parsel_dxf Rize both -> 2 dosya", not err and len(dosyalar) == 2, text[:300])
        tm = [d for d in dosyalar if d.get("birim") == "metre"] or [{}]
        if dosyalar:
            t = dxf_ozet(dosyalar[0]["dosya"])
            check("Rize DXF kapali POLYLINE, TM42/EPSG:5258",
                  tm[0].get("epsg") == 5258 and t["kapali"], tm[0])
            print("      Rize TM:", tm[0].get("dosya"), "alan:", rize.get("alan_m2"))

        err, text = call("parsel_dxf", {"mahalle_id": 55834, "ada": "2531", "parsel": "7"})
        ankara = json.loads(text) if not err else {}
        a = (ankara.get("dosyalar") or [{}])[0]
        check("parsel_dxf Ankara TM33/EPSG:5255", not err and a.get("epsg") == 5255, a)

        err, text = call("parsel_dxf", {"mahalle_id": 55834, "ada": "2531", "parsel": "7", "crs": "utm"})
        check("gecersiz crs -> Turkce hata", err and "crs" in text.lower(), text[:200])
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)


# ---------------------------------------------------------------- main

def main(argv):
    if "--online" in argv:
        run_online()
    else:
        run_offline()
    print("\n%d test gecti, %d basarisiz" % (OK, FAIL))
    if FAIL == 0:
        print("(gecici dizin: %s)" % TMP)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
