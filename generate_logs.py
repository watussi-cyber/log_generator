#!/usr/bin/env python3
"""Générateur de logs Apache réalistes à partir d'un fichier exemple.

Le script apprend le trafic du fichier exemple (robots, codes HTTP, URLs,
enchaînements 301 -> 200, rythme horaire et journalier), puis produit des
logs au même format pour la période et le volume demandés. Il peut injecter
des anomalies connues (faux Googlebot, incident serveur, scan, nouveau robot),
listées avec leurs dates exactes dans un manifeste JSON (la "vérité terrain").
Il ajoute du trafic venu des réseaux sociaux (aperçus de liens, visites) et des
annonces payantes (gclid, msclkid, fbclid, li_fat_id, AdsBot), décompté lui aussi.

Usage :
    python3 generate_logs.py                      # questions interactives
    python3 generate_logs.py --days 30 --lines-per-day 10000 --start 2026-10-01 --seed 42

Aucune dépendance externe (Python >= 3.9). Voir README.md pour la démarche.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import random
import re
import shutil
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from operator import itemgetter
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_NUM = {m: i + 1 for i, m in enumerate(MONTHS)}
DAY = 86400
DAY_MAX = DAY - 0.001

# --- Paramètres de modélisation ------------------------------------------------
FOLLOW_MAX_DELAY = 30          # s : délai max entre une 301 et la requête qui la suit
COMPLETE_DAY_MIN_SPAN = 20 * 3600  # un jour de l'exemple sert de modèle s'il couvre >= 20 h
BOT_JITTER = 300               # s : décalage aléatoire de chaque requête de robot
VISITOR_JITTER = 900           # s : décalage commun à tout le parcours d'un visiteur
EXTRA_JITTER = 1800            # s : requêtes ajoutées quand le volume dépasse l'exemple
BOT_NOVELTY = 0.5              # part des requêtes de robot dont l'URL est tirée ailleurs dans l'exemple
BLOCK_JUMP = 1 / 7             # chance, chaque jour, qu'une famille saute ailleurs dans l'exemple
DAILY_NOISE = 0.2              # écart-type (log) du bruit appliqué au volume de chaque famille et jour
SCENARIO_MAX_SHARE = 0.4       # les scénarios injectés ne dépassent pas 40 % du volume total
INCIDENT_FAILURE_RATE = 0.85   # part des requêtes en erreur pendant l'incident
ERROR_SIZES = {"502": "341", "504": "247"}  # 503 : taille observée dans l'exemple

# --- Réseaux sociaux et trafic payant ----------------------------------------------
SOCIAL_SHARE = 0.02            # part du volume total venant des réseaux sociaux (aperçus de liens + visites)
PAID_SHARE = 0.01              # part du volume total venant du trafic payant (robots publicitaires + visites)
SOURCES_MAX_SHARE = 0.5        # réseaux sociaux + trafic payant ne dépassent pas 50 % du volume
SOCIAL_BOT_SHARE = 0.2         # part des lignes "réseaux sociaux" écrites par les robots d'aperçu de liens
PAID_BOT_SHARE = 0.3           # part des lignes "trafic payant" écrites par les robots publicitaires
SOCIAL_UTM_RATE = 0.25         # part des publications dont le lien porte utm_medium=social
IN_APP_RATE = 0.6              # part des visites mobiles faites dans le navigateur intégré de l'appli
SOCIAL_NAV_RATE = 0.2          # chance de cliquer vers une autre page (répétée, 3 pages au plus)
PAID_NAV_RATE = 0.4
VIRAL_DAY_RATE = 0.04          # chance, chaque jour, qu'une publication devienne virale (volume x3 à x6)
PAID_BOT_EXPLORE = 0.25        # part des contrôles de robots publicitaires hors pages de destination

SCENARIOS = ["fake-googlebot", "incident", "scan", "new-bot"]

# Plages d'hébergeurs "génériques" pour les acteurs malveillants (hors plages de Google).
HOSTING_PREFIXES = ["45.148.10", "91.92.243", "194.26.29", "80.94.95", "147.45.112", "185.224.128", "103.145.13"]

NEW_BOTS = [  # (user-agent, préfixe /16 des IP du robot)
    ("Mozilla/5.0 (Linux; Android 5.0) AppleWebKit/537.36 (KHTML, like Gecko) Mobile Safari/537.36 "
     "(compatible; Bytespider; spider-feedback@bytedance.com)", "47.128"),
    ("Mozilla/5.0 (Linux; Android 7.0;) AppleWebKit/537.36 (KHTML, like Gecko) Mobile Safari/537.36 "
     "(compatible; PetalBot;+https://webmaster.petalsearch.com/site/petalbot)", "114.119"),
    ("Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)", "5.255"),
    ("Mozilla/5.0 (compatible; SemrushBot/7~bl; +http://www.semrush.com/bot.html)", "185.191"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 "
     "Safari/605.1.15 (Applebot/0.1; +http://www.apple.com/go/applebot)", "17.241"),
]

DEFAULT_GOOGLEBOT_UA = ("Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/153.0.8010.52 Mobile Safari/537.36 "
                        "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)")

SCANNER_UAS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "python-requests/2.32.3",
    "Mozilla/5.0 zgrab/0.x",
    "curl/8.5.0",
]
SCAN_PATHS = [
    "/.env", "/.env.local", "/.env.production", "/.git/config", "/.git/HEAD", "/.svn/entries",
    "/.htaccess", "/.htpasswd", "/.DS_Store", "/.aws/credentials", "/.ssh/id_rsa",
    "/wp-login.php", "/wp-admin/", "/wp-admin/install.php", "/wp-admin/setup-config.php",
    "/wp-includes/wlwmanifest.xml", "/wp-content/plugins/", "/xmlrpc.php", "/wp-config.php.bak", "/wp-config.php~",
    "/phpmyadmin/", "/pma/", "/myadmin/", "/phpinfo.php", "/info.php", "/test.php",
    "/admin/", "/admin/login", "/administrator/", "/login", "/user/login",
    "/config.php", "/config.json", "/config.yml", "/configuration.php", "/settings.py", "/web.config",
    "/server-status", "/server-info", "/backup.zip", "/backup.sql", "/db.sql", "/dump.sql",
    "/site.tar.gz", "/www.zip", "/database.sql", "/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php",
    "/cgi-bin/luci", "/cgi-bin/test.cgi", "/shell.php", "/cmd.php", "/upload.php", "/filemanager/",
    "/api/v1/users", "/api/.env", "/actuator/env", "/actuator/health", "/console", "/solr/admin/",
    "/jenkins/login", "/manager/html", "/owa/", "/remote/login", "/HNAP1/", "/boaform/admin/formLogin",
    "/index.php?page=../../../../etc/passwd", "/?id=1%27%20OR%20%271%27=%271",
    "/search?q=%3Cscript%3Ealert(1)%3C/script%3E", "/../../../../etc/passwd",
]
SCAN_PREFIXES = ["/wp", "/blog", "/old", "/new", "/backup", "/site", "/test"]
FORBIDDEN_RX = re.compile(r"/\.(ht|git|svn)")

# Robots d'aperçu de liens : famille -> (user-agents, préfixes /24 des IP). Préfixes None :
# l'aperçu est fabriqué par le téléphone de l'expéditeur, donc depuis une IP de particulier.
LINK_PREVIEW_BOTS = {
    "facebookexternalhit": (["facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"],
                            ["173.252.82", "173.252.83", "173.252.87", "173.252.95", "69.171.231", "69.63.184"]),
    "LinkedInBot": (["LinkedInBot/1.0 (compatible; Mozilla/5.0; Apache-HttpClient +http://www.linkedin.com)"],
                    ["108.174.2", "108.174.3", "108.174.5", "108.174.10"]),
    "Twitterbot": (["Twitterbot/1.0"], ["199.16.157", "199.59.150"]),
    "Pinterestbot": (["Mozilla/5.0 (compatible; Pinterestbot/1.0; +http://www.pinterest.com/bot.html)"], ["54.236.1"]),
    "redditbot": (["Mozilla/5.0 (compatible; redditbot/1.0; +http://www.reddit.com/feedback)"], ["52.90.80", "54.82.91"]),
    "WhatsApp": (["WhatsApp/2.26.18.74 A", "WhatsApp/2.26.18.74 i"], None),
    "Slackbot-LinkExpanding": (["Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)"],
                               ["3.89.143", "54.209.62", "18.207.131"]),
    "TelegramBot": (["TelegramBot (like TwitterBot)"], ["149.154.161"]),
    "Discordbot": (["Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"], ["35.227.62", "34.83.20"]),
}

# Réseaux sociaux : poids, robot d'aperçu, part de visites sur mobile, appli (navigateur intégré),
# utm_source et referers (ordinateur / mobile). Les messageries n'ont pas de referers : seul
# l'aperçu du lien est visible, les visites qui en viennent ne sont pas identifiables.
SOCIAL_NETWORKS = {
    "Facebook": dict(weight=30, preview="facebookexternalhit", mobile=0.75, app="facebook", utm="facebook",
                     desktop=["https://l.facebook.com/", "https://www.facebook.com/"],
                     mobile_ref=["https://lm.facebook.com/", "https://m.facebook.com/"]),
    "LinkedIn": dict(weight=24, preview="LinkedInBot", mobile=0.55, app="linkedin", utm="linkedin",
                     desktop=["https://www.linkedin.com/"],
                     mobile_ref=["https://www.linkedin.com/", "https://lnkd.in/"]),
    "Instagram": dict(weight=10, preview="facebookexternalhit", mobile=0.95, app="instagram", utm="instagram",
                      desktop=["https://www.instagram.com/"], mobile_ref=["https://l.instagram.com/"]),
    "X": dict(weight=9, preview="Twitterbot", mobile=0.65, utm="twitter",
              desktop=["https://t.co/"], mobile_ref=["https://t.co/"]),
    "Reddit": dict(weight=4, preview="redditbot", mobile=0.6, utm="reddit",
                   desktop=["https://www.reddit.com/"], mobile_ref=["https://www.reddit.com/"]),
    "Pinterest": dict(weight=3, preview="Pinterestbot", mobile=0.8, utm="pinterest",
                      desktop=["https://www.pinterest.fr/"], mobile_ref=["https://www.pinterest.fr/"]),
    "YouTube": dict(weight=4, preview=None, mobile=0.7, utm="youtube",
                    desktop=["https://www.youtube.com/"], mobile_ref=["https://m.youtube.com/"]),
    "TikTok": dict(weight=3, preview=None, mobile=0.95, utm="tiktok",
                   desktop=["https://www.tiktok.com/"], mobile_ref=["https://www.tiktok.com/"]),
    "WhatsApp": dict(weight=8, preview="WhatsApp"),
    "Slack": dict(weight=3, preview="Slackbot-LinkExpanding"),
    "Telegram": dict(weight=3, preview="TelegramBot"),
    "Discord": dict(weight=2, preview="Discordbot"),
}

# Régies publicitaires : poids, utm_source / utm_medium, identifiant de clic ajouté à l'URL,
# chance qu'une campagne porte aussi des UTM, part mobile, appli et referers.
PAID_PLATFORMS = {
    "Google Ads": dict(weight=50, source="google", medium="cpc", click_id="gclid", utm=0.5, mobile=0.6,
                       desktop=["https://www.google.com/", "https://www.google.fr/"],
                       mobile_ref=["https://www.google.com/"]),
    "Microsoft Ads": dict(weight=12, source="bing", medium="cpc", click_id="msclkid", utm=0.9, mobile=0.3,
                          desktop=["https://www.bing.com/"], mobile_ref=["https://www.bing.com/"]),
    "Facebook Ads": dict(weight=18, source="facebook", medium="paid_social", click_id="fbclid", utm=1.0,
                         mobile=0.85, app="facebook",
                         desktop=["https://l.facebook.com/", "https://www.facebook.com/"],
                         mobile_ref=["https://lm.facebook.com/", "https://m.facebook.com/"]),
    "Instagram Ads": dict(weight=8, source="instagram", medium="paid_social", click_id="fbclid", utm=1.0,
                          mobile=0.95, app="instagram",
                          desktop=["https://www.instagram.com/"], mobile_ref=["https://l.instagram.com/"]),
    "LinkedIn Ads": dict(weight=12, source="linkedin", medium="paid_social", click_id="li_fat_id", utm=1.0,
                         mobile=0.45, app="linkedin",
                         desktop=["https://www.linkedin.com/"], mobile_ref=["https://www.linkedin.com/"]),
}
CAMPAIGN_THEMES = ["national", "grandes-villes", "idf", "prospection", "retargeting", "notoriete"]
# Les annonces visent surtout les grandes villes : pages de destination choisies en priorité ici.
BIG_CITIES = {"paris", "marseille", "lyon", "toulouse", "nice", "nantes", "montpellier", "strasbourg", "bordeaux",
              "lille", "rennes", "toulon", "reims", "saint-etienne", "le-havre", "grenoble", "dijon", "angers",
              "nimes", "villeurbanne", "clermont-ferrand", "aix-en-provence", "le-mans", "brest", "tours",
              "amiens", "limoges", "annecy", "perpignan", "metz", "besancon", "orleans", "rouen", "caen", "nancy"}

# Robots publicitaires, qui contrôlent les pages de destination des annonces :
# famille -> (poids, user-agent par défaut, préfixes /24 des IP, régie concernée).
GOOGLE_ADS_PREFIXES = ["209.85.238", "74.125.218", "66.249.91"]       # observés dans l'exemple
BING_PREFIXES = ["40.77.167", "52.167.144", "207.46.13", "157.55.39"]  # bingbot dans l'exemple
PAID_BOTS = {
    "AdsBot-Google-Mobile": (5, "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/153.0.8010.52 Mobile Safari/537.36 "
                                "(compatible; AdsBot-Google-Mobile; +http://www.google.com/mobile/adsbot.html)",
                             GOOGLE_ADS_PREFIXES, "Google Ads"),
    "AdsBot-Google": (3, "AdsBot-Google (+http://www.google.com/adsbot.html)", GOOGLE_ADS_PREFIXES, "Google Ads"),
    "adidxbot": (2, "Mozilla/5.0 (compatible; adidxbot/2.0; +http://www.bing.com/bingbot.htm)",
                 BING_PREFIXES, "Microsoft Ads"),
}

# Navigateur intégré des applis : suffixe ajouté au user-agent du téléphone.
IN_APP_SUFFIXES = {
    "facebook": {"ios": " [FBAN/FBIOS;FBAV/531.0.0.38.109;FBBV/812345678;FBDV/iPhone16,2;FBMD/iPhone;FBSN/iOS;"
                        "FBSV/26.6.1;FBSS/3;FBCR/;FBID/phone;FBLC/fr_FR;FBOP/5;FBRV/0]",
                 "android": " [FB_IAB/FB4A;FBAV/531.0.0.48.74;]"},
    "instagram": {"ios": " Instagram 402.0.0.31.87 (iPhone16,2; iOS 26_6_1; fr_FR; fr; scale=3.00; 1290x2796; 812345679)",
                  "android": " Instagram 402.0.0.31.87 Android (35/15; 450dpi; 1080x2340; samsung; SM-S921B; e1s; "
                             "s5e9945; fr_FR; 812345680)"},
    "linkedin": {"ios": " [LinkedInApp]/9.32.1452", "android": " [LinkedInApp]/4.1.1120"},
}
DEFAULT_DESKTOP_UAS = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/152.0.0.0 Safari/537.36"]
DEFAULT_MOBILE_UAS = ["Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                      "Version/26.6 Mobile/15E148 Safari/604.1",
                      "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/152.0.0.0 Mobile Safari/537.36"]
# Activité humaine par heure (Europe/Paris) : publications et clics sur les annonces.
HUMAN_HOURS = [0.5, 0.3, 0.2, 0.15, 0.15, 0.25, 0.6, 1.2, 1.9, 2.4, 2.6, 2.5,
               2.2, 2.3, 2.5, 2.5, 2.4, 2.3, 2.3, 2.5, 2.7, 2.6, 2.0, 1.1]
TOKEN_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

# Ligne Apache "combined". Les champs entre guillemets peuvent contenir des \" échappés.
QUOTED = r'"([^"\\]*(?:\\.[^"\\]*)*)"'
LINE_RX = re.compile(
    r'^(\S+) \S+ \S+ \[(\d{2})/(\w{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2}) [+-]\d{4}\] '
    + QUOTED + r' (\d{3}) (\S+) ' + QUOTED + " " + QUOTED)
SECTION_RX = re.compile(r"^/([^/?]+)/([^/?]+)/?$")
IPV4_RX = re.compile(r"^(\d{1,3})\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
ORIGIN_RX = re.compile(r"^(https?://[^/]+)")
STATIC_RX = re.compile(r"^GET \S+\.(?:css|js|mjs|svg|ico|png|jpe?g|gif|webp|avif|woff2?|json)(?:\?\S*)? ")

BOT_RX = re.compile(r"bot|crawl|spider|slurp|externalhit|externalagent|fetcher|archiver", re.I)
BOT_NAME_RX = re.compile(r"([A-Za-z][\w.-]*?(?:bot|crawler|spider|externalhit|externalagent)[\w-]*)", re.I)
COMPATIBLE_RX = re.compile(r"compatible; ?([A-Za-z][\w.-]*)")


def classify(ua):
    """Famille d'un user-agent, et s'il s'agit d'un robot déclaré (dont on garde les IP)."""
    compat = COMPATIBLE_RX.search(ua)
    if compat and compat.group(1) in ("MSIE", "Trident"):
        compat = None
    if compat or BOT_RX.search(ua):
        named = compat or BOT_NAME_RX.search(ua)
        return (named.group(1) if named else ua[:30]), True
    if ua.startswith("Mozilla/5.0 ("):
        return "browser", False
    return "tool:" + re.split(r"[/ ;(]", ua, maxsplit=1)[0][:30], False


# --- Profil appris sur l'exemple -------------------------------------------------
# Une "entrée" est une requête initiale de l'exemple, sous forme de tuple :
#   (jour, heure_en_s, ip, requête, statut, taille, referer, user_agent, suite)
# où "suite" vaut None ou (délai, ip, requête, statut, taille, referer) : la requête
# qui a suivi une redirection 301 (même user-agent, même URL + "/").

class Family:
    def __init__(self, name, is_bot):
        self.name = name
        self.is_bot = is_bot
        self.pool = []                       # toutes les entrées
        self.by_day = defaultdict(list)      # jour modèle -> entrées
        self.lines_by_day = Counter()        # jour modèle -> lignes (suites comprises)
        self.groups_by_day = {}              # jour modèle -> parcours (entrées d'une même IP)
        self.groups_all = []                 # tous les parcours, tous jours confondus
        self.ua_counts = Counter()


class Profile:
    def __init__(self, path):
        self.path = path
        self.families = {}
        self.days = []
        self.n_lines = 0
        self.unparsed = 0
        self.first_day = self.last_day = None
        self.known_urls = set()      # URLs existantes (statut != 404), sans slash final
        self.crawl_pool = []         # entrées de robots réutilisables par les scénarios
        self.single_pool = []        # (famille, entrée) d'une seule ligne, pour ajuster les volumes
        self.first_octets = []       # 1er octet des IP des visiteurs (géographie plausible)
        self.size_503 = "299"
        self.robots_entry = None
        self.googlebot_ua = DEFAULT_GOOGLEBOT_UA
        # Pour les visites venues des réseaux sociaux et des annonces :
        self.site_origin = "https://www.example.com"   # referer interne le plus fréquent
        self.pages = {}              # page servie en 200 (URL avec / final) -> taille
        self.redirects = []          # (requête 301, taille, requête suivie, taille) : clic sur un lien interne
        self.assets = []             # (requête, taille, probabilité) : fichiers chargés avec une page
        self.desktop_uas = []        # user-agents de vrais navigateurs (qui chargent les fichiers de la page)
        self.mobile_uas = []
        self._load()

    def _load(self):
        entries = []      # listes mutables le temps du chargement
        pending = {}      # (user-agent, URL attendue) -> indices des 301 en attente de suite
        spans = {}
        classes = {}
        size_503 = Counter()
        opener = gzip.open if self.path.suffix == ".gz" else open
        with opener(self.path, "rt", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                m = LINE_RX.match(raw)
                if not m:
                    self.unparsed += 1
                    continue
                self.n_lines += 1
                ip, dd, mon, yyyy, hh, mi, ss, req, status, size, ref, ua = m.groups()
                day = date(int(yyyy), MONTH_NUM[mon], int(dd)).toordinal()
                tod = int(hh) * 3600 + int(mi) * 60 + int(ss)
                now = day * DAY + tod
                span = spans.setdefault(day, [tod, tod])
                span[0], span[1] = min(span[0], tod), max(span[1], tod)
                ip, ref, ua = sys.intern(ip), sys.intern(ref), sys.intern(ua)
                status, size = sys.intern(status), sys.intern(size)
                parts = req.split(" ")
                url = parts[1] if len(parts) == 3 else None
                if status == "503":
                    size_503[size] += 1

                # Cette requête est-elle la suite d'une 301 récente ?
                if status == "200" and url is not None:
                    waiting = pending.get((ua, url))
                    while waiting and now - entries[waiting[0]][9] > FOLLOW_MAX_DELAY:
                        waiting.pop(0)
                    if waiting:
                        parent = entries[waiting.pop(0)]
                        parent[8] = (now - parent[9], ip, req, status, size, ref)
                        continue

                entries.append([day, tod, ip, req, status, size, ref, ua, None, now])
                if ua not in classes:
                    classes[ua] = classify(ua)
                if status == "301" and url is not None and not url.endswith("/"):
                    pending.setdefault((ua, url + "/"), []).append(len(entries) - 1)

        if not entries:
            raise SystemExit(f"Aucune ligne exploitable dans {self.path}")
        if size_503:
            self.size_503 = size_503.most_common(1)[0][0]

        all_days = sorted(spans)
        self.first_day, self.last_day = date.fromordinal(all_days[0]), date.fromordinal(all_days[-1])
        complete = [d for d in all_days if spans[d][1] - spans[d][0] >= COMPLETE_DAY_MIN_SPAN]
        self.days = complete or all_days
        model_days = set(self.days)

        groups = defaultdict(lambda: defaultdict(list))   # famille -> (jour, ip) -> entrées
        origins = Counter()
        for raw_entry in entries:
            e = tuple(raw_entry[:9])
            day, ip, req, status, ua, follow = e[0], e[2], e[3], e[4], e[7], e[8]
            name, is_bot = classes[ua]
            fam = self.families.get(name)
            if fam is None:
                fam = self.families[name] = Family(name, is_bot)
            fam.pool.append(e)
            fam.ua_counts[ua] += 1
            if day in model_days:
                fam.by_day[day].append(e)
                fam.lines_by_day[day] += 2 if follow else 1
                if not is_bot:
                    groups[name][(day, ip)].append(e)

            parts = req.split(" ")
            if len(parts) == 3 and status != "404":
                self.known_urls.add(parts[1].rstrip("/") or "/")
                if follow:
                    self.known_urls.add(follow[2].split(" ")[1].rstrip("/") or "/")
            if len(parts) == 3 and parts[0] == "GET":
                if status == "200" and parts[1].endswith("/") and "?" not in parts[1]:
                    self.pages[parts[1]] = e[5]
                if follow and status == "301" and follow[3] == "200":
                    fparts = follow[2].split(" ")
                    if len(fparts) == 3 and fparts[1].endswith("/") and "?" not in fparts[1]:
                        self.pages[fparts[1]] = follow[4]
                        self.redirects.append((req, e[5], follow[2], follow[4]))
            if e[6] != "-":
                m = ORIGIN_RX.match(e[6])
                if m:
                    origins[m.group(1)] += 1
            if parts[1:2] == ["/robots.txt"] and self.robots_entry is None:
                self.robots_entry = e
            if is_bot:
                if req.startswith("GET ") and status in ("200", "301", "404"):
                    self.crawl_pool.append(e)
                if follow is None and status in ("200", "404"):
                    self.single_pool.append((name, e))
            else:
                m = IPV4_RX.match(ip)
                if m:
                    self.first_octets.append(int(m.group(1)))

        for name, by_key in groups.items():
            fam = self.families[name]
            for (day, _ip), group in by_key.items():
                fam.groups_by_day.setdefault(day, []).append(group)
                fam.groups_all.append(group)

        self._learn_visits(origins)
        google = self.families.get("Googlebot")
        if google:
            self.googlebot_ua = google.ua_counts.most_common(1)[0][0]
        if not self.single_pool:
            self.single_pool = [(f.name, e[:8] + (None,)) for f in self.families.values() for e in f.pool[:1000]]
        if not self.crawl_pool:
            self.crawl_pool = [e for f in self.families.values() for e in f.pool]
        if not self.first_octets:
            self.first_octets = list(range(1, 224))

    def _learn_visits(self, origins):
        """Ce que charge un vrai navigateur avec une page (CSS, favicon...), et ses user-agents."""
        if origins:
            self.site_origin = origins.most_common(1)[0][0]
        internal = self.site_origin + "/"
        static, uas = Counter(), Counter()
        for fam in self.families.values():
            if fam.is_bot:
                continue
            for e in fam.pool:
                if e[4] == "200" and e[6].startswith(internal) and STATIC_RX.match(e[3]):
                    static[(e[3], e[5])] += 1
                    if fam.name == "browser" and "Headless" not in e[7]:
                        uas[e[7]] += 1
        if static:
            top = static.most_common(1)[0][1]
            # Le fichier le plus demandé (la CSS) est chargé 9 fois sur 10, les autres en proportion.
            self.assets = [(req, size, 0.9 * n / top) for (req, size), n in static.most_common(4) if n >= 0.03 * top]
        for ua, n in sorted(uas.items()):
            pool = self.mobile_uas if re.search(r"Mobile|iPhone|Android", ua) else self.desktop_uas
            pool.extend([ua] * n)
        self.desktop_uas = self.desktop_uas or DEFAULT_DESKTOP_UAS[:]
        self.mobile_uas = self.mobile_uas or DEFAULT_MOBILE_UAS[:]

    def summary(self):
        lines = Counter()
        for fam in self.families.values():
            lines[fam.name] = sum(2 if e[8] else 1 for e in fam.pool)
        total = sum(lines.values())
        bots = sum(n for name, n in lines.items() if self.families[name].is_bot)
        top = ", ".join(f"{name} {100 * n / total:.0f} %" for name, n in lines.most_common(6))
        return (f"Exemple : {fmt_int(self.n_lines)} lignes du {self.first_day:%d/%m/%Y} au {self.last_day:%d/%m/%Y} "
                f"({len(self.days)} jours complets servent de modèle"
                + (f", {self.unparsed} lignes illisibles ignorées" if self.unparsed else "") + ")\n"
                f"Trafic : {100 * bots / total:.0f} % de robots déclarés. Principales familles : {top}")


# --- 404 inventées ------------------------------------------------------------------

class UrlInventor:
    """Fabrique des URLs inexistantes mais crédibles à partir des rubriques du site."""

    def __init__(self, known_urls, rng):
        self.known = known_urls
        self.rng = rng
        sections = defaultdict(set)
        for url in known_urls:
            m = SECTION_RX.match(url)
            if m:
                sections[m.group(1)].add(m.group(2))
        self.sections = {s: sorted(v) for s, v in sorted(sections.items()) if len(v) >= 20}
        # Rubriques à identifiants ("/i/13002715-nom") vs rubriques à noms de villes.
        self.id_sections = [s for s, v in self.sections.items()
                            if sum(1 for slug in v if re.match(r"\d+-", slug)) > len(v) / 2]
        self.city_sections = [s for s in self.sections if s not in self.id_sections]

    def invent(self, trailing_slash):
        rng = self.rng
        if not self.sections:
            return None
        for _ in range(20):
            section = rng.choice(list(self.sections))
            slug = rng.choice(self.sections[section])
            method = rng.random()
            if method < 0.5 and len(self.city_sections) > 1 and section in self.city_sections:
                # ville d'une rubrique placée dans une autre rubrique
                other = rng.choice([s for s in self.city_sections if s != section])
                slug = rng.choice(self.sections[other])
            elif section in self.id_sections and method < 0.8:
                # identifiant modifié
                digits = slug.split("-", 1)[0]
                slug = "".join(rng.choice("0123456789") for _ in digits) + slug[len(digits):]
            elif len(slug) > 4:
                # faute de frappe : une lettre manquante
                i = rng.randrange(1, len(slug) - 1)
                slug = slug[:i] + slug[i + 1:]
            url = f"/{section}/{slug}"
            if url not in self.known:
                return url + ("/" if trailing_slash else "")
        return None

    def rewrite(self, req):
        parts = req.split(" ")
        if len(parts) != 3:
            return None
        url = self.invent(parts[1].endswith("/"))
        return f"{parts[0]} {url} {parts[2]}" if url else None


# --- Génération --------------------------------------------------------------------
# Un "événement" est une liste de lignes (1, ou 2 si la 301 est suivie). Une ligne est :
#   [heure_en_s, ip, requête, statut, taille, referer, user_agent, famille, scénario]

def keep_ip(ip):
    return ip


def reflect(t):
    """Ramène une heure (en s depuis minuit) dans la journée, par effet miroir."""
    if t < 0:
        t = -t
    if t > DAY_MAX:
        t = 2 * DAY_MAX - t
    return min(max(t, 0.0), DAY_MAX)


def stochastic_round(x, rng):
    n = int(x)
    return n + (1 if rng.random() < x - n else 0)


def apportion(total, weights):
    """Répartit un entier proportionnellement aux poids (méthode du plus fort reste)."""
    if not weights:
        return []
    s = sum(weights)
    if s <= 0:
        weights, s = [1] * len(weights), len(weights)
    raw = [total * w / s for w in weights]
    out = [int(x) for x in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)
    for i in order[:total - sum(out)]:
        out[i] += 1
    return out


def fmt_int(n):
    return f"{n:,}".replace(",", " ")


class Generator:
    def __init__(self, profile, seed, invented_404_ratio):
        self.profile = profile
        self.rng = random.Random(seed)
        self.invented_404_ratio = invented_404_ratio
        self.inventor = UrlInventor(profile.known_urls, self.rng)
        self.families = list(profile.families.values())
        self.schedule = {}     # famille -> [(jour modèle, n° de bloc, multiplicateur)] pour chaque jour généré
        self.block_maps = {}

    # IP des visiteurs : remplacées par des IP neuves, cohérentes sur un cycle de l'exemple.
    def random_ip(self):
        rng = self.rng
        while True:
            a = rng.choice(self.profile.first_octets)
            b = rng.randrange(256)
            if (a in (0, 10, 127) or a >= 224 or (a == 169 and b == 254) or (a == 172 and 16 <= b <= 31)
                    or (a == 192 and b == 168) or (a == 100 and 64 <= b <= 127)):
                continue
            return f"{a}.{b}.{rng.randrange(256)}.{rng.randint(1, 254)}"

    def ip_mapper(self, mapping):
        def mapper(ip):
            new = mapping.get(ip)
            if new is None:
                new = mapping[ip] = self.random_ip()
            return new
        return mapper

    def build_schedule(self, n_days):
        """Choisit, pour chaque famille et chaque jour généré, le jour de l'exemple à rejouer.

        Chaque famille parcourt les jours de l'exemple dans l'ordre, par blocs de longueur
        aléatoire (les pics de plusieurs jours sont conservés), puis saute ailleurs : les
        familles sont désynchronisées et le calendrier ne se répète pas.
        """
        rng = self.rng
        days = self.profile.days
        for fam in self.families:
            pos, block, plan = rng.randrange(len(days)), 0, []
            for di in range(n_days):
                if di:
                    if rng.random() < BLOCK_JUMP:
                        pos, block = rng.randrange(len(days)), block + 1
                    else:
                        pos = (pos + 1) % len(days)
                plan.append((days[pos], block, rng.lognormvariate(0, DAILY_NOISE)))
            self.schedule[fam.name] = plan

    def block_map(self, fam, block):
        """Correspondance IP d'origine -> IP neuve, stable le temps d'un bloc."""
        current = self.block_maps.get(fam.name)
        if current is None or current[0] != block:
            current = self.block_maps[fam.name] = (block, {})
        return current[1]

    def make_event(self, e, tod, ipf, fam):
        tod = reflect(tod)
        req = e[3]
        if e[4] == "404" and self.rng.random() < self.invented_404_ratio:
            req = self.inventor.rewrite(req) or req
        event = [[tod, ipf(e[2]), req, e[4], e[5], e[6], e[7], fam, None]]
        if e[8]:
            delay, fip, freq, fstatus, fsize, fref = e[8]
            event.append([min(tod + delay, DAY_MAX), ipf(fip), freq, fstatus, fsize, fref, e[7], fam, None])
        return event

    def add_visit(self, events, group, mapper, fam):
        shift = self.rng.uniform(-VISITOR_JITTER, VISITOR_JITTER)
        added = 0
        for e in group:
            event = self.make_event(e, e[1] + shift, mapper, fam)
            events.append(event)
            added += len(event)
        return added

    def base_day(self, di, scale):
        """Rejoue, pour chaque famille, son jour modèle mis à l'échelle du volume voulu."""
        rng = self.rng
        events = []
        for fam in self.families:
            sday, block, mult = self.schedule[fam.name][di]
            keep = min(scale * mult, 1.0)
            extra = max(scale * mult - 1.0, 0.0)
            if fam.is_bot:
                src = fam.by_day.get(sday)
                if not src:
                    continue
                for e in src:
                    if rng.random() < keep:
                        content = rng.choice(fam.pool) if rng.random() < BOT_NOVELTY else e
                        tod = e[1] + rng.uniform(-BOT_JITTER, BOT_JITTER)
                        events.append(self.make_event(content, tod, keep_ip, fam.name))
                for _ in range(stochastic_round(extra * len(src), rng)):
                    tod = rng.choice(src)[1] + rng.uniform(-EXTRA_JITTER, EXTRA_JITTER)
                    events.append(self.make_event(rng.choice(fam.pool), tod, keep_ip, fam.name))
            else:
                groups = fam.groups_by_day.get(sday)
                if not groups:
                    continue
                mapper = self.ip_mapper(self.block_map(fam, block))
                for group in groups:
                    if rng.random() < keep:
                        self.add_visit(events, group, mapper, fam.name)
                need = extra * fam.lines_by_day[sday]
                while need > 0:
                    need -= self.add_visit(events, rng.choice(fam.groups_all), self.ip_mapper({}), fam.name)
        return events

    def add_fillers(self, events, count, avoid=None):
        """Ajoute des requêtes de robots d'une seule ligne (ajustement fin du volume)."""
        rng = self.rng
        for _ in range(count):
            fam, e = rng.choice(self.profile.single_pool)
            tod = rng.uniform(0, DAY_MAX)
            while avoid and avoid[0] <= tod < avoid[1]:
                tod = rng.uniform(0, DAY_MAX)
            events.append([[tod, e[2], e[3], e[4], e[5], e[6], e[7], fam, None]])

    def balance(self, events, target):
        """Ajuste la journée au nombre exact de lignes voulu."""
        n = sum(len(ev) for ev in events)
        while n > target:
            i = self.rng.randrange(len(events))
            n -= len(events[i])
            events[i] = events[-1]
            events.pop()
        if n < target:
            self.add_fillers(events, target - n)

    # --- Scénarios -------------------------------------------------------------
    def injected_event(self, e, tod, ip, ua, fam, tag):
        tod = reflect(tod)
        event = [[tod, ip, e[3], e[4], e[5], "-", ua, fam, tag]]
        if e[8]:
            _delay, _ip, freq, fstatus, fsize, _ref = e[8]
            event.append([min(tod + self.rng.uniform(0, 1.5), DAY_MAX), ip, freq, fstatus, fsize, "-", ua, fam, tag])
        return event

    def plan_scenarios(self, selected, n_days, per_day):
        rng = self.rng
        sizes = {"fake-googlebot": max(30, round(0.05 * per_day)),
                 "scan": rng.randint(150, 400),
                 "new-bot": round(per_day * rng.uniform(0.8, 1.5))}
        estimated = sum(sizes[s] * {"fake-googlebot": 1.4, "scan": 1, "new-bot": 1.5}[s]
                        for s in selected if s in sizes)
        factor = min(1.0, SCENARIO_MAX_SHARE * n_days * per_day / estimated) if estimated else 1.0
        sizes = {k: max(1, round(v * factor)) for k, v in sizes.items()}

        day_order = list(range(n_days))
        rng.shuffle(day_order)

        def pick_day():
            return day_order.pop() if day_order else rng.randrange(n_days)

        prefixes = rng.sample(HOSTING_PREFIXES, 2)
        injections, incident = [], None
        for name in selected:
            if name == "fake-googlebot":
                injections.append(self.scenario_fake_googlebot(pick_day(), sizes[name], prefixes[0]))
            elif name == "scan":
                injections.append(self.scenario_scan(pick_day(), sizes[name], prefixes[1]))
            elif name == "new-bot":
                first = pick_day()
                days = [first, first + 1] if first + 1 < n_days and rng.random() < 0.6 else [first]
                injections.append(self.scenario_new_bot(days, sizes[name]))
            elif name == "incident":
                duration = rng.uniform(60, 120) * 60
                start = rng.uniform(8 * 3600, 20 * 3600)
                incident = {"type": "incident", "day": pick_day(), "start": start, "end": start + duration,
                            "rate": INCIDENT_FAILURE_RATE, "statuses": Counter(), "follow_ups_removed": 0}
        return injections, incident

    def scenario_fake_googlebot(self, di, n_events, prefix):
        rng = self.rng
        ips = sorted({f"{prefix}.{rng.randint(2, 254)}" for _ in range(rng.randint(1, 3))})
        duration = rng.uniform(1, 4) * 3600
        start = rng.uniform(0, DAY - duration)
        ua = self.profile.googlebot_ua
        events = [self.injected_event(rng.choice(self.profile.crawl_pool),
                                      start + duration * (i + rng.random()) / n_events,
                                      rng.choice(ips), ua, "Googlebot (faux)", "fake-googlebot")
                  for i in range(n_events)]
        return {"type": "fake-googlebot",
                "description": "User-agent Googlebot depuis des IP qui n'appartiennent pas à Google "
                               "(un vrai Googlebot vient de 66.249.x.x et se vérifie par DNS inverse).",
                "events_by_day": {di: events}, "ips": ips, "user_agent": ua}

    def scenario_scan(self, di, n_requests, prefix):
        rng = self.rng
        ip = f"{prefix}.{rng.randint(2, 254)}"
        ua = rng.choice(SCANNER_UAS)
        paths = SCAN_PATHS[:]
        rng.shuffle(paths)
        while len(paths) < n_requests:
            paths.append(rng.choice(SCAN_PREFIXES) + rng.choice(SCAN_PATHS))
        tod = rng.uniform(0, DAY - 1000)
        events = []
        for path in paths[:n_requests]:
            method = "POST" if path.endswith(("/xmlrpc.php", "/wp-login.php")) and rng.random() < 0.5 else "GET"
            status, size = ("403", "199") if FORBIDDEN_RX.search(path) else ("404", "-")
            events.append([[tod, ip, f"{method} {path} HTTP/1.1", status, size, "-", ua, "scan", "scan"]])
            tod += rng.uniform(0.05, 1.5)
        return {"type": "scan",
                "description": "Scan de vulnérabilités : une IP teste en rafale des chemins sensibles "
                               "(/.env, /wp-admin, /.git, injections...).",
                "events_by_day": {di: events}, "ips": [ip], "user_agent": ua}

    def scenario_new_bot(self, days, n_events):
        rng = self.rng
        candidates = [b for b in NEW_BOTS if classify(b[0])[0] not in self.profile.families] or NEW_BOTS
        ua, prefix16 = rng.choice(candidates)
        name = classify(ua)[0]
        network = f"{prefix16}.{rng.randint(0, 255)}"
        ips = sorted({f"{network}.{rng.randint(2, 254)}" for _ in range(rng.randint(6, 20))})
        events_by_day = {}
        for di, count in zip(days, apportion(n_events, [1] * len(days))):
            duration = rng.uniform(8, 16) * 3600
            start = rng.uniform(0, DAY - duration)
            events = []
            if self.profile.robots_entry:
                events.append(self.injected_event(self.profile.robots_entry, start, rng.choice(ips), ua, name, "new-bot"))
            for i in range(count):
                events.append(self.injected_event(rng.choice(self.profile.crawl_pool),
                                                  start + duration * (i + rng.random()) / max(count, 1),
                                                  rng.choice(ips), ua, name, "new-bot"))
            events_by_day[di] = events
        return {"type": "new-bot",
                "description": f"Robot jamais vu auparavant ({name}) qui crawle massivement "
                               f"pendant {len(days)} jour(s).",
                "events_by_day": events_by_day, "ips": ips, "user_agent": ua}

    def apply_incident(self, events, incident):
        """Passe en 5xx la plupart des requêtes de la fenêtre d'incident."""
        rng = self.rng
        removed = 0
        for event in events:
            first = event[0]
            if incident["start"] <= first[0] < incident["end"] and rng.random() < incident["rate"]:
                code = rng.choices(["503", "502", "504"], [0.7, 0.2, 0.1])[0]
                first[3] = code
                first[4] = ERROR_SIZES.get(code, self.profile.size_503)
                incident["statuses"][code] += 1
                if len(event) > 1:          # plus de redirection, donc plus de requête qui suit
                    removed += len(event) - 1
                    del event[1:]
        incident["follow_ups_removed"] += removed
        self.add_fillers(events, removed, avoid=(incident["start"], incident["end"]))

    # --- Boucle principale --------------------------------------------------------
    def run(self, n_days, per_day, start, selected, writer, progress, social_share=0.0, paid_share=0.0):
        progress.start()
        self.build_schedule(n_days)
        volumes = [sum(fam.lines_by_day.get(self.schedule[fam.name][di][0], 0) * self.schedule[fam.name][di][2]
                       for fam in self.families)
                   for di in range(n_days)]
        injections, incident = self.plan_scenarios(selected, n_days, per_day)
        injected = Counter()
        for sc in injections:
            for di, evs in sc["events_by_day"].items():
                injected[di] += sum(len(ev) for ev in evs)
        total = n_days * per_day
        sources = SourceTraffic(self, social_share, paid_share)
        sources.plan(n_days, start, total)
        for di in range(n_days):
            injected[di] += sources.social_lines[di] + sources.paid_lines[di]
        targets = apportion(max(total - sum(injected.values()), 0), volumes)
        day_totals = [targets[di] + injected[di] for di in range(n_days)]
        peak = max(range(n_days), key=day_totals.__getitem__)
        progress.message(f"Planification : de {fmt_int(min(day_totals))} à {fmt_int(max(day_totals))} lignes "
                         f"par jour (pic le {start + timedelta(days=peak):%d/%m/%Y})")

        for di in range(n_days):
            day = start + timedelta(days=di)
            scale = targets[di] / volumes[di] if volumes[di] else 0.0
            events = self.base_day(di, scale)
            self.balance(events, targets[di])
            events.extend(sources.day_events(di, day))
            for sc in injections:
                evs = sc["events_by_day"].get(di)
                if evs:
                    events.extend(evs)
                    progress.message(f"  ⚑ {day:%d/%m/%Y} : {scenario_label(sc)} injecté "
                                     f"({fmt_int(sum(len(ev) for ev in evs))} lignes)")
            if incident and incident["day"] == di:
                self.apply_incident(events, incident)
                progress.message(f"  ⚑ {day:%d/%m/%Y} : incident serveur de {iso_time(day, incident['start'])[11:16]} "
                                 f"à {iso_time(day, incident['end'])[11:16]} "
                                 f"({fmt_int(sum(incident['statuses'].values()))} requêtes en 5xx)")
            lines = [line for ev in events for line in ev]
            lines.sort(key=itemgetter(0))
            writer.write_day(day, lines)
            progress.day_done(day, len(lines))
        writer.close()
        progress.finish()
        return injections, incident, sources


