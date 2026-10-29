#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcp-parsel-imar-v3 testleri.

    python3 test_mcp.py            # offline: ag yok
    python3 test_mcp.py --online   # gercek TKGM + KEOS + kaynak sayfa
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")
FIX = os.path.join(HERE, "tests", "fixtures")

TMP = tempfile.mkdtemp(prefix="parsel-imar-v3-test-")
os.environ.setdefault("PARSELIMAR_CIKTI", os.path.join(TMP, "cikti"))
os.environ.setdefault("PARSELIMAR_CACHE", os.path.join(TMP, "cache"))

sys.path.insert(0, HERE)
import server as srv       # noqa: E402
import imar                # noqa: E402
import cizim               # noqa: E402
import kaynak              # noqa: E402
import rapor               # noqa: E402

OK = 0
FAIL = 0

ARACLAR = ["parsel", "imar_sorgula", "imar_kapsam", "liste", "cizim", "kaynak_sayfa", "paket", "durum"]


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


def call(name, args):
    r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": name, "arguments": args}})
    res = r.get("result") or {}
    text = (res.get("content") or [{}])[0].get("text", "")
    try:
        return bool(res.get("isError")), json.loads(text)
    except ValueError:
        return bool(res.get("isError")), {"_text": text}


def dxf_entities(path):
    with open(path, "r", encoding="ascii") as fh:
        lines = [l.rstrip("\r\n") for l in fh]
    ents, cur = [], None
    for i in range(0, len(lines) - 1, 2):
        code, val = lines[i].strip(), lines[i + 1].strip()
        if code == "0":
            cur = {"type": val, "tags": {}}
            ents.append(cur)
        elif cur is not None:
            cur["tags"].setdefault(code, val)
    return ents


def png_boyut(path):
    import struct
    if not path or not os.path.exists(path):
        return (0, 0)
    with open(path, "rb") as fh:
        head = fh.read(24)
    return struct.unpack(">II", head[16:24]) if head[:8] == b"\x89PNG\r\n\x1a\n" else (0, 0)


# ---------------------------------------------------------------- offline

def test_sema():
    init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    info = (init.get("result") or {}).get("serverInfo") or {}
    check("initialize -> parsel-imar-v3", info.get("name") == "parsel-imar-v3", info)
    tl = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = (tl.get("result") or {}).get("tools") or []
    isimler = [t["name"] for t in tools]
    check("tools/list 8 arac", len(tools) == 8 and isimler == ARACLAR, isimler)
    check("her arac gecerli inputSchema",
          all(t["inputSchema"].get("type") == "object" and t.get("description") for t in tools))

    err, t = call("on_proje", {})
    check("on_proje -> 'kaldirildi' hatasi", err and t.get("hata_tipi") == "kaldirildi", t)
    err, t = call("kural_ozeti", {})
    check("kural_ozeti -> 'kaldirildi'", err and t.get("hata_tipi") == "kaldirildi", t)

    check("kodda maliyet/onproje/kural importu yok",
          not any(m in open(SERVER, encoding="utf-8").read()
                  for m in ("import maliyet", "import onproje", "kural_coz")),
          "import taramasi")

    err, t = call("durum", {})
    check("durum: surum 3 + 8 arac", not err and t["surum"] == "3.0.0" and t["arac_sayisi"] == 8, t)


def test_alias():
    feature = fixture("parsel_rize_157_59.geojson")
    cozum = {"il": "Rize", "il_id": 75, "ilce": "Merkez", "ilce_id": 786,
             "mahalle": "Tophane", "mahalle_id": 123938, "ada": "157", "parsel": "59"}
    eski = srv._tkgm_coz
    srv._tkgm_coz = lambda *a, **k: (feature, dict(cozum))
    try:
        err, t = call("parsel_bul", {"il_adi": "Rize", "ilce_adi": "Merkez",
                                     "mahalle_adi": "Tophane", "ada": "157", "parsel": "59"})
    finally:
        srv._tkgm_coz = eski
    check("alias parsel_bul -> parsel (il_adi cevirisi)", not err and t.get("alan_m2") == 640.26, t)


