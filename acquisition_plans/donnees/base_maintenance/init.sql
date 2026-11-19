-- Base de maintenance fictive (outil de gestion de maintenance de demonstration).
-- Chargee par le conteneur PostgreSQL a son premier demarrage
-- (dossier /docker-entrypoint-initdb.d). Aucune donnee personnelle :
-- une intervention est rattachee a une equipe, pas a une personne.

CREATE TABLE equipements (
    id SERIAL PRIMARY KEY,
    code_type TEXT NOT NULL,
    numero INTEGER NOT NULL,
    libelle TEXT NOT NULL,
    secteur TEXT NOT NULL,
    mise_en_service DATE NOT NULL,
    UNIQUE (code_type, numero)
);

CREATE TABLE interventions (
    id SERIAL PRIMARY KEY,
    equipement_id INTEGER NOT NULL REFERENCES equipements (id),
    date_intervention DATE NOT NULL,
    nature TEXT NOT NULL CHECK (nature IN ('preventive', 'corrective')),
    statut TEXT NOT NULL CHECK (statut IN ('realisee', 'planifiee', 'annulee')),
    equipe TEXT NOT NULL,
    duree_minutes INTEGER CHECK (duree_minutes > 0),
    compte_rendu TEXT NOT NULL
);

-- Jointure avec les equipements et filtre sur le statut et la date (requetes de la collecte).
CREATE INDEX idx_interventions_equipement ON interventions (equipement_id);
CREATE INDEX idx_interventions_statut_date ON interventions (statut, date_intervention);

INSERT INTO equipements (code_type, numero, libelle, secteur, mise_en_service) VALUES
    ('EQUIP_A', 101, 'Sectionneur Nord 1', 'Secteur Nord', '2019-03-12'),
    ('SIGNAL_B', 204, 'Signal arret Nord', 'Secteur Nord', '2018-06-01'),
    ('EQUIP_A', 102, 'Sectionneur Sud 2', 'Secteur Sud', '2020-09-30'),
    ('CAPTEUR_C', 305, 'Detecteur passage Sud', 'Secteur Sud', '2021-02-15'),
    ('SIGNAL_B', 208, 'Signal entree poste', 'Poste Central', '2017-11-20');

INSERT INTO interventions
    (equipement_id, date_intervention, nature, statut, equipe, duree_minutes, compte_rendu)
VALUES
    (1, '2026-09-08', 'preventive', 'realisee', 'Equipe Nord', 45,
     'Controle du sectionneur et serrage des connexions, aucun defaut constate.'),
    (2, '2026-09-22', 'corrective', 'realisee', 'Equipe Nord', 90,
     'Lampe rouge grillee : remplacement de la lampe et essai du feu conforme.'),
    (4, '2026-10-02', 'corrective', 'realisee', 'Equipe Sud', 120,
     'Detecteur muet apres un orage : remplacement de la carte de detection.'),
    (1, '2026-10-15', 'preventive', 'realisee', 'Equipe Nord', 30,
     'Visite de terrain du sectionneur : reperage conforme au plan.'),
    (3, '2026-10-20', 'preventive', 'annulee', 'Equipe Sud', NULL,
     'Visite reportee : voie occupee par des travaux.'),
    (5, '2026-10-27', 'corrective', 'realisee', 'Equipe Poste Central', 60,
     'Signal bloque au rouge : relais de commande remplace.'),
    (3, '2026-11-05', 'preventive', 'realisee', 'Equipe Sud', 40,
     'Controle du sectionneur et graissage du mecanisme.'),
    (2, '2026-11-12', 'preventive', 'realisee', 'Equipe Nord', 35,
     'Nettoyage de l optique et mesure de l intensite lumineuse.'),
    (4, '2026-12-03', 'preventive', 'planifiee', 'Equipe Sud', 60,
     'Etalonnage annuel du detecteur.'),
    (1, '2026-12-10', 'preventive', 'planifiee', 'Equipe Nord', 45,
     'Controle semestriel du sectionneur.');