# --- Réseaux sociaux et trafic payant ------------------------------------------------
# Lignes marquées "<social|paid>|<robots|visits>|<source>" : le manifeste en fait le décompte.

class SourceTraffic:
    """Visites venues des réseaux sociaux et des annonces payantes, et robots associés.

    Réseaux sociaux : chaque publication partage une page du site. Le robot du réseau lit la
    page pour en afficher l'aperçu, puis les clics arrivent dans les heures qui suivent, avec
    le referer du réseau (et parfois des UTM). Les messageries ne laissent que l'aperçu.
    Trafic payant : des campagnes par régie, chacune avec ses pages de destination. Chaque
    clic ajoute son identifiant (gclid, msclkid, fbclid, li_fat_id) et souvent des UTM ;
    AdsBot et adidxbot contrôlent les pages de destination.
    """

    def __init__(self, gen, social_share, paid_share):
        self.gen = gen
        self.rng = gen.rng
        profile = gen.profile
        self.origin = profile.site_origin
        self.pages = list(profile.pages.items())
        self.redirects = profile.redirects
        self.assets = profile.assets
        if not self.pages:          # exemple sans page HTML exploitable
            social_share = paid_share = 0.0
        self.social_share, self.paid_share = social_share, paid_share
        self.social_lines, self.paid_lines = [], []
        self.visits = Counter()     # (social|paid, source) -> nombre de visites
        self.campaigns = []
        self.landings = defaultdict(list)    # régie -> pages de destination de ses campagnes
        self.city_pages = defaultdict(list)  # rubrique à villes -> pages
        self.big_city_pages = defaultdict(list)
        for path, size in self.pages:
            m = SECTION_RX.match(path)
            if m and m.group(1) in gen.inventor.city_sections:
                self.city_pages[m.group(1)].append((path, size))
                if m.group(2) in BIG_CITIES:
                    self.big_city_pages[m.group(1)].append((path, size))
        # Navigateur intégré des applis : construit sur un vrai Safari iOS ou Chrome Android.
        self.in_app_bases = [ua for ua in profile.mobile_uas
                             if re.search(r"iPhone.*Version/\S+ Mobile/\S+ Safari/604\.1$", ua)
                             or re.search(r"Android.*\) AppleWebKit/537\.36 \(KHTML, like Gecko\) Chrome/\S+ Mobile Safari/537\.36$", ua)
                             ] or DEFAULT_MOBILE_UAS[:]

    # --- Planification -------------------------------------------------------------
    def plan(self, n_days, start, total):
        """Volume de chaque jour, et campagnes publicitaires actives sur toute la période."""
        rng = self.rng
        social_w, paid_w = [], []
        for di in range(n_days):
            weekend = (start + timedelta(days=di)).weekday() >= 5
            w = rng.lognormvariate(0, 0.4) * (1.15 if weekend else 1.0)
            if rng.random() < VIRAL_DAY_RATE:
                w *= rng.uniform(3, 6)
            social_w.append(w)
            paid_w.append(rng.lognormvariate(0, 0.15) * (0.6 if weekend else 1.0))
        self.social_lines = apportion(round(total * self.social_share), social_w)
        self.paid_lines = apportion(round(total * self.paid_share), paid_w)
        if self.paid_share:
            self.plan_campaigns()

    def plan_campaigns(self):
        rng = self.rng
        sections = sorted(self.city_pages)
        names = set()
        for platform, cfg in PAID_PLATFORMS.items():
            n = rng.randint(1, 3)
            for _ in range(n):
                section = rng.choice(sections) if sections else None
                pool = (self.big_city_pages.get(section) or self.city_pages[section]) if section else self.pages
                name = f"{section or 'site'}-{rng.choice(CAMPAIGN_THEMES)}"
                while name in names:
                    name += "-2"
                names.add(name)
                landings = rng.sample(pool, min(len(pool), rng.randint(3, 12)))
                self.landings[platform].extend(landings)
                self.campaigns.append({"platform": platform, "name": name, "utm": rng.random() < cfg["utm"],
                                       "landings": landings, "weight": cfg["weight"] * rng.uniform(0.5, 1.5) / n})

    # --- Briques communes ----------------------------------------------------------
    def human_time(self):
        hour = self.rng.choices(range(24), HUMAN_HOURS)[0]
        return min(hour * 3600 + self.rng.uniform(0, 3600), DAY_MAX)

    def prefix_ip(self, prefixes):
        return f"{self.rng.choice(prefixes)}.{self.rng.randint(1, 254)}"

    def token(self, n):
        return "".join(self.rng.choice(TOKEN_CHARS) for _ in range(n))

    def click_param(self, kind, ios):
        rng = self.rng
        if kind == "gclid":
            if ios and rng.random() < 0.4:      # clic depuis iOS : Google remplace gclid par wbraid
                return "wbraid=Cl" + self.token(rng.randint(60, 80))
            return "gclid=" + rng.choice(["Cj0KCQjw", "Cj0KCQiA", "EAIaIQobChMI"]) + self.token(rng.randint(70, 85)) + "_BwE"
        if kind == "msclkid":
            return f"msclkid={rng.getrandbits(128):032x}"
        if kind == "fbclid":
            return "fbclid=" + rng.choice(["IwAR", "IwZXh0bgNhZW0CMTAAAR"]) + self.token(rng.randint(50, 60))
        h = f"{rng.getrandbits(128):032x}"
        return f"li_fat_id={h[:8]}-{h[8:12]}-4{h[13:16]}-{'89ab'[int(h[16], 16) % 4]}{h[17:20]}-{h[20:]}"

    def visitor(self, cfg):
        """User-agent et referer d'un visiteur venu de ce réseau ou de cette régie."""
        rng = self.rng
        profile = self.gen.profile
        if rng.random() < cfg["mobile"]:
            if cfg.get("app") and rng.random() < IN_APP_RATE:
                ua = self.in_app_ua(rng.choice(self.in_app_bases), cfg["app"])
            else:
                ua = rng.choice(profile.mobile_uas)
            return ua, rng.choice(cfg["mobile_ref"])
        return rng.choice(profile.desktop_uas), rng.choice(cfg["desktop"])

    @staticmethod
    def in_app_ua(ua, app):
        if "iPhone" in ua:     # WebView iOS : ni Version/ ni Safari/
            ua = re.sub(r" Version/\S+", "", ua).replace(" Safari/604.1", "")
            return ua + IN_APP_SUFFIXES[app]["ios"]
        if "; wv)" not in ua:
            ua = ua.replace(")", "; wv)", 1)
        return ua + IN_APP_SUFFIXES[app]["android"]

    def visit(self, t, path, size, query, referer, ua, tag, nav_rate):
        """Lignes d'une visite : page d'arrivée, fichiers de la page, puis quelques clics internes."""
        rng = self.rng
        ip = self.gen.random_ip()
        url = f"{path}?{query}" if query else path
        page = self.origin + url
        lines = [[t, ip, f"GET {url} HTTP/1.1", "200", size, referer, ua, "browser", tag]]
        for req, asset_size, prob in self.assets:
            if rng.random() < prob:
                lines.append([t + rng.uniform(0.05, 2.0), ip, req, "200", asset_size, page, ua, "browser", tag])
        clicks = 0
        while self.redirects and clicks < 3 and rng.random() < nav_rate:
            # Les liens internes n'ont pas de / final : 301, puis la page, comme dans l'exemple.
            clicks += 1
            t += rng.lognormvariate(math.log(45), 0.9)
            req, size301, follow_req, follow_size = rng.choice(self.redirects)
            lines.append([t, ip, req, "301", size301, page, ua, "browser", tag])
            lines.append([t + rng.uniform(0.05, 0.5), ip, follow_req, "200", follow_size, page, ua, "browser", tag])
            page = self.origin + follow_req.split(" ")[1]
        return [line for line in lines if line[0] <= DAY_MAX]

    def preview(self, t, path, size, family):
        uas, prefixes = LINK_PREVIEW_BOTS[family]
        ip = self.prefix_ip(prefixes) if prefixes else self.gen.random_ip()
        return [[min(t, DAY_MAX), ip, f"GET {path} HTTP/1.1", "200", size, "-", self.rng.choice(uas),
                 family, f"social|robots|{family}"]]

    def bot_uas(self, family, default):
        """User-agents du robot tels qu'observés dans l'exemple (versions de Chrome à jour)."""
        fam = self.gen.profile.families.get(family)
        return list(fam.ua_counts.elements()) if fam else [default]

    # --- Génération d'une journée --------------------------------------------------
    def day_events(self, di, day):
        events = []
        if self.social_lines and self.social_lines[di]:
            events.extend(self.social_day(day, self.social_lines[di]))
        if self.paid_lines and self.paid_lines[di]:
            events.extend(self.paid_day(self.paid_lines[di]))
        return events

    def social_day(self, day, budget):
        rng = self.rng
        events, posts = [], []
        robots = round(budget * SOCIAL_BOT_SHARE)
        left = budget - robots
        public = [name for name, cfg in SOCIAL_NETWORKS.items() if "desktop" in cfg]
        weights = [SOCIAL_NETWORKS[name]["weight"] for name in public]

        # 1. Publications, et visites qu'elles amènent dans les heures qui suivent.
        while left > 0:
            name = rng.choices(public, weights)[0]
            net = SOCIAL_NETWORKS[name]
            path, size = rng.choice(self.pages)
            post = {"network": name, "path": path, "size": size, "t0": self.human_time(), "times": []}
            posts.append(post)
            query = (f"utm_source={net['utm']}&utm_medium=social&utm_campaign=publication-{day:%Y-%m}"
                     if rng.random() < SOCIAL_UTM_RATE else "")
            for _ in range(max(1, round(rng.lognormvariate(math.log(5), 1.0)))):
                t = post["t0"] + rng.lognormvariate(math.log(1800), 1.1)
                if t > DAY_MAX:
                    continue
                ua, ref = self.visitor(net)
                lines = self.visit(t, path, size, query, ref, ua, f"social|visits|{name}", SOCIAL_NAV_RATE)[:left]
                events.append(lines)
                left -= len(lines)
                post["times"].append(t)
                self.visits["social", name] += 1
                if left <= 0:
                    break

        # 2. Aperçus de liens : au moment de la publication, puis à chaque nouveau partage
        #    (y compris dans les messageries, qui ne laissent que cette trace).
        first = [p for p in posts if SOCIAL_NETWORKS[p["network"]]["preview"]]
        rng.shuffle(first)
        for post in first[:robots]:
            events.append(self.preview(post["t0"], post["path"], post["size"],
                                       SOCIAL_NETWORKS[post["network"]]["preview"]))
        robots -= min(robots, len(first))
        by_network = defaultdict(list)
        for post in first:
            by_network[post["network"]].append(post)
        sharers = [name for name, cfg in SOCIAL_NETWORKS.items() if cfg["preview"]]
        share_w = [SOCIAL_NETWORKS[name]["weight"] for name in sharers]
        for _ in range(robots):
            name = rng.choices(sharers, share_w)[0]
            if by_network.get(name):
                post = rng.choice(by_network[name])
                t = rng.choice(post["times"] or [post["t0"]]) + rng.uniform(0, 900)
                path, size = post["path"], post["size"]
            else:
                (path, size), t = rng.choice(self.pages), self.human_time()
            events.append(self.preview(t, path, size, SOCIAL_NETWORKS[name]["preview"]))
        return events

    def paid_day(self, budget):
        rng = self.rng
        events = []
        robots = round(budget * PAID_BOT_SHARE)
        left = budget - robots
        weights = [c["weight"] for c in self.campaigns]

        # 1. Clics sur les annonces, aux heures d'activité humaine.
        while left > 0:
            camp = rng.choices(self.campaigns, weights)[0]
            cfg = PAID_PLATFORMS[camp["platform"]]
            path, size = rng.choice(camp["landings"])
            ua, ref = self.visitor(cfg)
            params = []
            if camp["utm"]:
                params.append(f"utm_source={cfg['source']}&utm_medium={cfg['medium']}&utm_campaign={camp['name']}")
            params.append(self.click_param(cfg["click_id"], "iPhone" in ua))
            lines = self.visit(self.human_time(), path, size, "&".join(params), ref, ua,
                               f"paid|visits|{camp['platform']}", PAID_NAV_RATE)[:left]
            events.append(lines)
            left -= len(lines)
            self.visits["paid", camp["platform"]] += 1

        # 2. Robots publicitaires : contrôles des pages de destination, par petites sessions.
        families = list(PAID_BOTS)
        bot_w = [PAID_BOTS[f][0] for f in families]
        while robots > 0:
            family = rng.choices(families, bot_w)[0]
            _w, default_ua, prefixes, platform = PAID_BOTS[family]
            ua = rng.choice(self.bot_uas(family, default_ua))
            prefix = rng.choice(prefixes)
            landings = self.landings.get(platform) or self.pages
            tag = f"paid|robots|{family}"
            t = rng.uniform(0, DAY_MAX - 1800)
            for _ in range(rng.randint(2, 8)):
                if robots <= 0:
                    break
                path, size = rng.choice(self.pages if rng.random() < PAID_BOT_EXPLORE else landings)
                event = [[t, f"{prefix}.{rng.randint(1, 254)}", f"GET {path} HTTP/1.1", "200", size, "-", ua, family, tag]]
                if robots > 1 and self.assets and rng.random() < 0.4:    # page rendue : la CSS suit
                    req, asset_size, _p = self.assets[0]
                    event.append([t + rng.uniform(1, 15), f"{prefix}.{rng.randint(1, 254)}", req, "200",
                                  asset_size, self.origin + path, ua, family, tag])
                events.append(event)
                robots -= len(event)
                t += rng.uniform(5, 180)
        return events

    # --- Vérité terrain ------------------------------------------------------------
    def summary(self, writer):
        """Décompte exact, par catégorie, des lignes écrites (après un éventuel incident)."""
        out = {}
        for category, share in (("social", self.social_share), ("paid", self.paid_share)):
            robots, visit_lines = Counter(), Counter()
            for tag, info in writer.tags.items():
                parts = tag.split("|")
                if len(parts) == 3 and parts[0] == category:
                    (robots if parts[1] == "robots" else visit_lines)[parts[2]] += info["lines"]
            visits = Counter({src: n for (cat, src), n in self.visits.items() if cat == category})
            # Robots de mêmes familles déjà présents dans l'exemple et rejoués avec le trafic de base.
            replayed = {f: writer.families[f] - robots[f] for f in robots if writer.families[f] > robots[f]}
            out[category] = {
                "share": share,
                "lines": sum(robots.values()) + sum(visit_lines.values()),
                "robots": {"lines": sum(robots.values()), "by_robot": dict(robots.most_common()),
                           "replayed_from_sample": replayed},
                "visits": {"visits": sum(visits.values()), "lines": sum(visit_lines.values()),
                           "by_source": {src: {"visits": visits[src], "lines": visit_lines[src]}
                                         for src in sorted(set(visits) | set(visit_lines),
                                                           key=lambda s: -visit_lines[s])}},
            }
        out["paid"]["campaigns"] = [
            {"platform": c["platform"], "name": c["name"], "click_id": PAID_PLATFORMS[c["platform"]]["click_id"],
             "utm": ({"utm_source": PAID_PLATFORMS[c["platform"]]["source"],
                      "utm_medium": PAID_PLATFORMS[c["platform"]]["medium"], "utm_campaign": c["name"]}
                     if c["utm"] else None),
             "landing_pages": [path for path, _size in c["landings"]]}
            for c in self.campaigns]
        return out


