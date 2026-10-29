#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Netcad KEOS e-Imar adaptoru (Rize + 19 il).

20 ilin 13'unde ayni servis deseni calisiyor; 7 il olculen bir engel yuzunden
kapsam disi (giris, captcha, bakim, 404, 503, zaman asimi). Engel turleri
config'te tutulur ve kullaniciya aynen bildirilir.

Harici bagimlilik yok: Python 3.8+ stdlib.
"""

import hashlib
import json
import os
import re
import ssl
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0.0.0 Safari/537.36")
HTTP_TIMEOUT = 30
RETRIES = 3
MIN_INTERVAL = 0.50
LIST_TTL = 7 * 24 * 3600
IMAR_TTL = 24 * 3600
CAND_TTL = 6 * 3600
TR_TZ = timezone(timedelta(hours=3))

HERE = os.path.dirname(os.path.abspath(__file__))


def _env_dir(var, default):
    value = os.environ.get(var)
    if value:
        return os.path.abspath(os.path.expanduser(value))
    return os.path.join(HERE, default)


OUT_DIR = _env_dir("PARSELIMAR_CIKTI", "cikti")
CACHE_DIR = _env_dir("PARSELIMAR_CACHE", "cache")

# durum: aktif | giris_gerekli | captcha | bakimda | modul_yok | erisilemedi | zaman_asimi
# tls   : verify | noverify (yalniz bu host icin; sertifika zinciri dogrulanamiyor)
# enc   : utf-8 | iso-8859-9
ILLER = {
    "Adana": {"durum": "giris_gerekli", "url": "https://keos.adana.bel.tr/BELNET/LoginFW/Login.aspx",
              "not": "BELNET kurumsal giris ekrani; vatandas ada/parsel sorgusu giris istiyor."},
    "Amasya": {"durum": "aktif", "base": "https://keos.amasya.bel.tr/imardurumu/", "tls": "verify", "enc": "utf-8"},
    "Burdur": {"durum": "aktif", "base": "https://keos.burdur.bel.tr:444/imardurumu/", "tls": "verify", "enc": "utf-8"},
    "Diyarbakır": {"durum": "aktif", "base": "https://cbs.diyarbakir.bel.tr/imardurumu/", "tls": "verify", "enc": "utf-8"},
    "Giresun": {"durum": "bakimda", "url": "https://keos.giresun.bel.tr/imardurumu/",
                "not": "imar.aspx yerine maintenance.aspx donduruyor (olcum: 195 KB bakim sayfasi)."},
    "Hatay": {"durum": "zaman_asimi", "url": "https://keos.hatay.bel.tr/imardurumu/",
              "not": "ada/parsel servisi calisiyor ancak imar.aspx 45 sn'de yanit vermedi (olcum: TimeoutError)."},
    "Isparta": {"durum": "aktif", "base": "https://keos.isparta.bel.tr/imardurumu/", "tls": "verify", "enc": "utf-8"},
    "Kırşehir": {"durum": "aktif", "base": "http://keos.kirsehir.bel.tr/imardurumu/", "tls": "verify", "enc": "utf-8"},
    "Kahramanmaraş": {"durum": "captcha", "url": "https://keos.kahramanmaras.bel.tr/imardurumu/",
                      "not": "imar.aspx -> recaptcha.aspx?key=... (Guvenlik Dogrulamasi); captcha cozulmez."},
    "Nevşehir": {"durum": "aktif", "base": "https://keos.nevsehir.bel.tr/imardurumu/", "tls": "noverify", "enc": "utf-8"},
    "Niğde": {"durum": "aktif", "base": "https://keos.nigde.bel.tr/imardurumu/", "tls": "verify", "enc": "utf-8"},
    "Rize": {"durum": "aktif", "base": "https://keos.rize.bel.tr:444/imardurumu/", "tls": "verify", "enc": "utf-8"},
    "Samsun": {"durum": "giris_gerekli", "url": "https://cbs.samsun.bel.tr/BELNET/LoginFW/Login.aspx",
               "not": "Kurumsal CBS giris ekrani."},
    "Sivas": {"durum": "aktif", "base": "https://keos.sivas.bel.tr/imardurumu/", "tls": "noverify", "enc": "utf-8"},
    "Tokat": {"durum": "modul_yok", "url": "https://keos.tokat.bel.tr/",
              "not": "/imardurumu/, /keos/imardurumu/, /imar/ -> HTTP 404 (olculdu)."},
    "Tunceli": {"durum": "aktif", "base": "http://cbs.tunceli.bel.tr/imardurumu/", "tls": "verify", "enc": "utf-8"},
    "Şanlıurfa": {"durum": "aktif", "base": "https://keos.sanliurfa.bel.tr/imardurumu/", "tls": "verify", "enc": "iso-8859-9"},
    "Uşak": {"durum": "aktif", "base": "https://keos.usak.bel.tr/imardurumu/", "tls": "verify", "enc": "utf-8"},
    "Van": {"durum": "erisilemedi", "url": "http://cbs.van.bel.tr/keos/",
            "not": "/keos/imardurumu/ -> 404, /imardurumu/ -> HTTP 503 (olculdu)."},
    "Aksaray": {"durum": "aktif", "base": "https://ebelediye.aksaray.bel.tr:444/imardurumu/", "tls": "verify", "enc": "iso-8859-9"},
}

_CTX_OK = ssl.create_default_context()
_CTX_NV = ssl.create_default_context()
_CTX_NV.check_hostname = False
_CTX_NV.verify_mode = ssl.CERT_NONE

_last_request = 0.0


def log(msg):
    import sys
    sys.stderr.write("[imar] %s\n" % msg)
    sys.stderr.flush()


def fold(text):
    if text is None:
        return ""
    s = str(text).strip()
    s = s.replace("\u0130", "i").replace("I", "i").replace("\u0131", "i")
    s = s.replace("\u015e", "s").replace("\u015f", "s")
    s = s.replace("\u011e", "g").replace("\u011f", "g")
    s = s.replace("\u00c7", "c").replace("\u00e7", "c")
    s = s.replace("\u00d6", "o").replace("\u00f6", "o")
    s = s.replace("\u00dc", "u").replace("\u00fc", "u")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).casefold().strip()


def tr_num(value):
    """'10.974,80 m²' / '30.5' / '- (3,00)' -> float | None."""
    if value is None:
        return None
    s = str(value).replace("\u00b2", "").replace("m2", "").strip()
    m = re.search(r"-?\d+(?:[.,]\d+)*", s)
    if not m:
        return None
    num = m.group(0)
    if "," in num:
        num = num.replace(".", "").replace(",", ".")
    elif "." in num:
        parts = num.split(".")
        if len(parts) > 1 and len(parts[-1]) == 3 and len(parts[0]) <= 3:
            num = "".join(parts)
    try:
        return float(num)
    except ValueError:
        return None


def now_tr():
    return datetime.now(TR_TZ).replace(microsecond=0).isoformat()


class ApiError(Exception):
    def __init__(self, mesaj, tip="hata", ek=None):
        Exception.__init__(self, mesaj)
        self.mesaj = mesaj
        self.tip = tip
        self.ek = ek or {}

    def as_dict(self):
        d = {"durum": "hata", "hata_tipi": self.tip, "mesaj": self.mesaj}
        d.update(self.ek)
        return d


def il_bilgi(il):
    if not il:
        raise ApiError("il zorunlu. imar_iller_listele ile desteklenen illeri gorebilirsiniz.", "gecersiz_girdi")
    for ad, cfg in ILLER.items():
        if fold(ad) == fold(il):
            return ad, cfg
    raise ApiError("Desteklenmiyor: '%s'. Desteklenen iller: %s" % (il, ", ".join(sorted(ILLER))),
                   "kapsam_disi", {"desteklenen_iller": sorted(ILLER)})


def _ctx(cfg):
    return _CTX_NV if cfg.get("tls") == "noverify" else _CTX_OK


def _nonce():
    return hashlib.sha1(os.urandom(32)).hexdigest()


def _throttle():
    global _last_request
    wait = MIN_INTERVAL - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()


def _cache_path(url):
    return os.path.join(CACHE_DIR, hashlib.sha1(("imar|" + url).encode("utf-8")).hexdigest() + ".json")


def _cache_get(url, ttl):
    try:
        with open(_cache_path(url), "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        if time.time() - blob.get("ts", 0) <= ttl:
            return blob.get("data")
    except Exception:
        pass
    return None


def _cache_put(url, data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = _cache_path(url) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ts": time.time(), "url": url, "data": data}, fh, ensure_ascii=False)
        os.replace(tmp, _cache_path(url))
    except Exception as exc:
        log("onbellek yazilamadi: %s" % exc)


def _save_raw(ad, icerik):
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, ad)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(icerik if isinstance(icerik, str) else json.dumps(icerik, ensure_ascii=False, indent=1))
        return path
    except Exception as exc:
        log("ham yanit yazilamadi: %s" % exc)
        return None


def _looks_login(text):
    low = (text or "").lower()
    if len(low) > 200000:
        return "kullanici-girisi" in low or "name=\"password\"" in low
    return ("kullanici-girisi" in low or "giris yap" in low or
            ("<form" in low and "password" in low) or "loginfw" in low)


def _fetch(url, cfg, tip="json", ttl=0):
    cached = _cache_get(url, ttl) if ttl else None
    if cached is not None:
        return cached
    last = None
    for attempt in range(1, RETRIES + 1):
        _throttle()
        n = _nonce()
        headers = {
            "User-Agent": UA,
            "Accept": "application/json, text/javascript, text/html, */*; q=0.01",
            "Accept-Language": "tr-TR,tr;q=0.9",
            "Referer": url.rsplit("/service/", 1)[0] + "/" if "/service/" in url else url,
            "X-Requested-With": "XMLHttpRequest",
            "Cookie": "svc_nonce=%s" % n,
            "X-Service-Nonce": n,
        }
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=_ctx(cfg)) as resp:
                raw = resp.read()
            text = raw.decode(cfg.get("enc", "utf-8"), "replace")

            if "recaptcha" in text.lower() or "guvenlik dogrulamasi" in fold(text) or "güvenlik doğrulaması" in text.lower():
                raise ApiError("Portal captcha (guvenlik dogrulamasi) istiyor; otomatik sorgu yapilamaz.",
                               "captcha", {"portal": cfg.get("base") or cfg.get("url")})
            if "maintenance.aspx" in text.lower():
                raise ApiError("Portal bakim modunda (maintenance.aspx).", "bakimda",
                               {"portal": cfg.get("base") or cfg.get("url")})
            if _looks_login(text):
                raise ApiError("Portal oturum/giris istiyor; hesap acilmaz, e-Devlet girisi yapilmaz.",
                               "giris_gerekli", {"portal": cfg.get("base") or cfg.get("url")})

            if tip == "json":
                data = json.loads(text)
            else:
                data = text
            if ttl:
                _cache_put(url, data)
            return data
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                last = "HTTP %d" % exc.code
            elif exc.code == 404:
                raise ApiError("Portal adresi bulunamadi (HTTP 404): %s" % url, "modul_yok")
            else:
                last = "HTTP %d" % exc.code
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = "%s: %s" % (type(exc).__name__, exc)
        except ValueError:
            raise ApiError("Portal JSON yerine beklenmeyen bir yanit dondurdu (site degismis olabilir).",
                           "beklenmeyen_yanit", {"url": url})
        except ApiError:
            raise
        if attempt < RETRIES:
            time.sleep(0.8 * attempt)
    raise ApiError("Portala ulasilamadi (%d deneme). Son hata: %s. Portal bakimda/erisilemiyor olabilir."
                   % (RETRIES, last), "ag_hatasi", {"portal": cfg.get("base") or cfg.get("url")})


# ---------------------------------------------------------------- HTML ayristirma

class _TableScan(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self._d = 0
        self._label = None
        self._value = None
        self._pending = None
        self.pairs = []

    def handle_starttag(self, tag, attrs):
        if tag != "div":
            return
        self._d += 1
        cls = (dict(attrs).get("class") or "").split()
        if "divTableCellLabel" in cls:
            self._label = [self._d, []]
        elif "divTableContent" in cls and self._pending is not None and self._value is None:
            self._value = [self._d, []]

    def handle_endtag(self, tag):
        if tag != "div":
            return
        if self._label is not None and self._d == self._label[0]:
            self._pending = " ".join("".join(self._label[1]).split())
            self._label = None
        elif self._value is not None and self._d == self._value[0]:
            self.pairs.append((self._pending, " ".join("".join(self._value[1]).split())))
            self._pending = None
            self._value = None
        self._d -= 1

    def handle_data(self, data):
        if self._label is not None:
            self._label[1].append(data)
        if self._value is not None:
            self._value[1].append(data)


PLAN_ONEK = ("mer'i imar plani adi", "meri imar plani adi", "mer'i imar plani",
             "meri imar plani", "imar plani adi", "imar plani")

BOS = ("", "-", "--", "EMPTYROW")


def _sinifla(etiket):
    s = fold(etiket)
    if "k.a.k.s" in s or "emsal" in s:
        return "kaks"
    if "t.a.k.s" in s or s.replace(".", "").startswith("taks"):
        return "taks"
    if "kat adedi" in s or "kat say" in s:
        return "kat"
    if "yuksekl" in s:
        return "bina_yuk"
    if "nizam" in s:
        return "nizam"
    if "bahce" in s:
        if s.startswith("on"):
            return "on_bahce"
        if s.startswith("yan"):
            return "yan_bahce"
        if s.startswith("arka"):
            return "arka_bahce"
    if "bina derinlig" in s:
        return "bina_derinlik"
    if "kot alinacak" in s:
        return "kot"
    if "hesap alan" in s or "parsel alan" in s or "tapu alan" in s:
        return "alan"
    if "pafta" in s:
        return "pafta"
    if "ada/parsel" in s or "ada / parsel" in s:
        return "ada_parsel"
    if s == "ada":
        return "ada"
    if s == "parsel":
        return "parsel"
    if "kartezyen" in s:
        return "kartezyen"
    if "cografi" in s:
        return "cografi"
    if "projeksiyon" in s:
        return "projeksiyon"
    if "uyari" in s:
        return "uyari"
    if "fonksiyon" in s or "fonsiyon" in s:
        if "aciklama" in s:
            return "fonksiyon_aciklama"
        return "fonksiyon"
    if any(k in s for k in ("not", "aciklama", "kisitl", "kisitlama")):
        return "not"
    if "tapu kütügü" in s or "tapu kütüğü" in s or "tapu mahalle" in s:
        return "mahalle"
    if "idari mahalle" in s:
        return "mahalle_idari"
    if "ilçesi" in s or "ilce" in s:
        return "ilce"
    if "ili / ilcesi" in s:
        return "il_ilce"
    if "mahalle" in s:
        return "mahalle"
    if "tadilat" in s:
        return "tadilat"
    if "imar plan" in s or s.startswith("mer"):
        return "plan"
    return None


def _gecerli(v):
    return v is not None and str(v).strip() not in BOS


def _deger(raw, key, tercihler=()):
    items = raw.get(key) or []
    for w in tercihler:
        for lab, _orig, val in items:
            if w in lab and _gecerli(val):
                return str(val).strip()
    for lab, _orig, val in items:
        if _gecerli(val) and val != "EMPTYROW":
            return str(val).strip()
    return None


def parse_imar_html(html):
    """imar.aspx HTML -> {alan: [(etiket, deger)]}, plan notlari ayri listede."""
    scan = _TableScan()
    scan.feed(html)
    raw = {}
    for label, value in scan.pairs:
        key = _sinifla(label)
        if key is None:
            continue
        raw.setdefault(key, []).append((fold(label), label.strip(), value))
    return raw


def _emsal(deger):
    if deger is None:
        return None
    m = re.search(r"\(\s*(-?\d+(?:[.,]\d+)?)\s*\)", str(deger))
    if m:
        return tr_num(m.group(1))
    return tr_num(deger)


def normalize(raw, kimlik):
    ada, parsel = kimlik.get("ada"), kimlik.get("parsel")
    ada_parsel = _deger(raw, "ada_parsel")
    if ada_parsel:
        nums = re.findall(r"\d+", ada_parsel)
        if len(nums) >= 2:
            ada = ada or nums[0]
            parsel = parsel or nums[1]
    ada = ada or _deger(raw, "ada")
    parsel = parsel or _deger(raw, "parsel")

    def tem(ham):
        return None if not _gecerli(ham) else str(ham).strip()

    plan = None
    for w in ("mer'i imar plani adi", "meri imar plani adi", "mer'i imar plani",
              "meri imar plani", "imar plani"):
        plan = _deger(raw, "plan", (w,))
        if plan:
            break
    if not plan:
        plan = _deger(raw, "plan")

    fonk = tem(_deger(raw, "fonksiyon"))
    imar_yok_mesaj = None
    if fonk and re.search(r"mevcut degil|mevcut değil", fold(fonk)):
        imar_yok_mesaj = fonk
        fonk = None
    kullanim = fonk
    if kullanim:
        kullanim = re.sub(r"\s*\([^)]*\)", "", kullanim).strip() or None

    notlar = []
    for key in ("not", "fonksiyon_aciklama"):
        for _fold, orig, val in raw.get(key) or []:
            if _gecerli(val) and val not in notlar:
                notlar.append("%s: %s" % (orig.rstrip(":"), val))
    if imar_yok_mesaj:
        notlar.insert(0, imar_yok_mesaj)

    alan = tr_num(_deger(raw, "alan"))

    tm = {"dogu_y": None, "kuzey_x": None}
    kart = _deger(raw, "kartezyen") or ""
    m = re.search(r"SA\u011eA\s*\(Y\)\s*=\s*(-?\d+(?:[.,]\d+)?)\s*m?.*?YUKARI\s*\(X\)\s*=\s*(-?\d+(?:[.,]\d+)?)", kart)
    if m:
        tm = {"dogu_y": tr_num(m.group(1)), "kuzey_x": tr_num(m.group(2))}

    cografi = _deger(raw, "cografi")
    if cografi:
        cografi = re.split(r"\s+(Google Maps|Yandex Maps|OpenStreet Maps)", cografi)[0].strip() or None

    pafta = _deger(raw, "pafta")
    if pafta:
        pafta = re.sub(r"\s*(Harita|Parsel Kapı Listesi)\s*$", "", pafta).strip() or None

    ilce = _deger(raw, "ilce") or _deger(raw, "il_ilce")
    mahalle = _deger(raw, "mahalle") or _deger(raw, "mahalle_idari")

    return {
        "ilce": ilce or kimlik.get("ilce"),
        "mahalle": mahalle or kimlik.get("mahalle"),
        "ada": str(ada) if ada else None,
        "parsel": str(parsel) if parsel else None,
        "plan_adi": plan,
        "pafta": pafta,
        "emsal": _emsal(_deger(raw, "kaks")),
        "kaks": _emsal(_deger(raw, "kaks")),
        "taks": tr_num(_deger(raw, "taks")),
        "kat_sayisi": int(tr_num(_deger(raw, "kat"))) if tr_num(_deger(raw, "kat")) is not None else None,
        "yapi_nizami": tem(_deger(raw, "nizam")),
        "bina_yuksekligi_m": tr_num(_deger(raw, "bina_yuk")),
        "bina_derinligi_m": tr_num(_deger(raw, "bina_derinlik")),
        "cekme_mesafeleri": {
            "on": tr_num(_deger(raw, "on_bahce")),
            "yan": tr_num(_deger(raw, "yan_bahce")),
            "arka": tr_num(_deger(raw, "arka_bahce")),
        },
        "kot_alinacak_nokta": tem(_deger(raw, "kot")),
        "kullanim_karari": kullanim,
        "kullanim_karari_ham": fonk,
        "plan_notlari": notlar,
        "imar_yok": bool(imar_yok_mesaj),
        "hesap_alani_m2": alan,
        "olcek": tem(_deger(raw, "olcek")),
        "projeksiyon": tem(_deger(raw, "projeksiyon")),
        "koordinat_tm": tm,
        "koordinat_cografi": cografi,
    }


# ---------------------------------------------------------------- portal islemleri

def _base(cfg):
    base = cfg.get("base")
    if not base:
        raise ApiError("Bu il icin otomatik imar sorgusu kapali (%s)." % cfg.get("durum"),
                       cfg.get("durum") or "kapsam_disi", {"portal": cfg.get("url"), "not": cfg.get("not")})
    return base


def get_ilceler(il):
    ad, cfg = il_bilgi(il)
    base = _base(cfg)
    data = _fetch(base + "service/imarsvc.aspx?type=idariilce", cfg, "json", LIST_TTL)
    out = [{"id": x.get("OBJECTID"), "ad": x.get("ILCE_ADI")} for x in data if x.get("ILCE_ADI")]
    out.sort(key=lambda x: fold(x["ad"]))
    return ad, out


def get_mahalleler(il, ilce_id=None):
    ad, cfg = il_bilgi(il)
    base = _base(cfg)
    url = base + "service/imarsvc.aspx?type=mahalle"
    if ilce_id is not None:
        url += "&ilceid=%s" % ilce_id
    data = _fetch(url, cfg, "json", LIST_TTL)
    out = [{"id": x.get("OBJECTID"), "ad": x.get("ADI_NUMARASI")} for x in data if x.get("ADI_NUMARASI")]
    out.sort(key=lambda x: fold(x["ad"]))
    return ad, out


def get_tapu_mahalleler(il, ilce_id=None):
    ad, cfg = il_bilgi(il)
    base = _base(cfg)
    url = base + "service/imarsvc.aspx?type=tapuMahalle"
    if ilce_id is not None:
        url += "&ilceid=%s" % ilce_id
    data = _fetch(url, cfg, "json", LIST_TTL)
    out = [{"id": x.get("TAPU_MAH_ID"), "ad": x.get("TAPU_MAH_ADI")} for x in data if x.get("TAPU_MAH_ADI")]
    out.sort(key=lambda x: fold(x["ad"]))
    return ad, out


def parsel_adaylari(il, ada, parsel):
    _, cfg = il_bilgi(il)
    base = _base(cfg)
    ada, parsel = str(ada).strip(), str(parsel).strip()
    if not re.match(r"^\d+$", ada) or not re.match(r"^\d+$", parsel):
        raise ApiError("ada ve parsel sayisal olmali (or. ada=157, parsel=59).", "gecersiz_girdi")
    url = base + "service/imarsvc.aspx?type=adaparsel&adaparsel=%s%%2F%s" % (ada, parsel)
    data = _fetch(url, cfg, "json", CAND_TTL)
    adaylar = []
    for item in data if isinstance(data, list) else []:
        if (item.get("ADAPARSEL") or "").strip() not in ("", "%s/%s" % (ada, parsel)):
            continue
        adaylar.append({"tapu_mahalle": item.get("TAPU_MAH_ADI"),
                        "parsel_id": item.get("OBJECTID"),
                        "ada": item.get("ADA") or ada})
    if not adaylar:
        raise ApiError("%s e-Imar'da %s/%s bulunamadi. Ada/parsel numarasini kontrol edin; parsel "
                       "belediye siniri disinda olabilir." % (il, ada, parsel), "bulunamadi",
                       {"il": il, "ada": ada, "parsel": parsel})
    return adaylar


def _sec(adaylar, mahalle):
    if mahalle:
        hedef = fold(mahalle)
        es = [a for a in adaylar if a.get("tapu_mahalle") and fold(a["tapu_mahalle"]) == hedef]
        if not es:
            es = [a for a in adaylar if a.get("tapu_mahalle") and hedef in fold(a["tapu_mahalle"])]
        if es:
            return es[0]
        if len(adaylar) == 1:
            return adaylar[0]
        adlar = sorted({a["tapu_mahalle"] for a in adaylar if a.get("tapu_mahalle")})
        raise ApiError("'%s' ile eslesen kayit yok. Bu ada/parsel su mahallelerde: %s."
                       % (mahalle, ", ".join(adlar)), "belirsiz", {"aday_mahalleler": adlar})
    if len(adaylar) > 1:
        adlar = sorted({a["tapu_mahalle"] for a in adaylar if a.get("tapu_mahalle")})
        if len(adlar) > 1:
            raise ApiError("Bu ada/parsel %d mahallede var (%s). mahalle verin."
                           % (len(adaylar), ", ".join(adlar)), "belirsiz", {"aday_mahalleler": adlar})
    return adaylar[0]


def _ozel_notlar(il, parsel_id, cfg):
    base = _base(cfg)
    try:
        data = _fetch(base + "service/imarsvc.aspx?type=ozelnot&parselid=%s" % parsel_id, cfg, "json", CAND_TTL)
    except ApiError:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        for key in ("SINIR_PLANNOTU", "PLANNOTU", "NOT"):
            v = item.get(key)
            if v and str(v).strip() not in ("", "#"):
                out.append(str(v).strip())
    return out


def imar_durumu(il, parsel_id, tkgm_mahalle=None):
    ad, cfg = il_bilgi(il)
    base = _base(cfg)
    url = base + "imar.aspx?parselid=%s" % parsel_id
    cached = _cache_get(url, IMAR_TTL)
    if cached is not None:
        return cached
    html = _fetch(url, cfg, "html")
    if "imar durumu mevcut degil" in fold(html) or "error=tableback" in html.lower():
        raise ApiError("Bu parsel icin imar durumu kaydi yok (portal 'Imar Durumu Mevcut Degil' dondurdu).",
                       "imar_yok", {"portal": url})
    raw = parse_imar_html(html)
    toplam = sum(len(v) for v in raw.values())
    if not toplam:
        raise ApiError("Imar sayfasi okunamadi (beklenen tablo bulunamadi). Site degismis olabilir.",
                       "beklenmeyen_yanit", {"portal": url, "boyut": len(html)})

    kayit = normalize(raw, {"parsel_id": parsel_id, "mahalle": tkgm_mahalle, "ilce": None})
    kayit["il"] = ad
    kayit["parsel_id"] = parsel_id
    kayit["durum"] = "ok"
    kayit["kaynak"] = {"portal": base,
                       "sorgu_url": url,
                       "ham_dosya": _save_raw("imar_%s_%s.html" % (parsel_id, time.strftime("%Y%m%d-%H%M%S")), html)}
    kayit["sorgu_zamani"] = now_tr()
    _cache_put(url, kayit)
    return kayit


def imar_sorgula(il, ilce=None, mahalle=None, ada=None, parsel=None):
    ad, cfg = il_bilgi(il)
    if cfg.get("durum") != "aktif":
        raise ApiError("%s icin otomatik imar sorgusu kapali: %s" % (ad, cfg.get("durum")),
                       cfg.get("durum"), {"portal": cfg.get("url"), "not": cfg.get("not")})
    if ada in (None, "") or parsel in (None, ""):
        raise ApiError("ada ve parsel zorunlu.", "gecersiz_girdi")

    adaylar = parsel_adaylari(ad, ada, parsel)
    aday = _sec(adaylar, mahalle)
    kayit = imar_durumu(ad, aday["parsel_id"], aday.get("tapu_mahalle"))
    kayit["mahalle"] = aday.get("tapu_mahalle") or kayit.get("mahalle")
    kayit["ada"] = str(ada).strip()
    kayit["parsel"] = str(parsel).strip()
    kayit["aday_sayisi"] = len(adaylar)
    kayit["mahalle_esleme"] = {"tkgm": mahalle, "keos": aday.get("tapu_mahalle")}
    if kayit.get("kaynak"):
        kayit["kaynak"]["portal"] = cfg.get("base")
        kayit["kaynak"]["sorgu_url"] = cfg["base"] + "imar.aspx?parselid=%s" % aday["parsel_id"]
    kayit["sorgu_zamani"] = now_tr()
    return kayit


def imar_link(il, ilce=None):
    ad, cfg = il_bilgi(il)
    if cfg.get("durum") == "aktif":
        return {"il": ad, "durum": "aktif", "portal": cfg["base"], "otomatik_sorgu": True,
                "tls": cfg.get("tls"), "not": "Ada/parsel ile imar_sorgula kullanilabilir."}
    return {"il": ad, "durum": cfg.get("durum"), "portal": cfg.get("url"),
            "otomatik_sorgu": False, "not": cfg.get("not")}
