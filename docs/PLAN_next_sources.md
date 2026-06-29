# Plan: Yeni Kaynakların Entegrasyonu (2026-06-29)

## Amaç

`manul_sentinel_kaynak_listesi.md` içindeki 50+ kariyer sayfası / ATS /
program kaynağını Manul Sentinel'e eklemek. Listenin tamamı tek sprint'te
yapılamayacak kadar büyük; üç sprint'e bölüyoruz.

## Kapsam Dışı (Bilerek)

- LinkedIn, Kariyer.net, oyun firmaları, genel ilan siteleri.
- Şu an `kariyer_net` parser'ı zaten kodda var ama `enabled: false`.
  Listede de yok, dokunmuyoruz.

## Mimari Kararlar

1. **Tek parser = tek ATS ailesi.** Her yeni ATS ailesi için yeni
   `app/sources/<name>_source.py` modülü, `BaseSource` extend eder.
2. **`companies.yaml` tek ekleme noktası.** Yeni şirket config-only ise
   `companies.yaml`'a entry eklemek yeter; `config.yaml`'daki `sources:`
   listesi Sprint 2+'da kullanılmaya devam edilebilir ama Sprint 1'de
   yenisi eklenmeyecek (geriye uyumluluk için tutulur).
3. **Yeni parser = yeni smoke test.** `tests/smoke_ats_sources.py`'ye
   network-mock'lu test eklenir; her parser için en az count + title/url
   doğrulaması.
4. **Turkey-only kuralı.** Listedeki global/Turkey-dışı board'lar
   `enabled: false` + `disabled_reason` ile işaretlenir (mevcut kalıp).
5. **Program radarı (Sprint 3) ayrı mimari gerektirir.** `Job` modeli
   program sayfalarına uymuyor; yeni `Program` modeli + keyword radar
   abstraction Sprint 3'te tasarlanır.

## Sprint 1 — Hızlı ATS Kazanımları (UYGULA)

Hedef: Mevcut ATS parser'larına config ekleyerek ve 3 yeni kolay-parser
yazarak kapsamı genişletmek. Hepsi Turkey-focused.

### 1.1 Config-only Eklemeler (Sıfır Kod)

| Şirket | Parser | Nereden | Eklenecek config |
|---|---|---|---|
| iyzico | `lever` | `https://jobs.lever.co/iyzico` | `companies.yaml`'a `lever` entry |
| Ziraat Teknoloji | `hrpeak` | `https://ziraatteknoloji.hrpeak.com/jobs` | `companies.yaml`'a `hrpeak` entry |
| İnnova | `hrpeak` | `https://innova.hrpeak.com/jobs` | `companies.yaml`'a `hrpeak` entry |

Mevcut parser'lar generic (`HrPeakSource` zaten `careers_url` parametreli,
`LeverSource` zaten `company_slug` parametreli). Sadece config.

Not: HrPeak listing path'i `kafein.hrpeak.com/ilan/site.aspx` formatında
ama yeni adresler `/jobs`. Önce HrPeak source'un bu path'i kabul edip
etmediğini doğrulamak gerekiyor; değilse `_JOB_LINK_TOKENS` listesine
`/jobs` eklemek küçük bir refactor olabilir.

### 1.2 Yeni Parser Modülleri

#### `peoplise_source.py` — Logo Yazılım

- ATS: `live.peoplise.com/logo/career`
- Yapı: HTML listing; her iş için anchor, title, location.
- Pattern: `WorkableSource` benzeri (HTML parse + anchor extraction).
- Smoke test: HTML fixture ile 1 ilan parse, count + URL doğrula.

#### `hirex_source.py` — Papara

- ATS: `app.gethirex.com/o/papara/`
- Yapı: HTML listing + inline JSON (`__NEXT_DATA__` veya benzeri).
- Pattern: Önce HTML anchor taraması, boşsa inline JSON regex.
- Smoke test: HTML fixture, count + URL doğrula.

#### `zoho_recruit_source.py` — Param

- ATS: `param.zohorecruit.com/jobs/PARAM-Kariyer`
- Yapı: Zoho Recruit public JSON API (genelde
  `Recruit.asmx/JobOpenings` veya REST endpoint).
- Pattern: `LeverSource` benzeri (JSON GET + parse). Endpoint'i
  keşfetmek için ilk implementasyonda `probe_param_zoho.py` script'i
  yazılır.
