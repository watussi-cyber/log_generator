# Générateur de logs Apache

Produit des logs Apache réalistes, au format « combined », pour tester un analyseur de logs.
Le script apprend le trafic d'un **fichier exemple** réel, puis génère le **nombre de jours** et
le **volume moyen par jour** demandés. Il peut aussi injecter des **anomalies connues**, dont la
liste exacte est écrite dans un manifeste JSON : c'est la « vérité terrain » contre laquelle
comparer les résultats de l'analyseur. Il ajoute enfin du trafic venu des **réseaux sociaux** et
des **annonces payantes**, absent de l'exemple (voir plus bas).

- Python ≥ 3.9, aucune dépendance externe.
- Exemple utilisé par défaut : le `.gz` placé à côté du script
  (par exemple `sample-log-Sep-2026.gz`).

## Utilisation

```bash
python3 generate_logs.py
```

Le script analyse l'exemple (~3 s) puis pose trois questions :

```
Nombre de jours de logs : 30
Nombre moyen de lignes par jour : 10000
Premier jour (AAAA-MM-JJ) [2026-08-31] :        ← Entrée = la période se termine hier
```

Sans les questions, par exemple dans un script :

```bash
python3 generate_logs.py --days 45 --lines-per-day 8000 --start 2026-10-01 --seed 42
```

| Option | Rôle | Défaut |
|---|---|---|
| `--days` | nombre de jours | demandé |
| `--lines-per-day` | nombre moyen de lignes par jour | demandé |
| `--start` | premier jour (AAAA-MM-JJ) | la période se termine hier |
| `--seed` | graine aléatoire : même graine + mêmes paramètres = fichiers identiques | tirée au hasard, affichée en fin d'exécution |
| `--scenarios` | `all`, `none`, ou une liste parmi `fake-googlebot,incident,scan,new-bot` | `all` |
| `--invented-404-ratio` | part des 404 dont l'URL est inventée | `0.3` |
| `--social-share` | part du volume venue des réseaux sociaux (aperçus de liens + visites), `0` pour désactiver | `0.02` |
| `--paid-share` | part du volume venue du trafic payant (robots publicitaires + visites), `0` pour désactiver | `0.01` |
| `--sample` | autre fichier exemple (`.gz` ou texte) | le `.gz` du dossier |
| `--out` | dossier de sortie, **vidé avant chaque génération** | `output/` à côté du script |
| `--keep-output` | ne vide pas le dossier de sortie : seuls les fichiers des mêmes mois sont remplacés | désactivé |

Ordres de grandeur : 360 000 lignes en ~7 s, environ 22 octets par ligne une fois compressé.

### Suivi de la progression

Pendant la génération, le script affiche :

- la **planification** : volumes journaliers minimum et maximum, et date du pic ;
- une **barre de progression** mise à jour après chaque jour : pourcentage du volume, jour en
  cours, lignes écrites, temps écoulé et temps restant estimé ;
- les **événements marquants**, au moment où ils se produisent : scénario injecté (`⚑`) et
  fichier mensuel terminé (`✓`).

```
Planification : de 447 à 42 848 lignes par jour (pic le 12/11/2026)
  ⚑ 18/10/2026 : faux Googlebot injecté (538 lignes)
  ⚑ 25/10/2026 : scan de vulnérabilités injecté (244 lignes)
  ⚑ 26/10/2026 : incident serveur de 08:05 à 09:13 (112 requêtes en 5xx)
  ✓ sample-log-Oct-2026.gz : 210 315 lignes
  █████████████░░░░░░░  66 % · jour 39/45 (08/11/2026) · 238 867 lignes · 8 s · reste ~4 s
```

Si la sortie est redirigée vers un fichier (`> generation.log`), la barre est remplacée par une
ligne tous les 5 % du volume.

## Fichiers produits (dans `output/`)

Le dossier de sortie est **vidé avant chaque génération**, une fois les questions posées. Il ne
contient donc jamais que les fichiers de la dernière génération. Garde-fous :

- le script refuse de vider le dossier personnel, la racine du disque, le dossier du script ou
  celui du fichier exemple ;
