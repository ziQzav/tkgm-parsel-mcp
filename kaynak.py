#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kaynak sayfa yakalama: portalın kendi sonuç sayfasını (imar.aspx) headless
Chromium ile print-PDF + tam sayfa PNG olarak alır; varsa imar planı PDF'ini indirir.

Opsiyonel bagimlilik: playwright + chromium. Yoksa {"durum": "bagimlilik_yok"}.
"""

import glob
import os
import time

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")


def chromium_yolu():
    yol = os.environ.get("PLAYWRIGHT_CHROMIUM")
    if yol and os.path.exists(yol):
        return yol
    adaylar = []
    for pat in ("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome",
                "~/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
                "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"):
        adaylar += glob.glob(os.path.expanduser(pat))
    for a in adaylar:
        if os.path.exists(a) and os.access(a, os.X_OK):
            return a
    return None


def durum():
    try:
        import playwright  # noqa: F401
        var = True
    except Exception:
        var = False
    exe = chromium_yolu()
    return {"playwright": var, "chromium": bool(exe), "chromium_yolu": exe,
            "hazir": bool(var and exe)}


def _calistir(url, out_dir, ad, yatay, bekle_ms, timeout_s):
    import asyncio
    from playwright.async_api import async_playwright

    exe = chromium_yolu()
    os.makedirs(out_dir, exist_ok=True)
    pdf_yol = os.path.join(out_dir, ad + ".pdf")
    png_yol = os.path.join(out_dir, ad + ".png")
    harita_yol = os.path.join(out_dir, ad + "_harita.png")
    tablo_yol = os.path.join(out_dir, ad + "_tablo.png")
    sonuc = {"durum": "ok", "url": url, "pdf": None, "png": None, "pdf_linkleri": [],
             "plan_pdf": None, "sure_sn": None, "hata": None, "tooltip_gizlendi": False,
             "kaynak_sayfa": 0}
    t0 = time.time()

    async def isler():
        async with async_playwright() as p:
            kw = {"args": ["--no-sandbox"]}
            if exe:
                kw["executable_path"] = exe
            browser = await p.chromium.launch(**kw)
            ctx = await browser.new_context(viewport={"width": 1600, "height": 1800},
                                            user_agent=UA, accept_downloads=True)
            page = await ctx.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=timeout_s * 1000)
            except Exception:
                try:
                    await page.goto(url, wait_until="load", timeout=timeout_s * 1000)
                except Exception:
                    pass
            await page.wait_for_timeout(bekle_ms)
            try:
                await page.mouse.move(1, 1)
                await page.keyboard.press("Escape")
                sonuc["tooltip_gizlendi"] = bool(await page.evaluate("""() => {
                    let n = 0;
                    document.querySelectorAll('.map-hint-bubble').forEach(e => {
                        e.classList.remove('visible'); e.style.display = 'none'; n++;
                    });
                    return n;
                }"""))
            except Exception:
                pass
            await page.wait_for_timeout(400)
            await page.screenshot(path=png_yol, full_page=True)

            try:
                box = await page.evaluate("""() => {
                    const g = e => e ? e.getBoundingClientRect() : null;
                    const m = g(document.querySelector('#map-container'));
                    const t = g(document.querySelector('#htmlOutput'));
                    return {map: m && {x: m.x, y: m.y, w: m.width, h: m.height},
                            tablo: t && {x: t.x, y: t.y, w: t.width, h: t.height}};
                }""")
            except Exception:
                box = {}

            klipler = []
            if box.get("map"):
                m = box["map"]
                k = {"x": max(0.0, m["x"]), "y": 0.0, "width": m["w"],
                     "height": min(m["y"] + m["h"] + 20.0, 1800.0)}
                try:
                    await page.screenshot(path=harita_yol, clip=k, full_page=True)
                    klipler.append(harita_yol)
                except Exception:
                    pass
            if box.get("tablo"):
                t = box["tablo"]
                k = {"x": max(0.0, t["x"] - 20.0), "y": t["y"],
                     "width": min(t["w"] + 40.0, 1200.0), "height": t["h"]}
                try:
                    await page.screenshot(path=tablo_yol, clip=k, full_page=True)
                    klipler.append(tablo_yol)
                except Exception:
                    pass
            try:
                hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            except Exception:
                hrefs = []
            pdf_linkler = [h for h in hrefs if isinstance(h, str) and ".pdf" in h.lower()]
            sonuc["pdf_linkleri"] = sorted(set(pdf_linkler))[:5]
            if pdf_linkler:
                try:
                    r = await ctx.request.get(pdf_linkler[0], timeout=45000)
                    if r.ok:
                        govde = await r.body()
                        if govde[:4] == b"%PDF":
                            plan = os.path.join(out_dir, "imar_plani.pdf")
                            with open(plan, "wb") as fh:
                                fh.write(govde)
                            sonuc["plan_pdf"] = plan
                except Exception as exc:
                    sonuc["plan_pdf_hata"] = "%s: %s" % (type(exc).__name__, exc)
            await ctx.close()
            await browser.close()
            return klipler

    try:
        klipler = asyncio.run(isler())
    except Exception as exc:
        sonuc["durum"] = "hata"
        sonuc["hata"] = "%s: %s" % (type(exc).__name__, exc)
        klipler = []

    sonuc["sure_sn"] = round(time.time() - t0, 1)
    if os.path.exists(png_yol) and os.path.getsize(png_yol) > 5000:
        sonuc["png"] = png_yol
    else:
        sonuc["durum"] = "hata" if sonuc["durum"] == "ok" else sonuc["durum"]
        sonuc["hata"] = sonuc["hata"] or "ekran goruntusu uretilemedi"

    kaynaklar = [x for x in klipler if os.path.exists(x) and os.path.getsize(x) > 5000]
    if not kaynaklar and sonuc["png"]:
        kaynaklar = [sonuc["png"]]
    if kaynaklar:
        try:
            import rapor
            meta = rapor.gorsel_pdf(kaynaklar, pdf_yol, yatay=True, kenar=22.7)
            if meta.get("yazildi"):
                sonuc["pdf"] = pdf_yol
                sonuc["kaynak_sayfa"] = meta.get("sayfa", 0)
            else:
                sonuc["pdf_hata"] = meta.get("neden")
        except Exception as exc:
            sonuc["pdf_hata"] = "%s: %s" % (type(exc).__name__, exc)
    return sonuc


def kaynak_sayfa(url, out_dir, ad="kaynak_imar", yatay=False, bekle_ms=3500, timeout_s=90):
    d = durum()
    if not d["hazir"]:
        return {"durum": "bagimlilik_yok", "url": url,
                "hata": "playwright/chromium yok (kaynak sayfa alinamadi)",
                "kurulum": "./setup.sh --with-browser",
                "pdf": None, "png": None, "plan_pdf": None}
    return _calistir(url, out_dir, ad, yatay, bekle_ms, timeout_s)