def scenario_label(sc):
    if sc["type"] == "new-bot":
        return f"nouveau robot ({classify(sc['user_agent'])[0]})"
    return {"fake-googlebot": "faux Googlebot", "scan": "scan de vulnérabilités"}.get(sc["type"], sc["type"])


# --- Écriture -------------------------------------------------------------------

def day_bounds(d):
    start = int(datetime(d.year, d.month, d.day, tzinfo=TZ).timestamp())
    nxt = d + timedelta(days=1)
    end = int(datetime(nxt.year, nxt.month, nxt.day, tzinfo=TZ).timestamp())
    return start, (end - start) / DAY      # durée réelle (23 h ou 25 h aux changements d'heure)


def apache_time(ts):
    dt = datetime.fromtimestamp(ts, TZ)
    off = int(dt.utcoffset().total_seconds()) // 60
    sign = "+" if off >= 0 else "-"
    off = abs(off)
    return (f"{dt.day:02d}/{MONTHS[dt.month - 1]}/{dt.year}:{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d} "
            f"{sign}{off // 60:02d}{off % 60:02d}")


def iso_time(d, tod):
    start, scale = day_bounds(d)
    return datetime.fromtimestamp(start + int(tod * scale), TZ).isoformat()


class MonthlyWriter:
    """Un fichier .gz par mois calendaire, nommé comme le fichier de l'hébergeur."""

    def __init__(self, out_dir, prefix, protected, log=print):
        self.out_dir = out_dir
        self.prefix = prefix
        self.protected = protected.resolve()
        self.log = log
        self.current = None
        self.fh = None
        self.files = []
        self.daily = {}
        self.statuses = Counter()
        self.families = Counter()
        self.tags = {}

    def path_for(self, d):
        return self.out_dir / f"{self.prefix}-{MONTHS[d.month - 1]}-{d.year}.gz"

    def write_day(self, d, lines):
        if (d.year, d.month) != self.current:
            self.close()
            path = self.path_for(d)
            if path.resolve() == self.protected:
                raise SystemExit(f"Refus d'écraser le fichier exemple : {path}")
            self.fh = gzip.open(path, "wt", encoding="utf-8", newline="\n", compresslevel=6)
            self.current = (d.year, d.month)
            self.files.append({"file": path.name, "lines": 0})
        start, scale = day_bounds(d)
        out = []
        last_ts, stamp = None, ""
        for tod, ip, req, status, size, ref, ua, fam, tag in lines:
            ts = start + int(tod * scale)
            if ts != last_ts:
                last_ts, stamp = ts, apache_time(ts)
            out.append(f'{ip} - - [{stamp}] "{req}" {status} {size} "{ref}" "{ua}"\n')
            if tag:
                info = self.tags.setdefault(tag, {"lines": 0, "first": ts, "last": ts})
                info["lines"] += 1
                info["first"], info["last"] = min(info["first"], ts), max(info["last"], ts)
        self.fh.write("".join(out))
        self.files[-1]["lines"] += len(lines)
        self.daily[d.isoformat()] = len(lines)
        self.statuses.update(line[3] for line in lines)
        self.families.update(line[7] for line in lines)

    def close(self):
        if self.fh:
            self.fh.close()
            self.log(f"  ✓ {self.files[-1]['file']} : {fmt_int(self.files[-1]['lines'])} lignes")
            self.fh = None


