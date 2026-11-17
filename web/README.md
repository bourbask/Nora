# NORA — interface web

Dashboard de pilotage financier au-dessus de Firefly III. Deux morceaux :

- `api/` — proxy FastAPI (Python). Détient le token Firefly, calcule les agrégats,
  expose du JSON. **Le token ne touche jamais le navigateur.**
- `ui/` — SPA React + TypeScript (Vite + Tailwind + composants shadcn-style + ECharts).

## Lancer en dev

Deux terminaux (le stack Firefly doit tourner sur `:8066`) :

```sh
# 1) backend
cd web/api
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/uvicorn main:app --reload --port 8068

# 2) frontend
cd web/ui
npm install
npm run dev        # http://localhost:5173  (proxifie /api -> :8068)
```

L'API exige `NORA_API_TOKEN` (`make dev`/`api`/`ui` utilisent `dev` par défaut) ; le proxy Vite l'ajoute côté serveur.
Le navigateur ne parle qu'à Vite (`:5173`) ; `/api/*` est proxifié vers FastAPI.
En prod : nginx sert le build de `ui/` et proxifie `/api` vers `finance-api`
(origine unique, pas de CORS) — Dockerfiles/compose à ajouter lors de la
consolidation NORA.

## Config

- `config/strategy.yaml` (gitignoré, valeurs réelles) — copié depuis
  `config/strategy.example.yaml`. Cibles matelas / taux / buckets / plafond crypto,
  et classification des comptes dormant vs investi.

## État

- ✅ Phase 0 : `/api/networth`, `/api/summary`, `/api/categories/expense`, dashboard, thème.
- ✅ Phase 1 (partiel) : score de santé dormant (`/api/scores`) + carte dédiée.
- ⏳ Phase 2 : Sankey + portefeuille au coût + score investi.
- ⏳ Phase 3 : formulaires stratégie (`GET/PUT /api/strategy`).

## Classement des dépenses

- `GET /api/categories` → `{categories: [{name, budget, poste}]}` : catégories du registre et
  des postes du budget (`budget` vrai, `poste` = nom du poste), postes en tête ; catégories de
  virement exclues.
- `GET /api/categories/suggest?payee=…&payee=…&n=4` → `{suggestions: [{payee, categories, matched}]}` :
  catégories probables par commerçant. D'abord celles de ses opérations déjà classées sur un an
  (motif contenu dans le libellé ou le tiers), par nombre puis récence ; ensuite les plus utilisées
  de l'historique, enfin les autres catégories. `matched` = nombre venant du commerçant lui-même.
- `POST /api/rules` `{payee, poste}` (inchangé) ou `{payee, category, create?}` : n'importe quelle
  catégorie. Nom nettoyé (contrôles et invisibles retirés, espaces réduites), 1 à 60 caractères ;
  une catégorie existante à la casse près est réutilisée ; une inconnue n'est créée qu'avec
  `create: true`. La réponse ajoute alors `category` et `category_created`.

## Vérifs

```sh
python3 web/api/scores.py          # self-check des formules
# API live (backend démarré) :
curl -s localhost:8068/api/networth
curl -s "localhost:8068/api/scores?month=2026-06"
```
