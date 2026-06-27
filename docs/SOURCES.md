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
```

## Important behavior

Source/search terms are not enough to mark a job as relevant. The scorer first requires a technology/software/support domain signal in the job's own title/description. This prevents non-target roles such as accounting, import/export, planning, sales, logistics, and HR assistant roles from passing just because they are junior.