# --- Suivi de la progression ------------------------------------------------------

def fmt_duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"


class Progress:
    """Suivi de la génération.

    Dans un terminal : une barre mise à jour en place après chaque jour, avec les messages
    importants (scénarios injectés, fichiers terminés) affichés au-dessus. Quand la sortie
    est redirigée (fichier, pipe) : une ligne tous les 5 % du volume.
    """

    BAR_WIDTH = 20
    REPORT_STEP = 0.05

    def __init__(self, total_lines, n_days, stream=None):
        self.stream = stream or sys.stdout
        self.live = self.stream.isatty()
        self.total = max(total_lines, 1)
        self.n_days = n_days
        self.lines = 0
        self.days = 0
        self.day = None
        self.next_report = self.REPORT_STEP
        self.drawn = False
        self.t0 = time.time()

    def start(self):
        self.t0 = time.time()

    def message(self, text):
        """Affiche un message permanent sans casser la barre."""
        self._clear()
        print(text, file=self.stream, flush=True)
        if self.days:
            self._draw()

    def day_done(self, day, lines):
        self.days += 1
        self.lines += lines
        self.day = day
        if self.live:
            self._draw()
            return
        done = self.lines / self.total
        if done >= self.next_report or self.days == self.n_days:
            print(self._status(), file=self.stream, flush=True)
            while self.next_report <= done:
                self.next_report += self.REPORT_STEP

    def finish(self):
        self._clear()
        elapsed = time.time() - self.t0
        rate = self.lines / elapsed if elapsed > 0 else 0
        print(f"Génération terminée : {fmt_int(self.lines)} lignes en {fmt_duration(elapsed)} "
              f"({fmt_int(int(rate))} lignes/s)", file=self.stream, flush=True)

    def _status(self):
        done = min(self.lines / self.total, 1.0)
        elapsed = time.time() - self.t0
        filled = round(done * self.BAR_WIDTH)
        text = (f"  {'█' * filled}{'░' * (self.BAR_WIDTH - filled)} {done * 100:3.0f} % · "
                f"jour {self.days}/{self.n_days} ({self.day:%d/%m/%Y}) · "
                f"{fmt_int(self.lines)} lignes · {fmt_duration(elapsed)}")
        if 0 < done < 1:
            text += f" · reste ~{fmt_duration(elapsed * (1 - done) / done)}"
        return text

    def _draw(self):
        if self.live:
            width = shutil.get_terminal_size().columns
            self.stream.write("\r\033[K" + self._status()[:width - 1])
            self.stream.flush()
            self.drawn = True

    def _clear(self):
        if self.drawn:
            self.stream.write("\r\033[K")
            self.stream.flush()
            self.drawn = False


