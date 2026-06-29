# Manul Sentinel Sources

Current job-search scope is intentionally limited to job postings only.
Weather, tech news, Steam, and HTML reports are out of scope for this phase.

## Active source families

- `hrpeak` — Turkish HRPeak career pages.
- `successfactors` — SAP SuccessFactors / RMK public listing pages.
- `workable` — public `apply.workable.com/<account>` boards.
- `greenhouse` — public Greenhouse job board API.
- `lever` — public Lever postings API.
- `smartrecruiters` — SmartRecruiters public postings API first, public page fallback.
- `teamtailor` — public Teamtailor career pages.
- `peoplise` — public Peoplise career pages (e.g. `live.peoplise.com/<account>/career`).
  Only anchors pointing at `/application/landing/<uuid>` count as job links.
- `hirex` — public Hirex pages (e.g. `app.gethirex.com/o/<slug>/`). HTML anchor
  scan first, inline JSON hydration blobs as fallback. Empty pages and
  auth-gated tenants return `[]` quietly — not an error.
- `zoho_recruit` — Zoho Recruit public career pages. Best-effort HTML parser;
  many tenants hydrate from authenticated APIs and yield no parseable
  job anchors. Registered in `companies.yaml` as `enabled: false` until
  a verified tenant is available.

`kariyer_net` is still present in `config.yaml`, but it is disabled by default because the site often returns `403 Forbidden` to scripted runs. Re-enable it only when you explicitly want to test Kariyer.net again.

## Where to add companies

Add company career boards to:

```text
app/config/companies.yaml
```

Keep runtime/scoring settings in:

```text
app/config/config.yaml
```

## Example company entry shapes

```yaml
companies:
  - parser: workable
    company: Rapsodo
    account: rapsodo
    enabled: true

  - parser: greenhouse
    company: Duolingo
    board_token: duolingo
    enabled: true

  - parser: lever
    company: Midas
    company_slug: getmidas
    enabled: true

  - parser: smartrecruiters
    company: Experian
    company_slug: Experian
    enabled: true

  - parser: teamtailor
    company: Montel
    url: https://montel.teamtailor.com/jobs
    enabled: true

  - parser: peoplise
    company: Logo Yazılım
    url: https://live.peoplise.com/logo/career
    enabled: true

  - parser: hirex
    company: Papara
    url: https://app.gethirex.com/o/papara/
    enabled: true

  - parser: zoho_recruit
    company: Param
    url: https://param.zohorecruit.com/jobs/PARAM-Kariyer
    enabled: false
    disabled_reason: 'Tenant hydrates from authenticated API; no public anchors.'
```

## Important behavior

Source/search terms are not enough to mark a job as relevant. The scorer first requires a technology/software/support domain signal in the job's own title/description. This prevents non-target roles such as accounting, import/export, planning, sales, logistics, and HR assistant roles from passing just because they are junior.