- si le dossier contient autre chose que des fichiers du générateur (`*-Mon-AAAA.gz`,
  `manifest-*.json`, `.DS_Store`), le script demande confirmation. En mode non interactif, il
  s'arrête sans rien supprimer.

Fichiers générés :

- **Un `.gz` par mois calendaire**, nommé d'après le fichier exemple :
  `sample-log-Oct-2026.gz`. Les lignes y sont triées chronologiquement.
- **`manifest-<début>_<fin>.json`**, qui contient :
  - `parameters` : paramètres et graine, pour regénérer à l'identique ;
  - `total_lines`, `files`, `daily_lines` : volumes exacts, au total, par fichier et par jour ;
  - `status_codes`, `families` : répartition des codes HTTP et des familles de user-agent ;
  - `scenarios` : pour chaque anomalie injectée, son type, sa description, ses dates, son
    heure de début et de fin, son nombre de lignes, ses IP et son user-agent ;
  - `traffic_sources` : lignes des réseaux sociaux et du trafic payant, séparées entre robots
    et visites, par robot et par réseau ou régie, et la liste des campagnes publicitaires.

## Ce que contient l'exemple

Analyse faite en amont sur le fichier de septembre 2026 :

| Élément | Constat |
|---|---|
| Volume | 319 802 lignes du 31/08 au 28/09 (le 28/09 est partiel et n'est pas utilisé comme modèle) |
| Variabilité | de 696 à 44 897 lignes par jour : chaque pic correspond à un robot qui crawle en masse (Meta les 5-7/09, ClaudeBot le 17/09…) |
| Trafic | 91 % de robots déclarés : Meta 39 %, Ahrefs 29 %, ClaudeBot 15 %, Googlebot 2 %, OpenAI 2 %, Bing 2 % |
| Codes HTTP | 61 % de 200, 34 % de 301, 4,4 % de 404, quelques 401, 503 et 400 |
| Redirections | toutes les 301 ajoutent le `/` final (`/cgp/ville` → `/cgp/ville/`). Taille d'une 301 = longueur de l'URL + 275 à 280 |
| Suite des 301 | Meta suit la redirection en ~2 s depuis une autre IP du même /24, ClaudeBot et OpenAI immédiatement depuis la même IP, Googlebot en ~19 s, Ahrefs jamais immédiatement |
| URLs | 144 000 URLs distinctes : `/i/<id>-<nom>/` (68 %), puis `/courtier-assurance/`, `/courtier-credit/` et `/cgp/` suivis d'une ville |
| 404 | sans taille (`-`) et sans slash final dans 99,7 % des cas |
| « Navigateurs » | 9 % des lignes. Du 5 au 7/09, un seul acteur suit ses 301 depuis la même IP. Les autres jours, la page qui suit la 301 vient d'une autre IP dans ~90 % des cas : ce sont probablement des robots qui font tourner des proxys |
| Format | HTTP/1.1, fuseau Europe/Paris, IPv4 uniquement |
| Réseaux sociaux, payant | Aucune visite venue d'un réseau social ni d'une annonce (aucun referer social, aucun `gclid`, `fbclid`…). Seulement 307 lignes de `facebookexternalhit` (IP Meta 173.252.x, 69.171.x), 116 d'AdsBot-Google (209.85.238.x, 74.125.218.x, 66.249.91.x) et une quarantaine d'aperçus LinkedIn, X, Slack, Telegram, WhatsApp et Discord, tous depuis 136.110.75.x : probablement un seul outil qui change de user-agent |
| Vraies visites | Page, puis `/_astro/Base.….css` et `/favicon.svg` dans les 2 s. Les liens internes n'ont pas de `/` final : un clic donne une 301 puis la page, avec la page précédente en referer (requête comprise) |

## Démarche

### 1. Apprentissage de l'exemple

Chaque ligne est lue et rangée par **famille de user-agent** : `meta-externalagent`, `AhrefsBot`,
`Googlebot`… ou `browser`, ou `tool:<nom>` pour les outils comme `curl` et `python-httpx`.

Quand une requête 200 suit une 301 de la même URL avec un `/` en plus, depuis le même user-agent
et en moins de 30 s, elle est rattachée à cette 301 comme sa **suite**. Le script mémorise le
délai, l'IP, le referer et la taille réels. Le rejeu reproduit ainsi fidèlement le comportement
de chaque robot face aux redirections.

### 2. Volume de chaque jour

Pour chaque famille et chaque jour à générer, le script choisit un **jour modèle** dans
l'exemple :

- La famille parcourt les jours de l'exemple dans l'ordre, par **blocs de longueur aléatoire**
  (~7 jours en moyenne), puis saute ailleurs. Un pic de crawl de plusieurs jours reste donc
  groupé, mais les familles sont désynchronisées et le calendrier ne se répète pas.
- Un bruit d'environ ±20 % est appliqué au volume de chaque famille, chaque jour.
- Les volumes obtenus sont ensuite mis à l'échelle pour que le **total soit exactement**
  `jours × lignes par jour`. On retrouve donc des jours calmes et des jours de pic, comme
  dans l'exemple.

### 3. Contenu d'une journée

Le jour modèle de chaque famille est rejoué avec ses heures réelles, ce qui conserve le rythme
horaire et les rafales. Il est sous-échantillonné ou complété selon le volume voulu :

- **Robots** : chaque requête garde son IP réelle (les plages restent vérifiables par DNS
  inverse) et est décalée de ±5 min. Pour la moitié des requêtes, l'URL est tirée ailleurs
  dans l'historique du même robot, afin de varier les pages crawlées.
- **Navigateurs et outils** : les parcours sont rejoués en entier (toutes les requêtes d'une
  même IP dans la journée), décalés de ±15 min. Les IP sont remplacées par des IP neuves et
  plausibles, cohérentes sur toute la durée d'un bloc : un même visiteur peut ainsi revenir
  plusieurs jours de suite.
- Un dernier ajustement ajoute ou retire quelques requêtes pour atteindre le volume exact du jour.

### 4. 404 inventées

30 % des 404 reçoivent une URL qui n'existe pas dans l'exemple mais reste crédible :

- ville d'une rubrique placée dans une autre (`/courtier-credit/<ville présente seulement en /cgp/>`) ;
- identifiant `/i/` modifié ;
- lettre manquante dans le nom.

Ces URLs gardent la forme sans slash final et la taille `-`, comme les vraies 404.

### 5. Heure et fuseau

Les heures sont produites en Europe/Paris. Les jours de changement d'heure durent réellement
23 h ou 25 h, et le décalage passe de `+0200` à `+0100` (02:59:59 +0200 → 02:00:00 +0100 le
25/10/2026).

## Scénarios injectés (vérité terrain)

Les lignes des scénarios **sont comprises** dans le volume total demandé. Leur taille suit ce
volume et elles ne dépassent jamais 40 % du total.

| Scénario | Ce qui est injecté | Ce que l'analyseur devrait voir |
|---|---|---|
| `fake-googlebot` | ~5 % d'une journée de requêtes avec le user-agent Googlebot, depuis 1 à 3 IP d'hébergeur, sur 1 à 4 h | Googlebot hors des plages 66.249.x (le DNS inverse échoue). Il suit aussi ses 301 en moins de 1,5 s depuis la même IP, alors qu'un vrai Googlebot met ~19 s |
| `incident` | fenêtre de 1 à 2 h, entre 8 h et 21 h, où 85 % des requêtes reçoivent un 503, 502 ou 504. Les redirections qui échouent ne sont plus suivies | pic de 5xx sur une plage horaire, pour tous les visiteurs, Googlebot compris |
| `scan` | 150 à 400 requêtes en quelques minutes depuis une seule IP : `/.env`, `/wp-login.php`, `/.git/HEAD` (403), `/phpmyadmin/`, injections SQL et XSS… | rafale de 404 et 403 sur des chemins sensibles |
| `new-bot` | un robot absent de l'exemple (Bytespider, PetalBot, YandexBot, SemrushBot ou Applebot) qui lit `robots.txt` puis crawle l'équivalent de 0,8 à 1,5 jour de volume, sur 1 ou 2 jours, depuis un /24 de sa vraie plage | nouvelle famille de user-agent, pic de volume |

Les dates et heures exactes de chaque scénario sont dans `scenarios` du manifeste.

## Réseaux sociaux et trafic payant

Ces lignes **sont comprises** dans le volume total demandé : 2 % pour les réseaux sociaux et 1 %
pour le trafic payant par défaut (`--social-share`, `--paid-share`). À 1 000 000 de lignes par
jour, cela fait environ 20 000 et 10 000 lignes par jour. Chaque catégorie est séparée entre
**robots** et **visites**, comme dans l'analyseur.

### Réseaux sociaux

Chaque jour, des **publications** partagent une page du site (fiche ou page ville) :

1. Le robot du réseau lit la page pour afficher l'**aperçu du lien**, au moment de la publication
   puis à chaque nouveau partage. Ces aperçus font 20 % des lignes de la catégorie.
2. Les **clics** arrivent dans les heures qui suivent (délai médian 30 min), surtout depuis un
   mobile, souvent dans le navigateur intégré de l'appli. Une visite charge la page, la CSS et le
   favicon, puis clique parfois vers d'autres pages (301 puis 200, comme dans l'exemple).
