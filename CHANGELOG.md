# Changelog

## v2.0.0 — 2026-10-29

TKGM parsel MCP'si ile Rize imar MCP'si tek sunucuda birleşti.

- **12 araç:** 7 TKGM (liste, ada/parsel, koordinat, DXF) + 4 imar + birleşik `parsel_imar`
- **Netcad KEOS imar desteği: 13 il** (Amasya, Burdur, Diyarbakır, Isparta, Kırşehir,
  Nevşehir, Niğde, Rize, Sivas, Tunceli, Şanlıurfa, Uşak, Aksaray)
- `parsel_imar`: tek çağrıda kadastro + imar + DXF; imar kapsam dışıysa TKGM verisi yine döner
- DXF'e `IMAR` katmanı: emsal/TAKS/KAKS, plan adı, kullanım, kat/H/nizam
- 6 farklı KEOS HTML şablonu için etiket sözlüğü; ISO-8859-9 desteği
- Mahalle eşlemesi (TKGM ↔ KEOS tapu mahallesi) + belirsizlikte aday listesi
- Hosta özel TLS istisnası yalnız Nevşehir/Sivas için; genel doğrulama açık
- Testler: 37 offline, 10 online

## v1.0.0 — 2026-10-29

- İlk sürüm: TKGM parsel sorgusu (7 araç), DXF dışa aktarma (EPSG 5253-5262 / WGS84)
