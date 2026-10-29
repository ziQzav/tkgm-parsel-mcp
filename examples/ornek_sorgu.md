# Örnek sorgular

Aşağıdakileri MCP istemcinize (Claude Desktop, Claude Code, Cursor, OpenCode, VS Code…)
doğal dille yazabilirsiniz. Araç adları ve parametreleri parantez içinde.

## 1. İsimden parsel sorgusu

> Rize Merkez Hamidiye 1895/4 parselini sorgula.

Araç: `parsel_bul`

```json
{
  "name": "parsel_bul",
  "arguments": {
    "il_adi": "Rize",
    "ilce_adi": "Merkez",
    "mahalle_adi": "Hamidiye",
    "ada": "1895",
    "parsel": "4"
  }
}
```

Beklenen özet: `Rize / Merkez / Hamidiye Köyü`, `alan_m2: 2372.25`, `koordinat_sayisi: 9`,
`mahalle_id: 123992`.

## 2. DXF üretimi (metre, TM42)

> Hamidiye 1895/4 için DXF üret, crs=tm.

Araç: `parsel_dxf`

```json
{
  "name": "parsel_dxf",
  "arguments": {
    "il_adi": "Rize",
    "ilce_adi": "Merkez",
    "mahalle_adi": "Hamidiye",
    "ada": "1895",
    "parsel": "4",
    "crs": "tm"
  }
}
```

Dönen `dosyalar[0]`: `{"dosya": "cikti/Hamidiye_Köyü_1895_4_tm.dxf", "epsg": 5258, "birim": "metre"}`.
Dosyayı LibreCAD/QCAD/AutoCAD ile açın. Depodaki hazır örnek:
[`docs/ornek_rize_1895_4_tm.dxf`](../docs/ornek_rize_1895_4_tm.dxf).

## 3. İki koordinat sisteminde birden

> Aynı parselin WGS84 ve TM sürümlerini üret (crs=both).

`"crs": "both"` → iki dosya: `..._tm.dxf` (metre) ve `..._wgs84.dxf` (derece).

## 4. Mahalle id'si ile sorgu

> 55834 numaralı mahallede 2531/7 parselini sorgula.

Araç: `parsel_sorgula`

```json
{
  "name": "parsel_sorgula",
  "arguments": {"mahalle_id": 55834, "ada": "2531", "parsel": "7"}
}
```

## 5. Koordinattan parsel

> 39.9075, 32.8620 noktası hangi parselde?

Araç: `parsel_sorgula_koordinat`

```json
{
  "name": "parsel_sorgula_koordinat",
  "arguments": {"lat": 39.9075, "lon": 32.8620}
}
```

## 6. İdari yapı listeleri

- `iller_listele` → 81 il, `id` = plaka kodu
- `ilceler_listele({"il_id": 23})` → Adana'nın 15 ilçesi
- `mahalleler_listele({"ilce_id": 104})` → Aladağ'ın 31 mahallesi

## 7. Elle JSON-RPC (terminal)

Sunucu stdio üzerinden satır bazlı JSON-RPC 2.0 konuşur; her satır bir mesajdır.

```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1"}}}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"iller_listele","arguments":{}}}' \
 '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"parsel_dxf","arguments":{"mahalle_id":123992,"ada":"1895","parsel":"4","crs":"both"}}}' \
 | python3 server.py
```

İlk satır `initialize` yanıtı, ikincisi 7 aracın şeması, sonrakiler araç sonuçlarıdır.
`isError: true` gelen yanıtlarda `content[0].text` Türkçe hata mesajını taşır.

## İpuçları

- Ada/parsel bilinmiyorsa önce `parsel_sorgula_koordinat` deneyin.
- Türkçe karakter sorun olursa ASCII yazın: `Cankaya`, `Kucukesat`, `Hamidiye`.
- Üretilen dosyalar varsayılan olarak depo içindeki `cikti/` klasörüne yazılır;
  `TKGMPARSEL_CIKTI` ile değiştirebilirsiniz.
- `alan_m2` TKGM kayıt alanı, `alan_hesap_m2` DXF geometrisinin alanıdır; küçük farklar normaldir.
