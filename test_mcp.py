#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""parsel-imar birlesik MCP sunucusu testleri.

Kullanim:
    python3 test_mcp.py              # --offline (varsayilan): ag yok
    python3 test_mcp.py --online     # gercek TKGM + e-Imar portalleri

Offline mod: TKGM projeksiyonu, DXF (IMAR katmani dahil), imar HTML parser'i
(gercek portallardan alinan fixture'larla) ve il konfigurasyonunu dogrular.
"""

import json
import math
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")
FIX = os.path.join(HERE, "tests", "fixtures")

TMP = tempfile.mkdtemp(prefix="parsel-imar-test-")
os.environ.setdefault("PARSELIMAR_CIKTI", os.path.join(TMP, "cikti"))
os.environ.setdefault("PARSELIMAR_CACHE", os.path.join(TMP, "cache"))

sys.path.insert(0, HERE)
import server as srv   # noqa: E402
import imar            # noqa: E402

OK = 0
FAIL = 0

ARACLAR = ["iller_listele", "ilceler_listele", "mahalleler_listele", "parsel_sorgula", "parsel_bul",
           "parsel_sorgula_koordinat", "parsel_dxf", "imar_iller_listele", "imar_ilceler_listele",
           "imar_mahalleler_listele", "imar_sorgula", "parsel_imar"]


def check(label, cond, detail=""):
    global OK, FAIL
    if cond:
        OK += 1
        print("PASS  %s" % label)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (label, detail))


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return json.load(fh)


def fixture_text(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


def dxf_metinler(path):
    """DXF icindeki TEXT entity'lerini (katman, yazi) olarak dondurur."""
    with open(path, "r", encoding="ascii") as fh:
        lines = [l.rstrip("\r\n") for l in fh]
    out, cur = [], None
    for i in range(0, len(lines) - 1, 2):
        code, val = lines[i].strip(), lines[i + 1].strip()
        if code == "0":
            cur = {"type": val, "tags": {}}
            out.append(cur)
        elif cur is not None:
            cur["tags"].setdefault(code, val)
    return out


def dxf_katmanlar(path):
    return sorted({e["tags"].get("8") for e in dxf_metinler(path) if e["tags"].get("8")})


# ---------------------------------------------------------------- offline

def test_sema():
    init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    info = (init.get("result") or {}).get("serverInfo") or {}
    check("initialize -> parsel-imar", info.get("name") == "parsel-imar", info)

    tl = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = (tl.get("result") or {}).get("tools") or []
    isimler = [t["name"] for t in tools]
    check("tools/list 12 arac", len(tools) == 12, isimler)
    check("arac adlari beklenen", isimler == ARACLAR, isimler)
    check("her arac gecerli inputSchema",
          all(t["inputSchema"].get("type") == "object" and t.get("description") for t in tools))
    pi = [t for t in tools if t["name"] == "parsel_imar"][0]
    check("parsel_imar zorunlu alanlari",
          sorted(pi["inputSchema"]["required"]) == ["ada", "il", "ilce", "mahalle", "parsel"],
          pi["inputSchema"]["required"])


def test_tkgm_projeksiyon():
    tablo = [(27.0, 5253), (30.0, 5254), (33.0, 5255), (36.0, 5256), (39.0, 5257),
             (42.0, 5258), (45.0, 5259), (48.0, 5260), (51.0, 5261), (54.0, 5262)]
    hatalar = [(lon, srv.tm_epsg(lon), epsg) for lon, epsg in tablo if srv.tm_epsg(lon) != epsg]
    check("EPSG 5253-5262 eslemesi", not hatalar, hatalar)

    e, n = srv.tm_forward(41.04855, 40.61055)
    check("Rize TM42 koordinati", abs(e - 383182.591) < 0.01 and abs(n - 4546895.252) < 0.01, (e, n))

    en = 0.0
    for lat in (36.0, 38.0, 40.0, 42.0):
        for d in (-1.5, 0.0, 1.5):
            lon = 38.0 + d
            lon0 = srv.tm_merkez_boylam(lon)
            e, n = srv.tm_forward(lat, lon, lon0)
            bl, bn = srv.tm_inverse(e, n, lon0)
            en = max(en, math.hypot((bl - lat) * 110574.0,
                                    (bn - lon) * 111320.0 * math.cos(math.radians(lat))))
    check("WGS84->TM->WGS84 round-trip < 1 mm (%.4f mm)" % (en * 1000), en < 0.001, en)


def test_imar_parser():
    k = imar.normalize(imar.parse_imar_html(fixture_text("rize_157_59.html")), {})
    check("Rize fixture: emsal 3.0", k["emsal"] == 3.0 and k["kaks"] == 3.0, k["emsal"])
    check("Rize fixture: bina yuksekligi 30.5", k["bina_yuksekligi_m"] == 30.5, k["bina_yuksekligi_m"])
    check("Rize fixture: plan adi", "RİZE (MERKEZ) REVİZYON" in (k["plan_adi"] or ""), k["plan_adi"])
    check("Rize fixture: kullanim karari", k["kullanim_karari"] == "Yerleşik Konut Alanı", k["kullanim_karari"])

    b = imar.normalize(imar.parse_imar_html(fixture_text("burdur_412_20.html")), {})
    check("Burdur (sablon B): plan adi", "BURDUR İLAVE REVİZYON" in (b["plan_adi"] or ""), b["plan_adi"])
    check("Burdur: kat 2 / nizam BITISIK", b["kat_sayisi"] == 2 and b["yapi_nizami"] == "BİTİŞİK", b["yapi_nizami"])
    check("Burdur: EMPTYROW sizmamasi", "EMPTYROW" not in (b["plan_adi"] or ""), b["plan_adi"])

    a = imar.normalize(imar.parse_imar_html(fixture_text("aksaray_668_214.html")), {})
    check("Aksaray: emsal 1.8 / TAKS 0.4 / kat 4",
          a["emsal"] == 1.8 and a["taks"] == 0.4 and a["kat_sayisi"] == 4, (a["emsal"], a["taks"], a["kat_sayisi"]))
    check("Aksaray: yapi nizami A", a["yapi_nizami"] == "A", a["yapi_nizami"])

    m = imar.normalize(imar.parse_imar_html(fixture_text("amasya_2061_13.html")), {})
    check("Amasya: plan adi ve aciklama notu",
          "AMASYA" in (m["plan_adi"] or "") and any("Açıklama" in n for n in m["plan_notlari"]),
          m["plan_notlari"][:1])

    check("TR sayi: '10.974,80 m2' -> 10974.8", imar.tr_num("10.974,80 m²") == 10974.8)
    check("TR sayi: '30.5' -> 30.5", imar.tr_num("30.5") == 30.5)
    check("emsal: '- (3,00)' -> 3.0", imar._emsal("- (3,00)") == 3.0)
    check("emsal: '- (-)' -> None", imar._emsal("- (-)") is None)


def test_imar_config():
    check("20 il tanimli", len(imar.ILLER) == 20, len(imar.ILLER))
    aktif = [il for il, c in imar.ILLER.items() if c.get("durum") == "aktif"]
    check("13 il aktif", len(aktif) == 13, aktif)
    check("aktif illerde base URL var", all(imar.ILLER[i].get("base") for i in aktif))
    kapali = [il for il, c in imar.ILLER.items() if c.get("durum") != "aktif"]
    check("kapali illerde durum + portal/not var",
          all(imar.ILLER[i].get("url") and imar.ILLER[i].get("not") for i in kapali), kapali)
    check("noverify yalniz Nevsehir+Sivas",
          sorted(il for il, c in imar.ILLER.items() if c.get("tls") == "noverify") == ["Nevşehir", "Sivas"])

    err, t = call("imar_iller_listele", {})
    check("imar_iller_listele 20 / 13 aktif", not err and t["adet"] == 20 and t["aktif_adet"] == 13, t)

    err, t = call("imar_sorgula", {"il": "Giresun", "ada": "1", "parsel": "1"})
    check("Giresun -> kapsam disi (bakimda)", err and t["hata_tipi"] == "bakimda", t)

    err, t = call("imar_sorgula", {"il": "Atlantis", "ada": "1", "parsel": "1"})
    check("bilinmeyen il -> kapsam_disi", err and t["hata_tipi"] == "kapsam_disi", t)


def test_dxf():
    feature = fixture("parsel_rize_157_59.geojson")
    cozum = {"il": "Rize", "ilce": "Merkez", "mahalle": "Tophane",
             "mahalle_id": 123938, "ada": "157", "parsel": "59"}
    imar_kayit = imar.normalize(imar.parse_imar_html(fixture_text("rize_157_59.html")), {})
    imar_kayit["durum"] = "ok"
    imar_kayit["plan_adi"] = "RİZE (MERKEZ) REVİZYON UYGULAMA İMAR PLANI"

    sonuc = srv.parsel_dxf_uret(feature, cozum, "tm", out_dir=TMP, imar_kayit=imar_kayit)
    dosya = sonuc["dosyalar"][0]["dosya"]
    check("DXF uretildi (IMAR katmani)", os.path.exists(dosya) and sonuc["dosyalar"][0]["imar_katmani"], dosya)
    katmanlar = dxf_katmanlar(dosya)
    check("DXF katmanlari PARSEL + PARSEL_ETIKET + IMAR",
          katmanlar == ["IMAR", "PARSEL", "PARSEL_ETIKET"], katmanlar)
    imar_yazilar = [e["tags"].get("1") for e in dxf_metinler(dosya)
                    if e["type"] == "TEXT" and e["tags"].get("8") == "IMAR"]
    check("IMAR katmaninda 4 satir", len(imar_yazilar) == 4, imar_yazilar)
    check("IMAR satiri emsal/TAKS/KAKS iceriyor",
          any("Emsal 3" in y and "TAKS" in y for y in imar_yazilar), imar_yazilar)
    check("IMAR satiri plan adini iceriyor", any("Plan: RIZE" in y for y in imar_yazilar), imar_yazilar)

    # imar yoksa IMAR katmani eklenmemeli
    sonuc2 = srv.parsel_dxf_uret(feature, cozum, "tm", out_dir=TMP)
    check("imar yokken IMAR katmani yok", "IMAR" not in dxf_katmanlar(sonuc2["dosyalar"][0]["dosya"]))


def test_env():
    hedef = os.path.join(TMP, "env-cikti")
    env = dict(os.environ)
    env["PARSELIMAR_CIKTI"] = hedef
    env["PARSELIMAR_CACHE"] = os.path.join(TMP, "env-cache")
    out = subprocess.check_output(
        [sys.executable, "-c", "import server; print(server.OUT_DIR); print(server.CACHE_DIR)"],
        cwd=HERE, env=env, universal_newlines=True).splitlines()
    check("PARSELIMAR_CIKTI/CACHE env", out[0] == hedef and out[1].endswith("env-cache"), out)


def call(name, args):
    r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": name, "arguments": args}})
    res = r.get("result") or {}
    text = (res.get("content") or [{}])[0].get("text", "")
    try:
        return bool(res.get("isError")), json.loads(text)
    except ValueError:
        return bool(res.get("isError")), {"_text": text}


def run_offline():
    print("MOD: offline (ag erisimi yok)\n")
    test_sema()
    test_tkgm_projeksiyon()
    test_imar_parser()
    test_imar_config()
    test_dxf()
    test_env()


# ---------------------------------------------------------------- online

def run_online():
    print("MOD: online (gercek TKGM + e-Imar)\n")
    proc = subprocess.Popen([sys.executable, SERVER], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, bufsize=1)
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
            raise RuntimeError("sunucu kapandi; stderr: %s" % proc.stderr.read())
        return json.loads(line)

    def cagir(name, args):
        res = rpc("tools/call", {"name": name, "arguments": args}).get("result") or {}
        return bool(res.get("isError")), json.loads((res.get("content") or [{}])[0].get("text", "{}"))

    try:
        init = rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                  "clientInfo": {"name": "test", "version": "1"}})
        check("initialize", (init.get("result") or {}).get("serverInfo", {}).get("name") == "parsel-imar", init)
        rpc("notifications/initialized", {}, notify=True)
        check("tools/list 12", len(rpc("tools/list")["result"]["tools"]) == 12)

        err, t = cagir("parsel_bul", {"il_adi": "Rize", "ilce_adi": "Merkez", "mahalle_adi": "Tophane",
                                      "ada": "157", "parsel": "59"})
        check("TKGM parsel_bul (Rize/Tophane 157/59)", not err and t.get("alan_m2") == 640.26, t)

        err, t = cagir("parsel_imar", {"il": "Rize", "ilce": "Merkez", "mahalle": "Tophane",
                                       "ada": "157", "parsel": "59"})
        check("parsel_imar Rize: TKGM + imar", not err and t["tkgm"]["alan_m2"] == 640.26
              and t["imar"].get("emsal") == 3.0, t.get("imar"))
        check("parsel_imar Rize: DXF + IMAR katmani",
              t["dxf"]["dosyalar"][0]["imar_katmani"] and os.path.exists(t["dxf"]["dosyalar"][0]["dosya"]),
              t.get("dxf"))
        print("      Rize:", json.dumps({k: t["imar"].get(k) for k in
              ("plan_adi", "emsal", "taks", "bina_yuksekligi_m", "kullanim_karari")}, ensure_ascii=False))

        err, t = cagir("parsel_imar", {"il": "Aksaray", "ilce": "Merkez", "mahalle": "Kılıçarslan",
                                       "ada": "668", "parsel": "214"})
        check("parsel_imar Aksaray: emsal 1.8 / TAKS 0.4",
              not err and t["imar"].get("emsal") == 1.8 and t["imar"].get("taks") == 0.4, t.get("imar"))
        print("      Aksaray:", json.dumps({k: t["imar"].get(k) for k in
              ("plan_adi", "emsal", "taks", "kat_sayisi", "yapi_nizami")}, ensure_ascii=False))

        err, t = cagir("imar_sorgula", {"il": "Tunceli", "mahalle": "Alpdoğan", "ada": "276", "parsel": "9"})
        check("Tunceli: emsal 1.2 / H 16.5", not err and t.get("emsal") == 1.2
              and t.get("bina_yuksekligi_m") == 16.5, t)

        err, t = cagir("imar_sorgula", {"il": "Niğde", "mahalle": "Aşağıkayabaşı", "ada": "104", "parsel": "5"})
        check("Niğde: emsal 0.1 / TAKS 0.05", not err and t.get("emsal") == 0.1
              and t.get("taks") == 0.05, t)

        err, t = cagir("imar_sorgula", {"il": "Giresun", "ada": "1", "parsel": "1"})
        check("Giresun kapali (bakimda)", err and t.get("hata_tipi") == "bakimda", t)

        err, t = cagir("imar_mahalleler_listele", {"il": "Rize"})
        check("Rize mahalle listeleri", not err and t["adet"] > 20 and t["tapu_adet"] > 20, t.get("adet"))
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)


def main(argv):
    run_online() if "--online" in argv else run_offline()
    print("\n%d test gecti, %d basarisiz" % (OK, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
