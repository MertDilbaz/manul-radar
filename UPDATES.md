# UPDATES — Manul Radar

Bu dosya session bazlı özet tutar. Her büyük değişiklik turundan sonra güncellenir.

---

## 2026-06-29 — Production runner crash: `source_name` caller/callee mismatch

**Sorun:** Mert GH Actions'ta `workflow_dispatch` ile bir run tetikledi ve aşağıdaki hatayı aldı:

```
File "/home/runner/work/manul-radar/manul-radar/run_monitor.py", line 423, in _build_sources_from_config
    SuccessFactorsSource(
TypeError: SuccessFactorsSource.__init__() got an unexpected keyword argument 'source_name'
Error: Process completed with exit code 1.
```

**Kök neden:** `run_monitor._build_sources_from_config` dispatch helper'ı `SuccessFactorsSource`'a `source_name=name or None` geçiriyordu ama `SuccessFactorsSource.__init__` bu parametreyi kabul etmiyordu. Aynı tutarsızlık **5 parser** için geçerliydi:

- ❌ `HrPeakSource` — `source_name` kabul etmiyor
- ❌ `SuccessFactorsSource` — `source_name` kabul etmiyor (GH Actions hatası bu)
- ❌ `PeopliseSource` — `source_name` kabul etmiyor
- ❌ `HirexSource` — `source_name` kabul etmiyor
- ❌ `ZohoRecruitSource` — `source_name` kabul etmiyor
- ✅ `WorkableSource` / `GreenhouseSource` / `LeverSource` / `SmartRecruitersSource` / `TeamtailorSource` / `KariyerNetSource` — kabul ediyor

Neden smoke testler bunu yakalamadı? `tests/smoke_run_monitor.py` ve `tests/smoke_ats_sources.py` parser constructor'larını `mock.patch.object(SourceClass, "fetch_jobs", ...)` ile **monkeypatch'liyordu** — gerçek `__init__` çağrılmıyordu, gerçek kwarg seti test edilmiyordu. Tutarsızlık commit'ten production'a kadar görünmeden geçti.

**Çözüm — 3 katmanlı fix:**

1. **5 parser'a `source_name` parametresi eklendi** (geriye dönük uyumlu, `None` default'lu):
   - `app/sources/hrpeak_source.py`: `self.name = source_name or self._derive_source_name(company_name)`
   - `app/sources/successfactors_source.py`: `self.name = source_name or self._build_source_name(company_name)`
   - `app/sources/peoplise_source.py`: `self.name = source_name or f"peoplise_{self.account}"`
   - `app/sources/hirex_source.py`: `self.name = source_name or f"hirex_{self.slug}"`
   - `app/sources/zoho_recruit_source.py`: `self.name = source_name or ...` (early return ile branch temiz)

2. **`run_monitor.py` `_build_sources_from_config`** — `HrPeakSource` satırı da artık `source_name=name or None` geçiriyor (tutarlı dispatch).

3. **2 yeni savunma katmanı eklendi:**
   - `tests/smoke_source_name_contract.py` — 11 parser × 2 test = **22 assertion**: her parser `source_name` kabul ediyor mu + default name non-empty mi? Mock yok, gerçek `__init__` çağrılıyor.
   - `scripts/check_sources_construct.py` — lokal'de gerçek `config.sources` + `companies.yaml` ile dispatch helper'ı çalıştırıyor, 12 source'un hepsi kuruluyor mu? Network'suz, sadece constructor kurma.

**Doğrulama:**

- `python tests/smoke_source_name_contract.py` → `ALL_SOURCE_NAME_CONTRACT_OK` (22 OK)
- `python scripts/check_sources_construct.py` → `SOURCES_CONSTRUCT_OK count=12`
- Tüm diğer smoke testler (smoke_ats_sources, smoke_run_monitor_guards, smoke_run_monitor, smoke_job_scorer_policy, smoke_telegram_notifier, smoke_scoring_v2) **regresyon yok** (toplam **110 OK / 0 FAIL**).