3. Une publication sur quatre porte des UTM : `utm_source=<réseau>&utm_medium=social&utm_campaign=publication-AAAA-MM`.

| Réseau | Robot d'aperçu (IP) | Referer des visites | Navigateur intégré |
|---|---|---|---|
| Facebook | `facebookexternalhit` (173.252.x, 69.171.231.x, 69.63.184.x) | `l.facebook.com`, `lm.facebook.com`, `m.facebook.com`, `www.facebook.com` | `FBAN/FBIOS`, `FB_IAB/FB4A` |
| LinkedIn | `LinkedInBot` (108.174.x) | `www.linkedin.com`, `lnkd.in` | `[LinkedInApp]` |
| Instagram | `facebookexternalhit` | `l.instagram.com`, `www.instagram.com` | `Instagram 402…` |
| X | `Twitterbot` (199.16.157.x, 199.59.150.x) | `t.co` | — |
| Reddit, Pinterest | `redditbot`, `Pinterestbot` | `www.reddit.com`, `www.pinterest.fr` | — |
| YouTube, TikTok | aucun | `www.youtube.com`, `m.youtube.com`, `www.tiktok.com` | — |
| WhatsApp, Slack, Telegram, Discord | `WhatsApp/2.26…` (IP du téléphone), `Slackbot-LinkExpanding`, `TelegramBot`, `Discordbot` | aucune visite identifiable | — |