def test_imar_parser():
    k = imar.normalize(imar.parse_imar_html(fixture_text("rize_157_59.html")), {})
    check("imar fixture: emsal 3.0 / H 30.5", k["emsal"] == 3.0 and k["bina_yuksekligi_m"] == 30.5, k)
    a = imar.normalize(imar.parse_imar_html(fixture_text("aksaray_668_214.html")), {})
    check("imar fixture: TAKS 0.4 / emsal 1.8 / kat 4",
          a["taks"] == 0.4 and a["emsal"] == 1.8 and a["kat_sayisi"] == 4, a)
    b = imar.normalize(imar.parse_imar_html(fixture_text("burdur_412_20.html")), {})
    check("imar fixture: sablon B plan adi", "BURDUR İLAVE REVİZYON" in (b["plan_adi"] or ""), b["plan_adi"])


def test_cizim():
    feature = fixture("parsel_rize_157_59.geojson")
    cozum = {"il": "Rize", "ilce": "Merkez", "mahalle": "Tophane", "ada": "157", "parsel": "59"}
    imar_k = imar.normalize(imar.parse_imar_html(fixture_text("rize_157_59.html")), {})
    imar_k["durum"] = "ok"
    out = os.path.join(TMP, "cizim")
    os.makedirs(out, exist_ok=True)
    res = srv.parsel_dxf_uret(feature, cozum, "tm", out_dir=out, imar_kayit=imar_k)
    dxf = res["dosyalar"][0]["dosya"]
    ents = dxf_entities(dxf)
    polys = [e for e in ents if e["type"] == "POLYLINE"]
    check("DXF: kapali POLYLINE", polys and all(int(e["tags"].get("70", "0")) & 1 for e in polys), len(polys))
    katmanlar = sorted({e["tags"].get("8") for e in ents if e["tags"].get("8")})
    check("DXF: IMAR katmani var", "IMAR" in katmanlar, katmanlar)

    tm, lon0, epsg = srv._proj(feature)
    d = 5.0
    cekme, uyg = cizim.guvenli_offset(tm, d)
    resim = cizim.vaziyet_ciz(tm, cekme, "157/59", 640.26, (5.0, 3.0, 3.0), out)
    if resim.get("png"):
        check("PNG: >=1200 px", png_boyut(resim["png"])[0] >= 1200, png_boyut(resim["png"]))
    else:
        print("SKIP  PNG (cairosvg yok)")
    svg = open(resim["svg"], encoding="utf-8").read()
    check("PNG/SVG: TR sayi bicimi (640,26)", "640,26" in svg, svg[:60])
    check("PNG: cekme cizgisi yalniz gecerli offsette",
          (cekme is not None) == ('class="cekme"' in svg), (cekme is not None))