**Ders (memory'ye kaydedildi):** Dispatch helper her parser'a aynı kwarg geciriyorsa, o kwarg'i kabul etmeyen parser'lara da ekle (tutarli API). Smoke testlerin constructor'ları mock'lamasi, gercek kwarg setini gizler; bir "constructor contract" smoke test ekle. Mock'lu birim testleri yeterli degil — gercek config ile bir dispatch helper sanity check de yap.

**Sonraki adım:** Production'da bir kez daha `workflow_dispatch` run tetikle. Şimdi düzgün çalışmalı — 12 source kurulacak, yeni V2 scoring junior+java+SQL'i **🟢 yüksek**, generic SE / AI SE / iOS-Android'i **🔴 düşük** etiketiyle gönderecek.

---

## 2026-06-29 — Scoring V2: tiered weights + confidence + exclusive buckets

**Sorun:** Mert production run'da 5 ilan "uygun" olarak Telegram'a geldi:

- AI Software Engineer — Commencis — score 40
- Software Engineer — Midas — score 40
- Software Engineer, iOS — Midas — score 40
- AI Software Engineer - Remote — Insider — score 40
- Software Engineer — iyzico — score 40

Generic "Software Engineer" + Türkiye filtresi 40 puan alıp geçiyordu. Junior+Java+SQL ile Application Support+SQL hiçbir farkı yoktu — hepsi aynı sepette. iOS/Mobile ilanları da bu sepete giriyordu. AI Software Engineer özel olarak değerlendirilmiyordu.

Ek olarak Telegram sayaçları overlap yapıyordu: 196 ilan taranırken, "elenen" sayaçları (location + domain + experience + ...) toplamı 196'yı aşıyordu — çünkü bir ilan birden fazla sebeple elenebiliyor ve her birine ayrı sayaç artıyordu.

**Çözüm — 4 katmanlı V2:**

1. **Tiered keyword weights** (`JobScorer` + `config.yaml`):
   - `strong_weight = 25`: java, spring boot, backend, sql, application support, integration, junior, new grad, intern vb.
   - `weak_weight = 8`: software engineer, software developer, yazılım mühendisi, ai software engineer vb.
   - `location_weight = 10`: turkey, istanbul, ankara, izmir, manisa, remote türkiye vb.
   - `company_boost_weight = 10`: commencis, midas, insider, iyzico vb. (high-priority şirketler)
   - `include_keywords`'ta olup `weak_keywords`'ta da olanlar **weak** olarak değerlendirilir; geri kalanlar **strong**.

2. **Penalty'ler**:
   - `mobile_penalty = 25`: iOS, Android, React Native, Flutter vb. varsa VE strong backend/java/support sinyali yoksa uygulanır. Hard reject değil, skor eşiğin altına iter (high-priority şirket + mobile varsa düşük güvenle gösterilebilir).
   - `generic_only_penalty = 25`: sadece weak sinyalleri varsa (ör. sadece "Software Engineer") ve başka stack/junior/destek sinyali yoksa uygulanır.

3. **Confidence tier** (`ScoredJob.confidence`): Her relevant ilan için **high / medium / low** etiketi + `confidence_reasons` listesi atanır:
   - `high`: score >= 80 + en az 1 strong sinyal + junior veya support sinyali varsa.
   - `medium`: score >= 60 + strong sinyal varsa (junior olmasa bile).
   - `low`: score >= 40 + sadece weak/generic sinyaller varsa.
   - `low_confidence_generic_software` gibi reason'lar confidence_reasons'a eklenir.

4. **Exclusive reject buckets** (`JobMonitorStats`): Her rejected job artık **tek bir bucket**'a düşer (priority: location > domain > experience > hard > non_target > mobile > role > generic_only > score). Per-bucket sayaç toplamı artık `rejected_total`'a eşit — Telegram summary artık mantıklı.

**Yeni `minimum_score = 60`** (eskiden 40). Senior / 5+ yıl / non-Türkiye hâlâ hard reject.

**Telegram formatı** her job kartında artık şu var:

```
1) AI Software Engineer
🏢 Commencis
📍 Istanbul, Turkey | Çalışma: Hybrid
⭐ Skor: 83 | Güven: 🔴 düşük
✅ Eşleşenler: istanbul, turkey, software engineer, commencis
💡 Neden: sadece genel başlık sinyali
🔗 İlanı Aç
```

Summary bölümünde:
- 🎯 Güven dağılımı: 🟢 yüksek X, 🟡 orta Y, 🔴 düşük Z
- 🚫 Elenen toplam: N (toplam = per-bucket toplamı)
- Bucket'lar: lokasyon, no_domain, experience+hard, non_target, mobile, role, generic_only, score

**Değişen dosyalar:**

- `app/models/scored_job.py` — `confidence: str` + `confidence_reasons: list[str]` alanları.
- `app/filters/job_scorer.py` — V2 tiered weights + penalties + confidence tier. Geriye dönük uyumlu: eski parametreler (`include_weight` vs) aynen çalışır, yeni parametreler opsiyonel.
- `app/services/job_monitor_service.py` — `_count_rejection` artık **exclusive**; her job tek bucket. Yeni alanlar: `rejected_mobile`, `rejected_generic_only`. Eski `_has_prefix`/`_has_any` helper'ları kaldırıldı (private'dı).
- `app/notifier/telegram_notifier.py` — Job kartında `Güven: 🟢/🟡/🔴 <label>` + `Neden: <reasons>`. Summary'de `Güven dağılımı` satırı + bucket'lar "filtre nedenleri" olarak yeniden adlandırıldı.
- `app/config/config.yaml` — `minimum_score: 60`, `weak_keywords`, `company_boost_keywords`, `mobile_negative_keywords` listeleri + V2 tiered weight ayarları.
- `run_monitor.py` + `main.py` — `_build_scorer` yeni V2 parametreleri okuyacak şekilde güncellendi.
- `tests/smoke_run_monitor.py` — `_run_monitor_in_temp_env` helper'ı `MANUL_ENABLE_TELEGRAM_SEND` env var'ı ekleyecek şekilde güncellendi. `_check_use_dummy_source_routes_to_dummy` + `_check_use_dummy_source_dedup_on_second_run` testleri dummy mode'un Telegram skip ettiğini (yeni guard semantiği) DB satır sayısı üzerinden doğruluyor.
- `tests/smoke_scoring_v2.py` — yeni (17 OK): junior java high, SQL support high, generic SE low/reject, company boost delta, iOS penalty (mobile + balanced), AI engineer low, senior hard reject, 5+ years reject, non-Turkey reject, **exclusive buckets sum + each-bucket-one**.
- `scripts/simulate_v2_real_jobs.py` — yeni, Mert'in gerçek run'ındaki 7 ilanı simüle eden dry-run aracı. **Junior Java Backend Developer → high (233), Application Support Specialist (SQL) → high (165), generic SE/AI SE → low (73-83).**

