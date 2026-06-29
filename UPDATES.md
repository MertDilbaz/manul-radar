# UPDATES — Manul Radar

Bu dosya session bazlı özet tutar. Her büyük değişiklik turundan sonra güncellenir.

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