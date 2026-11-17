# acquisition-plans-api

Collecte et stockage des données de plans techniques ferroviaires : objets des plans, nomenclature des équipements, guide des symboles et bulletins de maintenance. Les quatre sources sont contrôlées, nettoyées et stockées dans SQLite. Une API REST viendra les mettre à disposition.

Le dépôt sert surtout à démontrer une chaîne d'intégration continue avec GitHub Actions ; la livraison de l'image Docker est l'étape suivante.

## Origine et périmètre

Ce projet est le prolongement personnel d'un travail de stage. Le lecteur de fichiers ILV écrit pendant le stage appartient au projet de l'entreprise : **il n'est pas inclus ici**. Les trois plans de `acquisition_plans/donnees/` sont des plans **fictifs**, décodés par ce lecteur puis exportés en JSON ; toutes les autres données sont fictives elles aussi.

## Organisation

```
acquisition_plans/
├── collecte/      collecte des quatre sources (plans JSON, CSV, HTML, RSS), déduplication SHA-256
├── stockage/      schéma SQLite (schema.sql) et requêtes (PlanDatabase)
├── donnees/       données fictives de démonstration
└── tests/         contrôle des données et tests automatisés
.github/workflows/ci-cd.yml   chaîne d'intégration continue
```

## Installation

Prérequis : Python 3.12. La collecte et le stockage n'utilisent que la bibliothèque standard ; `requirements-dev.txt` installe les outils de contrôle.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # Linux : source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

## Contrôles et tests en local

Depuis la racine du dépôt, les mêmes commandes que la chaîne :

```powershell
python -B -m ruff check --select E,F,B,SIM,C4,ARG acquisition_plans
python -B -m black --check acquisition_plans
python -B -m acquisition_plans.tests.valider_donnees
python -B -m coverage run --source=acquisition_plans --omit="*/tests/*" -m unittest discover -s acquisition_plans/tests -p "test_*.py" -v
python -B -m coverage report
```

## Chaîne d'intégration continue

Le fichier `.github/workflows/ci-cd.yml` est lu par GitHub Actions. Aucun secret n'est à créer.

**Déclencheurs** : envoi sur `main`, pull request vers `main`, lancement manuel (onglet *Actions*, *Run workflow*).

| Job | Dépend de | Ce qu'il fait | Échoue si |
|---|---|---|---|
| `qualite` | — | Ruff (règles E, F, B, SIM, C4, ARG) et Black | une règle de style n'est pas respectée |
| `donnees` | — | Contrôle des fichiers de `donnees/` : plans JSON (champs, positions, identifiants uniques), colonnes du CSV, articles du guide HTML, bulletins RSS | une donnée source est invalide |
| `tests` | `qualite`, `donnees` | Tests unitaires et d'intégration, mesure de couverture ; rapport HTML publié en artefact | un test échoue ou la couverture est sous 85 % |


**Installer la chaîne** : créer le dépôt sur GitHub et y envoyer ce contenu ; GitHub Actions détecte le workflow sans autre configuration. Les artefacts sont téléchargeables sur la page de chaque exécution.

**Tester la chaîne** :

1. Envoyer un commit sur une branche et ouvrir une pull request vers `main` : `qualite`, `donnees` et `tests` s'exécutent.
2. Fusionner la pull request : les mêmes jobs s'exécutent sur `main`.
3. Vérifier un échec volontaire : sur une branche, supprimer la colonne `description` de `donnees/nomenclature_equipements.csv` ; le job `donnees` échoue et bloque les tests.

## Limites

- La chaîne ne construit pas encore d'image Docker et ne livre rien.
- Les données ne sont pas encore exposées : l'API est l'étape suivante.
- Le lecteur ILV et les plans réels ne sont pas dans ce dépôt.
