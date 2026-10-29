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
    sonuc = {"durum": "ok", "url": url, "pdf": None, "png": None, "pdf_linkleri": [],
             "plan_pdf": None, "sure_sn": None, "hata": None}
    t0 = time.time()

    async def isler():
        async with async_playwright() as p:
            kw = {"args": ["--no-sandbox"]}
            if exe:
                kw["executable_path"] = exe
            browser = await p.chromium.launch(**kw)
            ctx = await browser.new_context(viewport={"width": 1280, "height": 1600},
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
                await page.emulate_media(media="print")
            except Exception:
                pass
            await page.pdf(path=pdf_yol, format="A4", landscape=bool(yatay),
                           print_background=True,
                           margin={"top": "8mm", "bottom": "8mm", "left": "8mm", "right": "8mm"})
            try:
                await page.emulate_media(media="screen")
            except Exception:
                pass
            await page.screenshot(path=png_yol, full_page=True)
            try:
                hrefs = await page.eval_on_selector_all(
                    "a[href]", "els => els.map(e => e.href)")
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

    try:
        asyncio.run(isler())
    except Exception as exc:
        sonuc["durum"] = "hata"
        sonuc["hata"] = "%s: %s" % (type(exc).__name__, exc)
    sonuc["sure_sn"] = round(time.time() - t0, 1)
    if os.path.exists(pdf_yol) and os.path.getsize(pdf_yol) > 5000:
        sonuc["pdf"] = pdf_yol
    else:
        sonuc["durum"] = "hata" if sonuc["durum"] == "ok" else sonuc["durum"]
        sonuc["hata"] = sonuc["hata"] or "pdf uretilemedi"
    if os.path.exists(png_yol) and os.path.getsize(png_yol) > 5000:
        sonuc["png"] = png_yol
    return sonuc


def kaynak_sayfa(url, out_dir, ad="kaynak_imar", yatay=True, bekle_ms=3500, timeout_s=90):
    d = durum()
    if not d["hazir"]:
        return {"durum": "bagimlilik_yok", "url": url,
                "hata": "playwright/chromium yok (kaynak sayfa alinamadi)",
                "pdf": None, "png": None, "plan_pdf": None}
    return _calistir(url, out_dir, ad, yatay, bekle_ms, timeout_s)