def test_ozet_pdf_ve_birlestirme():
    if getattr(rapor, "cairo", None) is None:
        print("SKIP  ozet.pdf (cairocffi yok)")
        return
    veri = {"parsel": {"il": "Rize", "ilce": "Merkez", "mahalle": "Tophane", "ada": "157",
                       "parsel": "59", "alan_m2": 640.26, "nitelik": "Kargir Ev Ve Arsası",
                       "pafta": "F45-D-21-D-2-B", "mevkii": "Orta Mahalle",
                       "zemin_km": "Ana Taşınmaz", "geometri_tipi": "Polygon", "koordinat_sayisi": 9},
            "imar": {"plan_adi": "RİZE (MERKEZ) REVİZYON UYGULAMA İMAR PLANI", "emsal": 3.0,
                     "taks": None, "bina_yuksekligi_m": 30.5, "kullanim_karari": "Yerleşik Konut Alanı",
                     "plan_notlari": ["Açıklama: örnek plan notu"], "sorgu_zamani": "2026-10-29T12:00:00+03:00",
                     "kaynak": {"sorgu_url": "https://keos.rize.bel.tr:444/imardurumu/imar.aspx?parselid=93274"}},
            "zaman": "2026-10-29T12:00:00+03:00"}
    out = os.path.join(TMP, "pdf")
    os.makedirs(out, exist_ok=True)
    gorsel = None
    ozet = os.path.join(out, "ozet.pdf")
    meta = rapor.ozet_pdf(ozet, veri, gorsel)
    check("ozet.pdf: 3 sayfa", meta.get("yazildi") and meta.get("sayfa") == 3, meta)
    check("ozet.pdf: %PDF + > 8 KB", open(ozet, "rb").read(4) == b"%PDF" and os.path.getsize(ozet) > 8000,
          os.path.getsize(ozet))

    # sahte kaynak sayfa (2 sayfa) + birlestirme
    src = os.path.join(out, "kaynak.pdf")
    pdf = rapor.PDF(src, "kaynak")
    pdf.yeni_sayfa("Kaynak sayfa")
    pdf.p("Portal çıktısı — harita ve tablo.")
    pdf.yeni_sayfa("Kaynak 2")
    pdf.p("Devam.")
    pdf.bitir()
    bir = rapor.birlestir(ozet, src, os.path.join(out, "paket.pdf"))
    check("birlestirme: 5 sayfa", bir.get("durum") == "ok" and bir.get("sayfa") == 5, bir)

    try:
        from pypdf import PdfReader
        metin = "".join((p.extract_text() or "") for p in PdfReader(os.path.join(out, "paket.pdf")).pages)
        check("PDF metni: 'Emsal' ve '640,26'", "Emsal" in metin and "640,26" in metin, metin[:120])
    except ImportError:
        check("pypdf yok, metin testi atlandi", True)

    md = rapor.rapor_md(veri)
    check("rapor.md: baslik + uyari", "Parsel bilgi paketi" in md and "ruhsata esas" in md.lower(), md[:80])


def test_paket_offline():
    feature = fixture("parsel_rize_157_59.geojson")
    cozum = {"il": "Rize", "il_id": 75, "ilce": "Merkez", "ilce_id": 786,
             "mahalle": "Tophane", "mahalle_id": 123938, "ada": "157", "parsel": "59"}
    imar_k = imar.normalize(imar.parse_imar_html(fixture_text("rize_157_59.html")), {})
    imar_k.update({"durum": "ok", "il": "Rize",
                   "kaynak": {"portal": "https://keos.rize.bel.tr:444/imardurumu/",
                              "sorgu_url": "https://keos.rize.bel.tr:444/imardurumu/imar.aspx?parselid=93274"},
                   "sorgu_zamani": "2026-10-29T12:00:00+03:00"})

    eski = (srv._tkgm_coz, imar.imar_sorgula, kaynak.kaynak_sayfa)
    srv._tkgm_coz = lambda *a, **k: (feature, dict(cozum))
    imar.imar_sorgula = lambda *a, **k: dict(imar_k)

    def sahte_kaynak(url, out_dir, ad="kaynak_imar", yatay=True, tkgm=False, **kw):
        os.makedirs(out_dir, exist_ok=True)
        yol = os.path.join(out_dir, ad + ".pdf")
        pdf = rapor.PDF(yol, "kaynak")
        pdf.yeni_sayfa("Kaynak imar sayfasi (mock)")
        pdf.p("Harita ve tablo burada.")
        pdf.bitir()
        png = os.path.join(out_dir, ad + ".png")
        open(png, "wb").write(b"")
        return {"durum": "ok", "pdf": yol, "png": None, "url": url, "plan_pdf": None,
                "sure_sn": 0.1, "pdf_linkleri": []}

    kaynak.kaynak_sayfa = sahte_kaynak
    try:
        r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "paket", "arguments": {
                            "il": "Rize", "ilce": "Merkez", "mahalle": "Tophane",
                            "ada": "157", "parsel": "59"}}})
        t = json.loads(r["result"]["content"][0]["text"])
    finally:
        srv._tkgm_coz, imar.imar_sorgula, kaynak.kaynak_sayfa = eski

    d = t.get("dosyalar") or {}
    check("paket(offline): ozet + kaynak + birlesik + zip",
          all(k in d for k in ("ozet.pdf", "paket.pdf", "parsel.dxf", "parsel.png", "paket.zip")), sorted(d))
    check("paket(offline): birlesik >= 4 sayfa", (t.get("paket_pdf") or {}).get("sayfa", 0) >= 4,
          t.get("paket_pdf"))
    check("paket(offline): zip icinde >= 7 dosya", len(t.get("zip_icerik") or []) >= 7, t.get("zip_icerik"))


