# Changelog

## v3.0.2 — 2026-10-29

- Kaynak sayfa **2 yatay A4** sayfaya bölündü: (1) başlık + harita, (2) tablo. Ekran görüntüsü
  kırpılıp 8 mm kenarla yerleştirildi → görüntü alanı **%84 / %87** (önce %69, dikey sayfa).
- Harita ipucu (`.map-hint-bubble`) çekimden önce gizleniyor → tooltip karede yok.
- `parsel.png`: kuzey oku "K" harfi okun 3·h üstüne, düz ve ayrık yerleştirildi.
- `paket.pdf` artık **5 sayfa** (3 dikey özet + 2 yatay kaynak); boş sayfa yok.
- Testler: **28 offline, 25 online** (sayfa sayısı, yataylık, görüntü alanı ≥%70, kenar ≤1 cm).

## v3.0.1 — 2026-10-29

PDF ve çizim görsel hataları düzeltildi (v3.0.0 çıktılarıyla uyumlu, araçlar değişmedi).

- Çizim sayfası: görsel artık başlığın altındaki içerik alanına **contain** yerleştiriliyor
  (ölçek uygulanmıyordu → sayfanın dışına taşıyordu). Sayfa 3'te görsel alanı **%58**.
- Kaynak sayfa: print CSS haritayı boşalttığı için **tam sayfa ekran görüntüsü** A4 dikey
  sayfaya raster yerleştiriliyor; harita + tablo görünür, tek sayfa, taşan boş sayfa yok.
- Özet sayfa 2: eksik alanlar "belirtilmemiş", plan notu yoksa "Plan notu bulunamadı",
  kaynak URL + küçük harita önizlemesi eklendi (sayfa artık boş görünmüyor).
- `parsel.png`: ölçü yazıları kenardan 2,5–5 m dışa taşındı, kenara paralel döndürüldü,
  arkasına yarı saydam beyaz kutu kondu; tuval kenar boşluğu %8'e çıkarıldı.
- Testler: **28 offline, 21 online** (sayfa sayısı, boş sayfa, görsel alan oranı, harita renk çeşitliliği).

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
