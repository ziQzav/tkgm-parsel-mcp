#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rapor: 3 sayfalik ozet PDF (cairocffi) + kaynak sayfa PDF'i ile birlestirme (pypdf).

Opsiyonel bagimliliklar: cairocffi (PDF), pypdf (birlestirme). Yoksa JSON/MD rapor kalir.
"""

import json
import os
import re
import zlib

try:
    import cairocffi as cairo
except Exception:
    cairo = None

A4 = (595.28, 841.89)
KENAR = 46
UST = 64
ALT = 52
FONT_NORMAL = "DejaVu Sans"
FONT_BOLD = "DejaVu Sans"
RENK = {
    "siyah": (0.10, 0.12, 0.16), "gri": (0.42, 0.45, 0.50), "cizgi": (0.80, 0.82, 0.86),
    "vurgu": (0.06, 0.36, 0.72), "yesil": (0.10, 0.50, 0.25), "kirmizi": (0.72, 0.12, 0.12),
    "sari": (0.70, 0.48, 0.00),
}


def _font_var():
    for d in ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu",
              "/usr/local/share/fonts", os.path.expanduser("~/.fonts")):
        if os.path.isdir(d) and any(f.lower().startswith("dejavusans.ttf") for f in os.listdir(d)):
            return True
    return False

def _tr_num(v, ond=2):
    """1234567.5 -> '1.234.567,50' (TR: binlik nokta, ondalik virgul)."""
    try:
        t = ("{:,." + str(ond) + "f}").format(float(v))     # 7,683,120.00
    except (TypeError, ValueError):
        return str(v)
    return t.replace(",", "X").replace(".", ",").replace("X", ".")

def _tl(v):
    if v is None:
        return "—"
    return _tr_num(v, 2) + " TL"

def _sayi(v, iki=False):
    if v is None:
        return "—"
    if iki:
        return _tr_num(v, 2)
    t = _tr_num(v, 1)
    return t[:-2] if t.endswith(",0") else t

class PDF(object):
    """Basit A4 PDF yazici (cairo). Tum metin vektor/secilebilir."""

    def __init__(self, path, ust_bilgi=""):
        if cairo is None:
            raise RuntimeError("cairocffi yok")
        if not _font_var():
            raise RuntimeError("DejaVu Sans fontu bulunamadi")
        self.path = path
        self.surface = cairo.PDFSurface(path, A4[0], A4[1])
        self.c = cairo.Context(self.surface)
        self.sayfa = 0
        self.ust = ust_bilgi
        self.y = 0

    # ---- temel
    def _set(self, boyut, kalin=False, renk="siyah"):
        self.c.select_font_face(FONT_NORMAL if not kalin else FONT_BOLD,
                                cairo.FONT_SLANT_NORMAL,
                                cairo.FONT_WEIGHT_BOLD if kalin else cairo.FONT_WEIGHT_NORMAL)
        self.c.set_font_size(boyut)
        self.c.set_source_rgb(*RENK.get(renk, RENK["siyah"]))

    def genislik(self, metin, boyut, kalin=False):
        self._set(boyut, kalin)
        return self.c.text_extents(metin)[4]

    def _sar(self, metin, boyut, genislik, kalin=False):
        self._set(boyut, kalin)
        kelimeler = str(metin).split()
        satirlar, cur = [], ""
        for k in kelimeler:
            test = (cur + " " + k).strip()
            if self.c.text_extents(test)[4] <= genislik or not cur:
                cur = test
            else:
                satirlar.append(cur)
                cur = k
        if cur:
            satirlar.append(cur)
        # hucreye sigmayan tek kelimeleri karakterden kir (tasma olmasin)
        out = []
        for satir in satirlar or [""]:
            if self.c.text_extents(satir)[4] <= genislik:
                out.append(satir)
                continue
            parca = ""
            for ch in satir:
                if parca and self.c.text_extents(parca + ch)[4] > genislik:
                    out.append(parca)
                    parca = ch
                else:
                    parca += ch
            if parca:
                out.append(parca)
        return out or [""]

    def _yaz(self, x, y, metin, boyut, kalin=False, renk="siyah"):
        self._set(boyut, kalin, renk)
        self.c.move_to(x, y)
        self.c.show_text(str(metin))

    # ---- sayfa
    def yeni_sayfa(self, baslik=None):
        if self.sayfa:
            self.sayfa_bitir()
        self.sayfa += 1
        self.c.set_source_rgb(1, 1, 1)
        self.c.rectangle(0, 0, A4[0], A4[1])
        self.c.fill()
        # ustbilgi
        self._yaz(KENAR, 34, self.ust, 8, False, "gri")
        self._yaz(A4[0] - KENAR - self.genislik("parsel-imar", 8), 34, "parsel-imar", 8, False, "gri")
        self.c.set_source_rgb(*RENK["cizgi"])
        self.c.set_line_width(0.6)
        self.c.move_to(KENAR, 42)
        self.c.line_to(A4[0] - KENAR, 42)
        self.c.stroke()
        if baslik:
            self.h1(baslik)

    def sayfa_bitir(self):
        # altbilgi + sayfa no
        self.c.set_source_rgb(*RENK["cizgi"])
        self.c.set_line_width(0.6)
        self.c.move_to(KENAR, A4[1] - ALT + 12)
        self.c.line_to(A4[0] - KENAR, A4[1] - ALT + 12)
        self.c.stroke()
        self._yaz(KENAR, A4[1] - 30, "Ruhsata esas proje degildir.", 7.5, False, "gri")
        numara = "Sayfa %d" % self.sayfa
        self._yaz(A4[0] - KENAR - self.genislik(numara, 8), A4[1] - 30, numara, 8, False, "gri")
        self.c.show_page()
        self.y = UST

    def bitir(self):
        self.sayfa_bitir()
        self.surface.finish()

    def bosluk(self, n=8):
        self.y += n

    def h1(self, metin):
        self._yaz(KENAR, self.y + 12, metin, 15, True, "vurgu")
        self.y += 24

    def h2(self, metin):
        self._yaz(KENAR, self.y + 10, metin, 11, True)
        self.y += 20

    def p(self, metin, boyut=9.5, renk="siyah", girinti=0):
        for satir in self._sar(metin, boyut, A4[0] - 2 * KENAR - girinti):
            self._yaz(KENAR + girinti, self.y + boyut, satir, boyut, False, renk)
            self.y += boyut + 2.6
        self.y += 3

    def madde(self, metin, boyut=9.5):
        kalem = "• "
        ilk = True
        for satir in self._sar(metin, boyut, A4[0] - 2 * KENAR - 14):
            onek = kalem if ilk else "  "
            self._yaz(KENAR, self.y + boyut, onek + satir, boyut)
            self.y += boyut + 2.6
            ilk = False
        self.y += 2

    # ---- tablolar
    def tablo(self, basliklar, satirlar, genislikler=None, boyut=8.6, devam="devam"):
        if not satirlar:
            self.p("(kayit yok)", boyut)
            return
        toplam = A4[0] - 2 * KENAR
        if not genislikler:
            genislikler = [toplam / len(basliklar)] * len(basliklar)
        else:
            oran = toplam / float(sum(genislikler))
            genislikler = [g * oran for g in genislikler]

        # en uzun kelimeye gore minimum genislik; hucreler komsuya tasmaz
        mins = []
        for i, b in enumerate(basliklar):
            mx = self.genislik(str(b), boyut - 0.4, True)
            for r in satirlar:
                hucre = "" if i >= len(r) or r[i] is None else str(r[i])
                for w in hucre.split():
                    mx = max(mx, self.genislik(w, boyut))
            mins.append(mx + 16)
        for i in range(len(genislikler)):
            genislikler[i] = max(genislikler[i], mins[i])
        if sum(genislikler) > toplam:
            if sum(mins) <= toplam:
                fazla = sum(genislikler) - toplam
                esnek = [max(0.0, genislikler[i] - mins[i]) for i in range(len(genislikler))]
                es = sum(esnek) or 1.0
                genislikler = [genislikler[i] - fazla * esnek[i] / es for i in range(len(genislikler))]
            else:
                k = toplam / sum(mins)
                genislikler = [m * k for m in mins]
        k = toplam / sum(genislikler)
        genislikler = [g * k for g in genislikler]

        def ciz_baslik():
            h = 16
            self.c.set_source_rgb(0.93, 0.95, 0.98)
            self.c.rectangle(KENAR, self.y, toplam, h)
            self.c.fill()
            x = KENAR
            for b, g in zip(basliklar, genislikler):
                self._yaz(x + 6, self.y + 11, b, boyut - 0.4, True)
                x += g
            self.y += h
            self.c.set_source_rgb(*RENK["cizgi"])
            self.c.set_line_width(0.5)
            self.c.move_to(KENAR, self.y)
            self.c.line_to(KENAR + toplam, self.y)
            self.c.stroke()

        ciz_baslik()
        alt = A4[1] - ALT
        for i, satir in enumerate(satirlar):
            hucreler = ["" if v is None else str(v) for v in satir]
            satir_satirlari = [self._sar(h, boyut, g - 12) for h, g in zip(hucreler, genislikler)]
            yuk = max(len(s) for s in satir_satirlari) * (boyut + 2.4) + 6
            if self.y + yuk > alt - 4:
                self.sayfa_bitir()
                self.y = UST
                self._yaz(KENAR, self.y, devam, 9, True, "gri")
                self.y += 14
                ciz_baslik()
            if i % 2 == 0:
                self.c.set_source_rgb(0.975, 0.975, 0.98)
                self.c.rectangle(KENAR, self.y, toplam, yuk)
                self.c.fill()
            x = KENAR
            for sira, (s_liste, g) in enumerate(zip(satir_satirlari, genislikler)):
                yy = self.y + boyut + 3
                for s in s_liste:
                    # sutunlar arasi ayrim: metin cikariminda da bosluk kalsin
                    yazi = s if sira == len(genislikler) - 1 else s + " "
                    self._yaz(x + 6, yy, yazi, boyut)
                    yy += boyut + 2.4
                x += g
            self.y += yuk
        self.c.set_source_rgb(*RENK["cizgi"])
        self.c.set_line_width(0.5)
        self.c.move_to(KENAR, self.y)
        self.c.line_to(KENAR + toplam, self.y)
        self.c.stroke()
        self.y += 10

    def gorsel(self, path, baslik=None, alt_yazi=None):
        surf = cairo.ImageSurface.create_from_png(path)
        w, h = surf.get_width(), surf.get_height()
        alan_w = A4[0] - 2 * KENAR
        alan_h = A4[1] - UST - ALT - (40 if alt_yazi else 20)
        olcek = min(alan_w / w, alan_h / h)
        gx, gy = w * olcek, h * olcek
        x = (A4[0] - gx) / 2
        self.c.set_source_surface(surf, x, UST + 6)
        self.c.paint()
        self.y = UST + 6 + gy + 10
        if alt_yazi:
            self._yaz((A4[0] - self.genislik(alt_yazi, 8))/2, self.y + 8, alt_yazi, 8, False, "gri")
            self.y += 16

def pdf_sayfa_sayisi(path):
    """PDF icindeki sayfa nesnelerini (sikistirilmis objstm dahil) sayar."""
    try:
        data = open(path, "rb").read()
    except Exception:
        return 0
    n = len(re.findall(rb"/Type\s*/Page[^s]", data))
    if n:
        return n
    # FlateDecode akislarini ac ve objstm icindekileri say
    for m in re.finditer(rb"stream\r?\n", data):
        bas = m.end()
        son = data.find(b"endstream", bas)
        if son < 0:
            continue
        try:
            coz = zlib.decompress(data[bas:son])
        except Exception:
            continue
        n += len(re.findall(rb"/Type\s*/Page[^s]", coz))
    return n


# ---------------------------------------------------------------- ozet PDF (3 sayfa)

def _kv_tablo(pdf, satirlar, genislik=(1.1, 2.9)):
    pdf.tablo(["Alan", "Değer"], satirlar, list(genislik))


def ozet_pdf(path, veri, gorsel=None):
    """3 sayfa: 1 kapak+parsel, 2 imar, 3 cizim + kaynak. Donus: {yazildi,sayfa,neden}."""
    if cairo is None:
        return {"yazildi": False, "sayfa": 0, "neden": "cairocffi yok"}
    if not _font_var():
        return {"yazildi": False, "sayfa": 0, "neden": "DejaVu Sans fontu yok"}

    p = veri.get("parsel") or {}
    im = veri.get("imar") or {}
    kimlik = "%s/%s" % (p.get("ada"), p.get("parsel"))
    ust = "%s / %s / %s  •  %s" % (p.get("il"), p.get("ilce"), p.get("mahalle"), kimlik)
    tarih = (veri.get("zaman") or "")[:10]
    try:
        pdf = PDF(path, ust)

        # 1 — kapak + parsel
        pdf.yeni_sayfa(None)
        pdf._yaz(KENAR, 150, "PARSEL BİLGİ PAKETİ", 24, True, "vurgu")
        pdf._yaz(KENAR, 178, "%s / %s / %s — %s" % (p.get("il"), p.get("ilce"), p.get("mahalle"), kimlik),
                 12, False, "gri")
        pdf._yaz(KENAR, 198, "Rapor tarihi: %s" % tarih, 10, False, "gri")
        pdf.y = 236
        pdf.h2("Parsel (TKGM)")
        _kv_tablo(pdf, [
            ["Yüzölçümü", "%s m²" % _sayi(p.get("alan_m2"), True)],
            ["Nitelik", p.get("nitelik") or "—"],
            ["Pafta", p.get("pafta") or "—"],
            ["Mevkii", p.get("mevkii") or "—"],
            ["Zemin / Kat Mülkiyeti", p.get("zemin_km") or "—"],
            ["Geometri", "%s · %s koordinat" % (p.get("geometri_tipi") or "—", p.get("koordinat_sayisi") or "—")],
            ["Kaynak", (veri.get("kaynak") or {}).get("tkgm") or "—"],
        ])
        pdf.bosluk(8)
        pdf.p("Bu paket bilgi toplama amaçlıdır. Ruhsata esas proje değildir; mimari, statik ve "
              "zemin etüdü onayı gerekir. İmar bilgisi ve harita, kaynak portalın kendi "
              "sayfasından birebir alınmıştır (ekli sayfalar).")

        # 2 — imar
        pdf.yeni_sayfa("İmar durumu (kaynak portal)")
        if veri.get("imar_hata"):
            ih = veri["imar_hata"]
            pdf.p("İmar verisi alınamadı: %s (%s)" % (ih.get("mesaj"), ih.get("hata_tipi")), 9.5, "kirmizi")
        _kv_tablo(pdf, [
            ["Plan adı", im.get("plan_adi") or "—"],
            ["Kullanım / fonksiyon", im.get("kullanim_karari") or "—"],
            ["Emsal (KAKS)", _sayi(im.get("emsal"), True)],
            ["TAKS", _sayi(im.get("taks"), True)],
            ["Bina yüksekliği (H)", "%s m" % _sayi(im.get("bina_yuksekligi_m"), True)],
            ["Kat sayısı (imar)", _sayi(im.get("kat_sayisi"))],
            ["Yapı nizamı", im.get("yapi_nizami") or "—"],
            ["Çekme (ön/yan/arka)", "%s / %s / %s m" % (
                _sayi((im.get("cekme_mesafeleri") or {}).get("on")),
                _sayi((im.get("cekme_mesafeleri") or {}).get("yan")),
                _sayi((im.get("cekme_mesafeleri") or {}).get("arka")))],
            ["Pafta / ölçek", "%s / %s" % (im.get("pafta") or "—", im.get("olcek") or "—")],
            ["Kaynak URL", (im.get("kaynak") or {}).get("sorgu_url") or "—"],
            ["Son kontrol", im.get("sorgu_zamani") or "—"],
        ])
        notlar = im.get("plan_notlari") or []
        if notlar:
            pdf.bosluk(4)
            pdf.h2("Plan notları")
            for n in notlar[:12]:
                pdf.madde(n, 8.6)

        # 3 — cizim
        pdf.yeni_sayfa("Parsel çizimi (1/500)")
        if gorsel and os.path.exists(gorsel):
            pdf.gorsel(gorsel, alt_yazi="Ölçek 1/500 · %s · %s" % (tarih, kimlik))
        else:
            pdf.p("Çizim üretilemedi (cairosvg yok veya geometri uygun değil).", 9.5, "kirmizi")
        pdf.bitir()
        return {"yazildi": True, "sayfa": pdf.sayfa, "neden": None,
                "sayfa_dogrulama": pdf_sayfa_sayisi(path)}
    except Exception as exc:
        return {"yazildi": False, "sayfa": 0, "neden": "%s: %s" % (type(exc).__name__, exc)}


def birlestir(ozet, kaynak, hedef):
    """Ozet PDF + kaynak sayfa PDF -> tek PDF (pypdf). Yoksa iki dosya kalir."""
    try:
        from pypdf import PdfWriter, PdfReader
    except Exception:
        return {"durum": "pypdf_yok", "yol": None,
                "not": "pypdf kurulu degil; ozet ve kaynak PDF ayri dosya olarak birakildi."}
    try:
        w = PdfWriter()
        sayfa = 0
        for yol in (ozet, kaynak):
            if yol and os.path.exists(yol):
                r = PdfReader(yol)
                for pg in r.pages:
                    w.add_page(pg)
                    sayfa += 1
        if not sayfa:
            return {"durum": "bos", "yol": None, "not": "birlestirilecek PDF yok"}
        with open(hedef, "wb") as fh:
            w.write(fh)
        return {"durum": "ok", "yol": hedef, "sayfa": sayfa}
    except Exception as exc:
        return {"durum": "hata", "yol": None, "not": "%s: %s" % (type(exc).__name__, exc)}


def rapor_md(veri):
    p = veri.get("parsel") or {}
    im = veri.get("imar") or {}
    satir = ["# Parsel bilgi paketi — %s/%s %s/%s" % (p.get("il"), p.get("ilce"), p.get("mahalle"),
                                                      "%s/%s" % (p.get("ada"), p.get("parsel"))), ""]
    satir.append("> Bilgi toplama amaçlıdır; ruhsata esas proje değildir. İmar bilgisi kaynak "
                 "portalın kendi sayfasından birebir alınmıştır.")
    satir.append("")
    satir.append("## Parsel (TKGM)")
    satir.append("")
    satir.append("| Alan | Değer |")
    satir.append("|---|---|")
    for ad, deger in (("Yüzölçümü", "%s m²" % _sayi(p.get("alan_m2"), True)),
                      ("Nitelik", p.get("nitelik")), ("Pafta", p.get("pafta")),
                      ("Mevkii", p.get("mevkii")), ("Zemin/KM", p.get("zemin_km")),
                      ("Geometri", p.get("geometri_tipi")),
                      ("Koordinat sayısı", p.get("koordinat_sayisi"))):
        satir.append("| %s | %s |" % (ad, deger if deger not in (None, "") else "—"))
    satir.append("")
    satir.append("## İmar")
    satir.append("")
    satir.append("| Alan | Değer |")
    satir.append("|---|---|")
    for ad, deger in (("Plan", im.get("plan_adi")), ("Kullanım", im.get("kullanim_karari")),
                      ("Emsal", _sayi(im.get("emsal"), True)), ("TAKS", _sayi(im.get("taks"), True)),
                      ("H (m)", _sayi(im.get("bina_yuksekligi_m"), True)),
                      ("Kat", _sayi(im.get("kat_sayisi"))), ("Nizam", im.get("yapi_nizami")),
                      ("Kaynak", (im.get("kaynak") or {}).get("sorgu_url"))):
        satir.append("| %s | %s |" % (ad, deger if deger not in (None, "") else "—"))
    if im.get("plan_notlari"):
        satir.append("")
        satir.append("### Plan notları")
        for n in im["plan_notlari"]:
            satir.append("- %s" % n)
    satir.append("")
    satir.append("## Dosyalar")
    satir.append("")
    for ad, yol in (veri.get("dosyalar") or {}).items():
        satir.append("- %s: `%s`" % (ad, yol))
    return "\n".join(satir) + "\n"