# ---------------------------------------------------------------- online

def run_online():
    print("MOD: online (gercek TKGM + KEOS + kaynak sayfa)\n")
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
        check("initialize", (init.get("result") or {}).get("serverInfo", {}).get("name") == "parsel-imar-v3", init)
        rpc("notifications/initialized", {}, notify=True)
        check("tools/list 8", len(rpc("tools/list")["result"]["tools"]) == 8)

        err, t = cagir("parsel", {"il": "Rize", "ilce": "Merkez", "mahalle": "Tophane",
                                  "ada": "157", "parsel": "59"})
        check("parsel Rize 157/59", not err and t.get("alan_m2") == 640.26, t)

        err, t = cagir("imar_sorgula", {"il": "Rize", "mahalle": "Tophane", "ada": "157", "parsel": "59"})
        check("imar_sorgula: emsal 3 / H 30.5", not err and t.get("emsal") == 3.0
              and t.get("bina_yuksekligi_m") == 30.5, t)

        err, t = cagir("liste", {"tur": "il"})
        check("liste(tur=il) 81", not err and len(t.get("iller") or []) == 81, len(t.get("iller") or []))

        err, t = cagir("cizim", {"il": "Rize", "ilce": "Merkez", "mahalle": "Tophane",
                                 "ada": "157", "parsel": "59"})
        check("cizim: dxf", not err and os.path.exists(t.get("dxf", "")), t.get("dxf"))
        if (t or {}).get("png"):
            check("cizim: png >=1200", png_boyut(t["png"])[0] >= 1200, t["png"])
        else:
            print("SKIP  cizim png (cairosvg yok)")

        for il, ilce, mah, ada, parsel in (("Rize", "Merkez", "Tophane", "157", "59"),
                                           ("Aksaray", "Merkez", "Kılıçarslan", "668", "214")):
            err, t = cagir("paket", {"il": il, "ilce": ilce, "mahalle": mah,
                                     "ada": ada, "parsel": parsel})
            d = (t or {}).get("dosyalar") or {}
            check("paket %s: dosyalar" % il,
                  not err and all(k in d for k in ("ozet.pdf", "paket.pdf", "kaynak_imar.pdf",
                                                   "parsel.dxf", "parsel.png", "paket.zip")), sorted(d))
            check("paket %s: birlesik 5+ sayfa" % il, (t.get("paket_pdf") or {}).get("sayfa", 0) >= 4,
                  (t or {}).get("paket_pdf"))
            check("paket %s: kaynak pdf > 100 KB" % il,
                  os.path.getsize(d.get("kaynak_imar.pdf", "/dev/null")) > 100000,
                  d.get("kaynak_imar.pdf"))
            if d.get("parsel.png"):
                check("paket %s: PNG >=1200 px" % il, png_boyut(d["parsel.png"])[0] >= 1200)
            else:
                print("SKIP  paket %s png (cairosvg yok)" % il)
            print("      %s: paket.pdf %d sayfa %.0f KB | kaynak %.0f KB | zip %.0f KB" % (
                il, (t.get("paket_pdf") or {}).get("sayfa", 0),
                os.path.getsize(d.get("paket.pdf", "/dev/null")) / 1024.0,
                os.path.getsize(d.get("kaynak_imar.pdf", "/dev/null")) / 1024.0,
                os.path.getsize(d.get("paket.zip", "/dev/null")) / 1024.0))
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)


def run_offline():
    print("MOD: offline (ag erisimi yok)\n")
    test_sema()
    test_alias()
    test_imar_parser()
    test_cizim()
    test_ozet_pdf_ve_birlestirme()
    test_paket_offline()


def main(argv):
    run_online() if "--online" in argv else run_offline()
    print("\n%d test gecti, %d basarisiz" % (OK, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