- Smoke test: JSON fixture, count + title doğrula.

### 1.3 `run_monitor.py` Dispatch Güncellemesi

`_build_sources_from_config()` içine 3 yeni `parser ==` branch'i:

```python
elif parser == "peoplise":
    if not company or not url:
        raise ValueError("peoplise requires company and url")
    built.append(PeopliseSource(company_name=company, careers_url=url, source_name=name or None))

elif parser == "hirex":
    if not company or not url:
        raise ValueError("hirex requires company and url")
    built.append(HirexSource(company_name=company, careers_url=url, source_name=name or None))

elif parser == "zoho_recruit":
    if not company or not url:
        raise ValueError("zoho_recruit requires company and url")
    built.append(ZohoRecruitSource(company_name=company, careers_url=url, source_name=name or None))
```

Modül-level import'lar da eklenir. "Supported: …" uyarı mesajı güncellenir.

### 1.4 `companies.yaml` Yeni Entry'leri

```yaml
- parser: lever
  name: lever_iyzico
  company: iyzico
  company_slug: iyzico
  enabled: true

- parser: hrpeak
  name: hrpeak_ziraat_teknoloji
  company: Ziraat Teknoloji
  url: https://ziraatteknoloji.hrpeak.com/jobs
  enabled: true

- parser: hrpeak
  name: hrpeak_innova
  company: İnnova
  url: https://innova.hrpeak.com/jobs
  enabled: true

- parser: peoplise
  name: peoplise_logo
  company: Logo Yazılım
  url: https://live.peoplise.com/logo/career
  enabled: true

- parser: hirex
  name: hirex_papara
  company: Papara
  url: https://app.gethirex.com/o/papara/
  enabled: true

- parser: zoho_recruit
  name: zoho_recruit_param
  company: Param
  url: https://param.zohorecruit.com/jobs/PARAM-Kariyer
  enabled: true
```

### 1.5 Smoke Test Güncellemesi