Les messageries ne laissent que l'aperçu : les visites qui en viennent arrivent sans referer et
ne peuvent pas être attribuées, le générateur n'en produit donc pas.

Le volume varie d'un jour à l'autre (±40 %, un peu plus le week-end). Environ un jour sur 25, une
publication devient virale et le volume du jour est multiplié par 3 à 6.

### Trafic payant

Le générateur crée 1 à 3 **campagnes par régie**. Chacune a 3 à 12 pages de destination, des
pages ville d'une rubrique (`/courtier-credit/lyon/`…), de préférence dans les grandes villes.
Chaque clic arrive aux heures d'activité humaine, moins le week-end :

| Régie | Identifiant de clic | UTM | Referer |
|---|---|---|---|
| Google Ads | `gclid` (`wbraid` pour une partie des iPhone) | une campagne sur deux : `utm_source=google&utm_medium=cpc` | `www.google.com`, `www.google.fr` |
| Microsoft Ads | `msclkid` | presque toujours : `utm_source=bing&utm_medium=cpc` | `www.bing.com` |
| Facebook Ads, Instagram Ads | `fbclid` | `utm_source=facebook` ou `instagram`, `utm_medium=paid_social` | domaines Facebook ou Instagram |
| LinkedIn Ads | `li_fat_id` | `utm_source=linkedin&utm_medium=paid_social` | `www.linkedin.com` |

