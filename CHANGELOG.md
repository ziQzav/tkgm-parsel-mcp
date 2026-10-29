# CHANGELOG

Tüm önemli değişiklikler bu dosyada tutulur.
Biçim: [Keep a Changelog](https://keepachangelog.com/tr/1.1.0/), sürümleme: [SemVer](https://semver.org/lang/tr/).

## [1.0.0] — 2026-10-29

İlk sürüm.

### Eklenenler

- **7 MCP aracı** (stdio, satır bazlı JSON-RPC 2.0, harici bağımlılık yok):
  - `iller_listele`, `ilceler_listele`, `mahalleler_listele`
  - `parsel_sorgula` (mahalle id + ada + parsel, GeoJSON çıktısı)
  - `parsel_bul` (il/ilçe/mahalle adından id çözer)
  - `parsel_sorgula_koordinat` (WGS84 lat/lon)
  - `parsel_dxf` (CAD için DXF)
- **DXF dışa aktarma**: AutoCAD R12 (AC1009) ASCII, kapalı `POLYLINE` (`PARSEL` katmanı),
  köşe numaraları ve alan etiketi (`PARSEL_ETIKET` katmanı). `crs=tm` (varsayılan),
  `wgs84` veya `both`.
- **TUREF/ITRF96 3° Transverse Mercator** projeksiyonu saf Python (stdlib) ile:
  GRS80, k=1, FE=500000, FN=0, zondan otomatik EPSG (5253–5262).
  pyproj ile fark < 0,2 mm, round-trip < 0,1 mm.
- Türkçe/aksan duyarsız ad eşleştirme (`Çankaya` = `Cankaya`).
- 3 denemeli ağ katmanı, 400 ms istek aralığı, disk önbelleği (liste 7 gün, parsel 1 saat).
- `TKGMPARSEL_CIKTI` / `TKGMPARSEL_CACHE` ortam değişkenleriyle çıktı ve önbellek yolu.
- Testler: `test_mcp.py --offline` (ağ yok, 35 kontrol) ve `--online` (gerçek TKGM, 14 kontrol).
- GitHub Actions matrisi: Python 3.8 / 3.10 / 3.12, yalnızca offline test.
- Örnek istemci yapılandırmaları: Claude Desktop, OpenCode, Cursor; örnek DXF ve PNG önizleme.

### Bilinen sınırlar

- Resmî API değil; TKGM arayüzü değişirse kırılabilir.
- Yalnızca kadastral geometri ve özet nitelikler; tapu sahibi/hisse/ipotek verisi yok.
- DWG desteklenmez (DXF her CAD programında açılır).
- TLS sertifika doğrulaması kapalı (`CERT_NONE`).
