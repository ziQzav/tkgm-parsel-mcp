# Changelog

## v3.0.0 — 2026-10-29

**"Çizim öncesi bilgi toplama" sürümü.** 18 araçtan 8 araca indirildi; hesap ve maliyet kaldırıldı;
PDF artık portalın kendi sonuç sayfasını birebir içeriyor.

### Kaldırılanlar (v2 → v3)

- `maliyet.py` (arsa, inşaat, proje, ruhsat, tapu, finansman, KDV) — **tamamen kaldırıldı**
- `onproje.py` hesap motoru (taban/toplam inşaat, kat, daire, dükkan, otopark, sığınak, asansör)
- Kural motoru: `kural_listesi`, `kural_kontrol`, `kural_ozeti`, `kural_guncelle`, `kurallar/*.json`, tazelik hesabı
- Ölü kod: `tr_lower`, `IMAR_OUT_DIR`, `_ozel_notlar`, `imar_link`, `dxf_yaz`, `_svg_rect`, `kat_plani`, `birim_yerlesimi`
- Araçlar: `on_proje`, `parsel_sorgula`, `parsel_sorgula_koordinat`, `parsel_imar`, `dosya_olustur`,
  `iller/ilceler/mahalleler_listele`, `imar_ilceler/mahalleler_listele`
  (yerine `parsel`, `liste`, `paket`; eski adlar 1 sürüm alias, `on_proje` ve `kural_*` net hata)

### Eklenenler

- **8 araç:** `parsel`, `imar_sorgula`, `imar_kapsam`, `liste`, `cizim`, `kaynak_sayfa`, `paket`, `durum`
- **`kaynak_sayfa`**: headless Chromium (playwright) ile portalın `imar.aspx` sayfasından print-PDF +
  tam sayfa PNG; varsa imar planı PDF'i indirilir
- **`paket`**: 3 sayfalık özet PDF + kaynak sayfa(lar) → `paket.pdf` (pypdf birleştirme) + DXF + PNG + GeoJSON + ZIP
- Tek JSON şeması: `{sorgu, parsel, imar, kaynak_sayfa, cizim, uyarilar, zaman}`
- Testler: **24 offline, 14 online**