# --- Interface ------------------------------------------------------------------

def positive_int(text):
    try:
        value = int(text.replace(" ", "").replace("_", ""))
    except ValueError:
        raise ValueError("nombre entier attendu") from None
    if value < 1:
        raise ValueError("doit être au moins 1")
    return value


def iso_date(text):
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError("date attendue au format AAAA-MM-JJ") from None


def share(text):
    try:
        value = float(text.replace(",", ".").rstrip("%").strip())
    except ValueError:
        raise argparse.ArgumentTypeError("nombre attendu, par exemple 0.02 pour 2 %") from None
    if not 0 <= value <= SOURCES_MAX_SHARE:
        raise argparse.ArgumentTypeError(f"doit être compris entre 0 et {SOURCES_MAX_SHARE}")
    return value


def ask(prompt, parse, default=None):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        try:
            raw = input(f"{prompt}{suffix} : ").strip()
        except EOFError:
            raise SystemExit("\nSaisie interrompue.")
        if not raw and default is not None:
            raw = str(default)
        try:
            return parse(raw)
        except ValueError as exc:
            print(f"  Valeur invalide ({exc}), recommence.")


def parse_scenarios(text):
    text = text.strip().lower()
    if text in ("all", "tous"):
        return SCENARIOS[:]
    if text in ("none", "aucun", ""):
        return []
    chosen = [s.strip() for s in text.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in SCENARIOS]
    if unknown:
        raise argparse.ArgumentTypeError(f"scénario(s) inconnu(s) : {', '.join(unknown)} "
                                         f"(possibles : {', '.join(SCENARIOS)}, all, none)")
    return [s for s in SCENARIOS if s in chosen]