**Doğrulama (tüm smoke testler):**

- `python tests/smoke_job_scorer_policy.py` → `ALL_SCORER_POLICY_OK` (9 OK)
- `python tests/smoke_telegram_notifier.py` → `ALL_TELEGRAM_OK` (10 OK)
- `python tests/smoke_run_monitor_guards.py` → `RUN_MONITOR_GUARDS_OK` (24 OK)
- `python tests/smoke_ats_sources.py` → `ATS_SOURCES_SMOKE_OK` (19 OK)
- `python tests/smoke_run_monitor.py` → `ALL_RUN_MONITOR_OK` (9 OK)
- `python tests/smoke_scoring_v2.py` → `ALL_SCORING_V2_OK` (17 OK)
- `python scripts/simulate_v2_real_jobs.py` → real production jobs ile V2 davranışı doğrulandı

**Toplam: 88 OK / 0 FAIL.** Mevcut Telegram guard + source parser davranışı bozulmadı.

**Sonraki adım:** Production'da bir workflow_dispatch run daha tetikle, Mert yeni Telegram çıktısını görsün. Junior Java + Application Support **high 🟢**, generic SE / AI SE / iOS-Android **low 🔴** etiketiyle gelecek. Onaylarsa Sprint 2'ye (yeni source/parser ekleme) geçeriz.

---

## 2026-06-29 — Production Telegram safety guards

