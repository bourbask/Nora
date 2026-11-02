# NORA — *Net-wOrth · Reporting · Analytics*

Self-hosted personal-finance cockpit on top of [Firefly III](https://www.firefly-iii.org/):
a Finary-style dashboard for people who want their banking data to stay on their own
machine. Import your accounts, see where the money flows, and track the health of
your savings and investments — no cloud, no third-party aggregator.

> **Privacy by design.** Which institutions you use, your real account names, balances
> and objectives never live in this repository. They stay in local, gitignored files
> (`.env`, `config/strategy.yaml`, `providers/<yourbank>/`, `docs-local/`). What's public
> is the tool, not how you use it.

## What it does

- **Dashboard** — net worth split *dormant* (cash/savings) vs *invested*, monthly savings
  capacity (net-worth delta — immune to internal transfers), savings rate, top cost centers.
- **Health scores** (0–100, transparent formulas, no ML) — a *dormant* score (safety-cushion
  coverage, cash-drag, savings rate) and an *invested* score (diversification via HHI,
  allocation vs target risk buckets, crypto cap). *(invested score: in progress)*
- **Flow & portfolio** *(in progress)* — a Sankey of monthly money flow and a cost-basis
  allocation of your investments.
- **Strategy** *(in progress)* — set your safety-cushion target, recurring charges, and
  risk-bucket allocation; scores compare actuals to these targets.

## Architecture

```
Firefly III (ledger, source of truth)
        ▲                         ▲
        │ REST (token, server-side)│ import
   web/api  (FastAPI proxy)    scripts/ + providers/
        ▲                         ▲
        │ JSON (aggregates only)   │ standard CSV
   web/ui   (React + TS SPA)   your bank exports
```

- **`web/api`** — thin FastAPI proxy. Holds the Firefly token, computes aggregates, serves
  JSON. The browser never sees the token or raw transactions.
- **`web/ui`** — React + TypeScript SPA (Vite, Tailwind, shadcn-style components, ECharts).
- **`providers/`** — one plugin per institution converts its export to a standard CSV
  (`providers/README.md`). Ship none of your banks publicly; add them locally.
- **`scripts/`** — generic import (`import-csv-firefly.sh`), scheduled `auto-sync.sh`, systemd timer.

## Quick start

```sh
cp .env.example .env                 # fill APP_KEY, DB_PASSWORD, token, AUTO_IMPORT_SECRET
docker compose up -d                 # Firefly + Postgres + Data Importer
cp config/strategy.example.yaml config/strategy.yaml            # your accounts + targets
cp importer/config/config.example.json importer/config/config.json
# add a provider: cp -r providers/example providers/<yourbank> and adapt convert.py
```

Run the dashboard — see [`web/README.md`](web/README.md).

## Configuration

- `config/strategy.yaml` — classify accounts (dormant/invested), safety-cushion target,
  savings-rate target, risk buckets, crypto cap. Drives the health scores.
- `importer/config/config.json` — maps your account names to Firefly account IDs.
- `providers/<name>/` — your per-institution converters + drop-folder.

## Déploiement

Après mise à jour : générer `NORA_API_TOKEN` (`openssl rand -hex 32`) dans `.env` AVANT de déployer, sinon l'API refuse de démarrer. Le jeton ne doit contenir aucun espace.
Toutes les routes exigent `Authorization: Bearer <jeton>` sauf `GET /api/health` ; le proxy du tableau de bord l'ajoute côté serveur.

## Status

MVP in progress: dashboard + dormant health score are live; flow/portfolio, invested
score, and the strategy editor are being built.