def yes_no(text):
    answer = text.strip().lower()
    if answer in ("o", "oui", "y", "yes"):
        return True
    if answer in ("n", "non", "no"):
        return False
    raise ValueError("réponds o ou n")


# Fichiers que le générateur produit (ou que macOS ajoute) : supprimés sans confirmation.
GENERATED_RX = re.compile(r"(-(?:%s)-\d{4}\.gz|^manifest-.+\.json|^\.DS_Store)$" % "|".join(MONTHS))


def clear_output(out_dir, sample, interactive):
    """Vide le dossier de sortie avant une nouvelle génération."""
    out = out_dir.resolve()
    if not out.exists():
        return
    script_dir = Path(__file__).resolve().parent
    if (out in (Path.home().resolve(), Path(out.anchor)) or script_dir.is_relative_to(out)
            or sample.resolve().is_relative_to(out)):
        raise SystemExit(f"Refus de vider {out} : il contient le script, l'exemple ou tes données. "
                         "Choisis un autre --out, ou utilise --keep-output.")
    items = sorted(out.iterdir())
    if not items:
        print(f"Dossier de sortie déjà vide : {out_dir}")
        return
    unexpected = [p for p in items if not (p.is_file() and GENERATED_RX.search(p.name))]
    if unexpected:
        names = ", ".join(p.name for p in unexpected[:5]) + (" …" if len(unexpected) > 5 else "")
        warning = f"{out_dir} contient {len(unexpected)} élément(s) qui ne viennent pas du générateur : {names}"
        if not interactive:
            raise SystemExit(f"{warning}\nRien n'a été supprimé : vide-le à la main ou utilise --keep-output.")
        if not ask(f"{warning}\nTout supprimer quand même ? (o/n)", yes_no, "n"):
            raise SystemExit("Génération annulée, rien n'a été supprimé.")
    size = 0
    for item in items:
        if item.is_dir() and not item.is_symlink():
            size += sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
            shutil.rmtree(item)
        else:
            size += item.lstat().st_size
            item.unlink()
    print(f"Dossier de sortie vidé : {len(items)} élément(s) supprimé(s), {size / 1e6:.1f} Mo ({out_dir})")