L'identifiant de clic est placé après les UTM, comme le font les régies :
`/courtier-credit/lyon/?utm_source=google&utm_medium=cpc&utm_campaign=courtier-credit-idf&gclid=Cj0KCQjw…_BwE`.

Les **robots publicitaires** font 30 % des lignes de la catégorie. Ils contrôlent les pages de
destination par petites sessions de 2 à 8 pages, et parfois d'autres pages du site (comme pour
les annonces dynamiques) :

- `AdsBot-Google-Mobile` et `AdsBot-Google`, avec les user-agents et les plages d'IP de
  l'exemple. La CSS suit parfois la page, depuis une autre IP du même /24 ;
- `adidxbot` (Microsoft Ads), depuis les plages de bingbot.

### Vérité terrain

`traffic_sources` du manifeste donne, pour `social` et `paid` :

- `robots.lines` et `robots.by_robot` : lignes de robots injectées, par robot ;
- `robots.replayed_from_sample` : lignes des mêmes robots déjà présentes dans l'exemple et
  rejouées avec le trafic de base. Un analyseur doit les compter aussi ;
- `visits.visits`, `visits.lines`, `visits.by_source` : nombre de visites et de lignes, par
  réseau ou régie ;
- `paid.campaigns` : régie, nom, identifiant de clic, UTM et pages de destination de chaque
  campagne.

Si un incident tombe le même jour, les visites concernées reçoivent une 5xx et perdent leurs
lignes suivantes. Le manifeste compte les lignes réellement écrites.

## Limites connues

- **Découpage mensuel** : les fichiers suivent les mois calendaires. Le fichier de l'hébergeur
  « Sep-2026 » commençait en réalité le 31/08 à 00:20 : sa rotation ne tombe pas exactement
  au 1er du mois.
- **Pas de nouvelles pages** : en dehors des 404 inventées, les URLs sont celles de l'exemple.
  Aucune page en 200 n'apparaît qui n'existait pas en septembre.
- **Tailles des 502 et 504** : fixées à 341 et 247 octets, car l'exemple n'en contient pas.
  Les 503 reprennent la taille observée (6 278 octets).
- **IP des visiteurs** : uniquement IPv4, comme dans l'exemple. Leur premier octet suit la
  répartition observée, le reste est aléatoire.
- **Plusieurs exemples** : ils ne peuvent pas être combinés. Pour couvrir un trafic plus
  varié, il faut un fichier exemple plus long.
- **Réseaux sociaux et payant** : l'exemple n'en contient pas, ces lignes ne sont donc pas
  apprises. Les referers, user-agents d'applis, formats d'identifiants de clic et plages d'IP
  (hors Facebook, AdsBot et bingbot, vus dans l'exemple) sont écrits en dur en tête du script.
  Les pages, tailles, fichiers chargés et user-agents des navigateurs viennent de l'exemple.
- **`fbclid` organique** : dans la réalité, Facebook ajoute `fbclid` à presque tous les liens
  sortants, publicités ou non. Le générateur ne le met que sur les visites payantes, pour que
  la vérité terrain reste nette. Sur de vrais logs, un analyseur qui classe tout `fbclid` en
  payant y rangera aussi les clics organiques venus de Facebook.

## Réglages fins

Les constantes en tête de `generate_logs.py` (`BLOCK_JUMP`, `DAILY_NOISE`, `BOT_NOVELTY`,
`INCIDENT_FAILURE_RATE`, `SCENARIO_MAX_SHARE`…) règlent la longueur des blocs, l'intensité du
bruit, la variété des URLs et la sévérité des anomalies.

Pour les réseaux sociaux et le trafic payant : `SOCIAL_BOT_SHARE` et `PAID_BOT_SHARE` (part des
robots), `SOCIAL_UTM_RATE`, `IN_APP_RATE`, `VIRAL_DAY_RATE`, et les tables `SOCIAL_NETWORKS`,
`PAID_PLATFORMS`, `LINK_PREVIEW_BOTS` et `PAID_BOTS` (poids, referers, user-agents, plages d'IP).
