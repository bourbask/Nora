# V2-B — Alertes de dérive (allocation & crypto) — Design

**Date:** 2026-07-09
**Milestone:** V2-B — feature « dérive », non gatée par le matelas (suivi permanent)
**Statut:** auto-validé en mode autonome (décisions documentées dans le rapport) ; à relire.

## Contexte

`invested_health` calcule déjà les distances (`class_align` via TVD vs
`target_buckets`, `crypto_cap` via l'écart au plafond). Cette feature les
transforme en **alertes explicites** au-delà d'un seuil : un bucket hors cible,
ou la crypto au-dessus du cap. Données de portefeuille présentes (holdings) →
sortie live immédiate.

Pose aussi la **couture IA V2-C** à moindre coût : chaque alerte porte un objet
trace `{rule_id, computed_value, threshold, verdict, message}` — payload exact de
la narration IA V3, sans aucun LLM dans le calcul.

Invariants : zéro LLM, déterministe.

## Composant 1 — cœur pur `web/api/drift.py`

Aucune I/O.

- `drift_alerts(bucket_weights_actual, target_buckets, crypto_weight, crypto_cap, threshold=0.10) -> list[dict]`
  - Pour chaque bucket de `set(target) | set(actual)` : `delta = actual - target` ;
    si `abs(delta) > threshold` → alerte
    `{"rule_id": "bucket_drift", "bucket": b, "actual", "target", "delta"(signé),
      "direction": "over"|"under", "computed_value": abs(delta), "threshold",
      "verdict": "drift", "message": "<bucket> à {actual:.0%} vs cible {target:.0%} (écart {delta:+.0%})"}`.
  - Crypto : si `crypto_weight > crypto_cap` → alerte
    `{"rule_id": "crypto_over_cap", "actual": crypto_weight, "cap": crypto_cap,
      "computed_value": crypto_weight, "threshold": crypto_cap, "verdict": "over",
      "message": "crypto à {crypto_weight:.0%} > plafond {crypto_cap:.0%}"}`.
  - Trié : crypto d'abord (dur), puis buckets par `computed_value` desc.
  - Aucune alerte → `[]` (allocation saine).

Self-check `web/api/test_drift.py` (`make test-unit`) :
- bucket au-delà du seuil → 1 alerte, direction correcte (over/under).
- bucket dans le seuil → pas d'alerte.
- crypto > cap → alerte ; crypto ≤ cap → pas d'alerte.
- allocation parfaite → `[]`.
- tri : crypto en tête.

## Composant 2 — knob + endpoint `/api/drift`

- **Modèle** `InvestedEdit` (`main.py`) : ajouter `drift_threshold: float =
  Field(default=0.10, ge=0, le=1)`. Persisté par `PUT /api/strategy` (ajouter la
  clé dans `inv[...]`).
- **Endpoint** `GET /api/drift` (lecture pure) : `pf = fc.portfolio(cfg)` →
  `drift.drift_alerts(pf["bucket_weights"], cfg["invested"]["target_buckets"],
  pf["crypto_weight"], cfg["invested"].get("crypto_cap", 0.10),
  cfg["invested"].get("drift_threshold", 0.10))` → `{"alerts": [...]}`.

## Composant 3 — UI

- **api.ts** : type `DriftAlert` + `Drift` + hook `useDrift()`.
- **Carte « Dérive d'allocation »** (Stratégies, sous les buckets, ou Dashboard) :
  liste des alertes (pastille rouge crypto / orange bucket) avec le `message`, ou
  « Allocation dans les clous ✓ » si vide.
- **Réglages/Stratégies** : le knob `drift_threshold` éditable (input %, défaut 10)
  dans le formulaire invested existant.

## Tests

`test_drift.py` dans `make test-unit`. Endpoint + UI vérifiés live (portefeuille réel).

## Hors scope

- Reco d'action de rééquilibrage (« vends X, achète Y ») — V2-B allocateur/cash-drag séparés, gatés matelas.
- Narration LLM (la trace est posée, la génération = V3).

## Invariants

- Zéro LLM ; déterministe ; cœur pur sans I/O.
- Chaque alerte porte la trace `{rule_id, computed_value, threshold, verdict, message}` (couture V2-C).
- Réutilise `portfolio`, `invested` config, `PUT strategy`.
- Commits `--no-gpg-sign`, sans attribution IA.