**Sorun:** Mert GH Actions'ta `python run_monitor.py` çalıştırdığında 13:03, 13:06, 13:18 saatlerinde (cron + workflow_dispatch + bir tuhaf gecikme) bildirim almış. Zamanlama şüphesi + dummy-looking içerik → "production'da dummy source mu kullanılıyor?" endişesi.

**Kök neden:** Workflow dosyası düzgündü (`python run_monitor.py`, dummy flag yok). Saat farkı GH Actions cron gecikmesi + manuel `workflow_dispatch` tetiklemeleri. Dummy-looking içerik ise aslında Kafein'den gelen gerçek iş ilanıydı (filter'lardan geçmiş tek ilan). AMA mevcut kodda **defense-in-depth eksikti**:

- `--use-dummy-source` mode'da Telegram gönderimi koşulsuz açıktı (birisi yanlışlıkla flag'i unutsa bile dummy data Telegram'a gidebilirdi).
- Telegram gönderimi için `MANUL_ENABLE_TELEGRAM_SEND` opt-in guard yoktu → CI'da secret set edilirse koşulsuz gönderiyordu.
- Boş source list durumunda `SystemExit(1)` fırlatılıyordu ama mesaj çok genel ("No usable sources").
- Runner kaç source yüklediğini loglamıyordu → CI log'unda görünmezdi.

**Çözüm — 4 katmanlı guard:**

1. **Boş source → RuntimeError** (`run_monitor._build_sources_from_config`): `SystemExit(1)` yerine açık mesajlı `RuntimeError("No enabled job sources configured. Refusing to run dummy source in production.")`.
2. **Dummy mode → Telegram yasak**: `main()` içinde Telegram gönderim dalına `and not args.use_dummy_source` koşulu eklendi. Dummy mode'da `WARNING Telegram delivery SKIPPED: dummy source mode is active. Dummy data must never leave the process.` loglanır.
3. **`MANUL_ENABLE_TELEGRAM_SEND` env guard**: Truthy değer (`1`/`true`/`yes`/`on`, case-insensitive) gerekli. CI workflow'u `MANUL_ENABLE_TELEGRAM_SEND: "true"` set eder; local checkout / başka CI job'larında unset ise `WARNING Telegram delivery SKIPPED: MANUL_ENABLE_TELEGRAM_SEND is not truthy...` loglanır.
4. **Source log**: `main()` başlangıcında `Loaded N enabled sources:` + her source için `  - parser / label` satırları.

**Değişen dosyalar:**

- `run_monitor.py` — guard'lar + loglar + docstring güncellemesi.
- `.github/workflows/job-monitor.yml` — `MANUL_ENABLE_TELEGRAM_SEND: "true"` env'i eklendi.
- `tests/smoke_run_monitor_guards.py` — yeni smoke test (22 assertion):
  - Guard truthy/falsy/unset case'leri.
  - Boş source → RuntimeError.
  - `--use-dummy-source` → Telegram çağrılmaz (sent=0 + log kontrolü).
  - `MANUL_ENABLE_TELEGRAM_SEND` unset → Telegram çağrılmaz.
  - Production run → `Loaded N enabled sources:` loglanır.

**Doğrulama:**

- ATS smoke: `python tests/smoke_ats_sources.py` → `ATS_SOURCES_SMOKE_OK` (regresyon yok).
- Guards smoke: `python tests/smoke_run_monitor_guards.py` → `RUN_MONITOR_GUARDS_OK` (22/22 OK).
- Real production run (`python run_monitor.py`): `Loaded 12 enabled sources:` loglandı, 1 yeni relevant job bulundu, guard kapalı olduğu için Telegram SKIPPED.
- Dummy run (`python run_monitor.py --use-dummy-source`): `DUMMY SOURCE MODE ACTIVE` warning, `Loaded 1 enabled source: dummy`, Telegram SKIPPED.

**Öğrenilen ders:** Production runner'da "her şey açık" varsayımı yanlış. CI'da bile `dummy` veya `local test` senaryosu sızarsa gerçek kullanıcıya notification gidebilir. **Opt-in guard + yapısal fail-fast** her zaman daha güvenli. Aynı pattern gelecekteki tüm `run_*` scriptleri için default olmalı.