`tests/smoke_ats_sources.py`'ye 3 yeni `test_peoplise / test_hirex /
test_zoho_recruit` fonksiyonu eklenir. Mevcut kalıp: HTML/JSON fixture
ile `mock.patch("requests.get", ...)`.

### 1.6 Döküman Güncellemesi

- `docs/SOURCES.md`: Yeni ATS aileleri (`peoplise`, `hirex`,
  `zoho_recruit`) "Active source families" listesine eklenir; her
  biri için config-entry örneği gösterilir.
- `README.md`: "Supported Source Types" bölümü güncellenir.

### 1.7 Doğrulama Kabul Barları

- Tüm smoke testler pass: `python tests/smoke_ats_sources.py`
- `python tests/smoke_run_monitor.py` pass (dummy-source ile).
- `python run_monitor.py --use-dummy-source` 0 hata ile çalışır.
- Yeni parser'lardan en az 1 canlı fetch denenirse (rate-limit'e
  dikkat); yoksa config disabled bırakılır.

### 1.8 Canlı Fetch Sonuçları (2026-06-29)

`scripts/probe_new_sources.py` ile canlı probe yapıldı:

| Kaynak | Canlı sonuç | Karar |
|---|---|---|
| iyzico (Lever) | 8 job | enabled (✅) |
| Ziraat Teknoloji (HRPeak) | empty marker | disabled (❌) — sayfa "yayınlanmış bir açık pozisyon bulunamadı" döndü |
| İnnova (HRPeak) | 10 job | enabled (✅) |
| Logo Yazılım (Peoplise) | 6 job | enabled (✅) |
| Papara (Hirex) | 0 job | enabled (✅) — Mert kararı: "0 job hata değil" |
| Param (Zoho Recruit) | 0 job | zaten disabled — auth-gated API |

### 1.9 Sprint 1 Tamamlandı (2026-06-29)

Yapılan değişiklikler:

- `app/sources/hrpeak_source.py` — `_LISTING_URL_SUFFIXES`'a `/jobs`
  eklendi (yeni HRPeak tenantları için).
- `app/sources/peoplise_source.py` — yeni parser.
- `app/sources/hirex_source.py` — yeni parser.
- `app/sources/zoho_recruit_source.py` — yeni parser (best-effort).
- `run_monitor.py` — 3 yeni `parser ==` branch'i (`peoplise`, `hirex`,
  `zoho_recruit`).
- `app/config/companies.yaml` — 5 yeni entry (iyzico, Ziraat, İnnova,
  Logo, Papara, Param).
- `tests/smoke_ats_sources.py` — 3 yeni test fonksiyonu.
- `docs/SOURCES.md` — yeni ATS aileleri listelendi, config örnekleri.
- `README.md` — "Supported Source Types" güncellendi.
- `scripts/probe_new_sources.py` — canlı fetch probe script'i.
- `scripts/probe_param_zoho.py` — Zoho endpoint probe.
- `scripts/probe_peoplise_hirex.py` — HTML yapısı probe.

### 1.10 Bulunan Bug + Düzeltme

Peoplise parser'ı absolute URL href'lerini reddediyordu (`^/...` regex
relative path bekliyordu). `_looks_like_landing_url` fonksiyonu
`urlparse(href).path` kullanacak şekilde düzeltildi.

## Sprint 2 — Resmi Kariyer Sayfaları (DEFER)

Liste:
- Yapı Kredi Teknoloji (ykteknoloji.com.tr/kariyer)
- Koç Kariyerim Yapı Kredi Teknoloji (kockariyerim.com/companies/...)
- Garanti BBVA Teknoloji (kariyer.garantibbva.com.tr/...)
- Akbank (kariyer.akbank.com/shared/advert-list) — JavaScript-rendered
  olabilir, probe gerekir
- Softtech, Intertech, IBTech, AktifTech, Fibabanka
- Midas (zaten Lever'de, ama resmi site de takip edilebilir)
- Colendi, PayTR, PayTR Kariyer
- QNB Türkiye (qnbkariyer.com/ilanlar/)
- TEB, TEB Arf, AktifTech
- Etiya, Kafein (zaten HRPeak), Mikrogrup, İnnova (zaten HRPeak)
- Commencis (zaten Lever), Insider (zaten Lever)
- Akinon (ApplyToJob ATS)
- NTT DATA (global careers)
- Sanction Scanner (zaten Workable)
- OREDATA (zaten Workable)
- EPAM Türkiye

Riskler:
- Çoğu JS-rendered (özellikle Akbank advert-list, Garanti BBVA),
  Playwright/Selenium gerektirebilir — toaster-friendly hedefiyle
  çelişir.
- Her biri için ayrı HTML probe + parser; coverage patlaması.
- "Resmi kariyer sayfası ilanı göstermeyip dış portala yönlendirebilir"
  (yorumun kendin söylediği).

Planlama ayrı turda yapılacak; her sayfa için probe sonucu ve ATS-pattern
kararı (custom HTML parser vs JS headless vs dış ATS redirect'i takip).

## Sprint 3 — Program Radarı (DEFER, MİMARİ DEĞİŞİKLİK)

Liste:
- EPAM Campus, EPAM A!Tech Bootcamp
- Youthall Jobs, Youthall Talent Programs
- Coderspace Events, Hiring Challenge, Bootcamp
- Patika.dev Bootcamp, Skillcamp
- Koç Kariyerim, Koç Genç Yetenek

Mimari gereksinim:
- Mevcut `Job` modeli program sayfalarına uymuyor (program title +
  başvuru linki + keyword relevance, ilan gibi değil).
- Yeni `Program` dataclass + `BaseSource` analog'u
  (`BaseProgramSource`?) + keyword-based radar pipeline.
- ARCHITECTURE.md'de "Future Expansion" altında zaten sinyali var.

Sprint 3 başlamadan önce ayrı mimari brief gerekli.

## Açık Sorular

- HrPeak yeni `/jobs` path'i mevcut parser ile çalışıyor mu? Yoksa
  `_JOB_LINK_TOKENS`'a ekleme gerekiyor mu? → Sprint 1.1 başlarken
  keşfedilecek; gerekirse 1 satır config değişikliği.
- Zoho Recruit public endpoint'i ne? → İlk implementasyonda
  `probe_param_zoho.py` ile keşfedilecek.
- "Real network fetch" Sprint 1 sonunda zorunlu mu, yoksa
  config-disabled + smoke-test-only yeterli mi? → Ben önerim:
  Sprint 1 sonunda 1 canlı fetch dene, başarısız olursa config'i
  disabled bırakıp Sprint 2'ye bırak. Ama bu kullanıcı kararı.

## Sonraki Adım

Kullanıcı onayı gelince Sprint 1 uygulamasına başla.