def find_default_sample():
    here = Path(__file__).resolve().parent
    candidates = sorted(here.glob("*log-*.gz")) or sorted(here.glob("*.gz"))
    return candidates[0] if candidates else None


def output_prefix(sample):
    m = re.match(r"^(.+)-(?:%s)-\d{4}\.gz$" % "|".join(MONTHS), sample.name)
    return m.group(1) if m else "access_log"


def main():
    parser = argparse.ArgumentParser(
        description="Génère des logs Apache réalistes à partir d'un fichier exemple.")
    parser.add_argument("--sample", type=Path, help="fichier exemple (.gz ou texte) ; par défaut le .gz du dossier du script")
    parser.add_argument("--days", type=positive_int, help="nombre de jours à générer")
    parser.add_argument("--lines-per-day", type=positive_int, help="nombre moyen de lignes par jour")
    parser.add_argument("--start", type=iso_date, help="premier jour (AAAA-MM-JJ) ; par défaut, la période finit hier")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "output",
                        help="dossier de sortie, vidé avant chaque génération (défaut : output/ à côté du script)")
    parser.add_argument("--keep-output", action="store_true",
                        help="ne vide pas le dossier de sortie (les fichiers des mêmes mois sont remplacés)")
    parser.add_argument("--seed", type=int, help="graine aléatoire, pour regénérer exactement les mêmes logs")
    parser.add_argument("--scenarios", type=parse_scenarios, default=SCENARIOS[:],
                        help=f"anomalies à injecter : all (défaut), none, ou liste parmi {', '.join(SCENARIOS)}")
    parser.add_argument("--invented-404-ratio", type=float, default=0.3,
                        help="part des 404 dont l'URL est inventée (défaut : 0.3)")
    parser.add_argument("--social-share", type=share, default=SOCIAL_SHARE,
                        help=f"part du volume venue des réseaux sociaux : aperçus de liens et visites "
                             f"(défaut : {SOCIAL_SHARE} ; 0 pour désactiver)")
    parser.add_argument("--paid-share", type=share, default=PAID_SHARE,
                        help=f"part du volume venue du trafic payant : robots publicitaires et visites "
                             f"(défaut : {PAID_SHARE} ; 0 pour désactiver)")
    args = parser.parse_args()
    if args.social_share + args.paid_share > SOURCES_MAX_SHARE:
        parser.error(f"--social-share + --paid-share ne doivent pas dépasser {SOURCES_MAX_SHARE}")
    interactive = sys.stdin.isatty()

    sample = args.sample or find_default_sample()
    if not sample or not sample.exists():
        raise SystemExit("Fichier exemple introuvable : précise-le avec --sample.")
    if not interactive and (args.days is None or args.lines_per_day is None):
        raise SystemExit("Mode non interactif : --days et --lines-per-day sont obligatoires.")

    t0 = time.time()
    print(f"Analyse de {sample.name}...", flush=True)
    profile = Profile(sample)
    print(profile.summary())
    print(f"(analyse faite en {fmt_duration(time.time() - t0)})\n")

    days = args.days or ask("Nombre de jours de logs", positive_int)
    per_day = args.lines_per_day or ask("Nombre moyen de lignes par jour", positive_int)
    default_start = date.today() - timedelta(days=days)
    start = args.start
    if start is None:
        start = ask("Premier jour (AAAA-MM-JJ)", iso_date, default_start) if interactive else default_start
    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    end = start + timedelta(days=days - 1)
    total = days * per_day
    bytes_per_line = sample.stat().st_size / max(profile.n_lines, 1) if sample.suffix == ".gz" else 25

    args.out.mkdir(parents=True, exist_ok=True)
    progress = Progress(total, days)
    writer = MonthlyWriter(args.out, output_prefix(sample), sample, log=progress.message)
    print(f"\nPériode : du {start:%d/%m/%Y} au {end:%d/%m/%Y} ({days} jours)")
    megabytes = total * bytes_per_line / 1e6
    print(f"Volume  : {fmt_int(total)} lignes (≈ {megabytes:.{0 if megabytes >= 10 else 1}f} Mo compressés)")
    print(f"Scénarios : {', '.join(args.scenarios) or 'aucun'} — graine : {seed}")
    print(f"Sources : réseaux sociaux {args.social_share * 100:g} % du volume "
          f"(≈ {fmt_int(round(total * args.social_share))} lignes), "
          f"trafic payant {args.paid_share * 100:g} % (≈ {fmt_int(round(total * args.paid_share))} lignes)")
    if args.keep_output:
        month = date(start.year, start.month, 1)
        while month <= end:
            path = writer.path_for(month)
            if path.exists():
                print(f"  ! {path.name} existe déjà et sera remplacé")
            month = (month + timedelta(days=32)).replace(day=1)
    else:
        clear_output(args.out, sample, interactive)
    print("Génération...", flush=True)

    gen = Generator(profile, seed, args.invented_404_ratio)
    injections, incident, sources = gen.run(days, per_day, start, args.scenarios, writer, progress,
                                            args.social_share, args.paid_share)

    # Manifeste : paramètres, volumes et vérité terrain des anomalies injectées.
    scenarios = []
    for sc in injections:
        tag = writer.tags.get(sc["type"], {"lines": 0, "first": None, "last": None})
        scenarios.append({
            "type": sc["type"], "description": sc["description"],
            "dates": sorted((start + timedelta(days=di)).isoformat() for di in sc["events_by_day"]),
            "start": datetime.fromtimestamp(tag["first"], TZ).isoformat() if tag["first"] else None,
            "end": datetime.fromtimestamp(tag["last"], TZ).isoformat() if tag["last"] else None,
            "lines": tag["lines"], "ips": sc["ips"], "user_agent": sc["user_agent"]})
    if incident:
        day = start + timedelta(days=incident["day"])
        scenarios.append({
            "type": "incident",
            "description": f"Incident serveur : ~{incident['rate']:.0%} des requêtes de la fenêtre "
                           "reçoivent une erreur 5xx (les redirections échouées ne sont plus suivies).",
            "dates": [day.isoformat()],
            "start": iso_time(day, incident["start"]), "end": iso_time(day, incident["end"]),
            "lines": sum(incident["statuses"].values()), "status_codes": dict(incident["statuses"])})
    scenarios.sort(key=lambda s: s["start"] or "")

    manifest = {
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "sample": sample.name,
        "parameters": {"days": days, "lines_per_day": per_day, "start": start.isoformat(), "end": end.isoformat(),
                       "seed": seed, "scenarios": args.scenarios, "invented_404_ratio": args.invented_404_ratio,
                       "social_share": args.social_share, "paid_share": args.paid_share},
        "total_lines": sum(f["lines"] for f in writer.files),
        "files": writer.files,
        "daily_lines": writer.daily,
        "status_codes": dict(writer.statuses.most_common()),
        "families": dict(writer.families.most_common()),
        "scenarios": scenarios,
        "traffic_sources": sources.summary(writer),
    }
    manifest_path = args.out / f"manifest-{start.isoformat()}_{end.isoformat()}.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nTerminé en {fmt_duration(time.time() - t0)} (analyse comprise) : "
          f"{fmt_int(manifest['total_lines'])} lignes.")
    for sc in scenarios:
        print(f"  • {sc['type']:<15} {sc['start']} → {sc['end']}  ({fmt_int(sc['lines'])} lignes)")
    for key, label in (("social", "réseaux sociaux"), ("paid", "trafic payant")):
        src = manifest["traffic_sources"][key]
        if src["lines"]:
            print(f"  • {label:<15} {fmt_int(src['lines'])} lignes : robots {fmt_int(src['robots']['lines'])}, "
                  f"{fmt_int(src['visits']['visits'])} visites ({fmt_int(src['visits']['lines'])} lignes)")
    print(f"Manifeste (vérité terrain) : {manifest_path}")
    print(f"Pour regénérer à l'identique : --seed {seed}")


if __name__ == "__main__":
    main()
