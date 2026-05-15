"""
Auto-tagging de arquivos de áudio a partir do nome do arquivo.

Padrão esperado: "Artista - Título.ext"
Exemplos:
  "Robin Schulz - Sight (Original Mix).mp3"   → artist="Robin Schulz", title="Sight (Original Mix)"
  "Reynn, SirGio8A - Say Anything (Extended Mix).flac"
"""
import base64
import json
import os
import random
import re
import time
import urllib.request
import urllib.parse
from pathlib import Path


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_filename(path: str) -> tuple[str | None, str | None]:
    """
    Extrai artist e title do nome do arquivo.
    Divide no PRIMEIRO ' - ' encontrado.
    Retorna (None, None) se o padrão não for encontrado.
    """
    stem = Path(path).stem
    if ' - ' not in stem:
        return None, None
    artist, title = stem.split(' - ', 1)
    return artist.strip() or None, title.strip() or None


# ── MusicBrainz ───────────────────────────────────────────────────────────────

# Sufixos que não identificam a gravação em si — removidos antes da busca
_VERSION_RE = re.compile(
    r'\s*\((original mix|extended mix|extended|radio edit|radio version|'
    r'club mix|club version|album version|original|edit|remaster|remastered)\)\s*$',
    re.IGNORECASE,
)


def _clean_title(title: str) -> str:
    return _VERSION_RE.sub('', title).strip()


# Ruído típico de sites de download/blog nos nomes de arquivo:
#   "[www.electrobuzz.net]", "(Free Download)", "[320kbps]", "http://site.com"
_TITLE_JUNK_RE = re.compile(
    r'\s*[\[\(](?:'
    r'https?://[^\]\)\s]*|'                                                                          # http(s):// URL
    r'ftp://[^\]\)\s]*|'                                                                             # ftp:// URL
    r'www\.[a-zA-Z0-9][a-zA-Z0-9\-]*(?:\.[a-zA-Z0-9\-]+)*\.[a-zA-Z]{2,}[^\]\)]*|'                 # www.site.com
    r'[a-zA-Z0-9][a-zA-Z0-9\-]*(?:\.[a-zA-Z0-9\-]+)*\.(?:com|net|org|io|fm|me|co|club|info|'      # site.com/net/...
    r'de|uk|fr|pl|ru|nl|br|pt|es|it|se|no|dk|fi|at|be|ch)[^\]\)]*|'
    r'(?:free\s*)?downloads?|'                                                                       # (Free Download)
    r'\d{2,3}\s*kbps|'                                                                              # (320kbps)
    r'@[a-zA-Z0-9_]+|'                                                                              # @username
    r'(?:hq|hd)\b'                                                                                  # (HQ), (HD)
    r')[\]\)]'
    r'|\s+(?:https?://|www\.)\S+$',                                                                 # URL solta no fim
    re.IGNORECASE,
)


def _clean_filename_title(title: str) -> str:
    """Remove ruído de sites de download/blog do título parseado do arquivo."""
    cleaned = _TITLE_JUNK_RE.sub('', title).strip().strip('-_ ').strip()
    return cleaned or title


_MIN_SCORE = 80  # confiança mínima do MusicBrainz para aceitar o match


def _query_musicbrainz(query: str) -> list[dict]:
    params = urllib.parse.urlencode({'query': query, 'fmt': 'json', 'limit': 10})
    req = urllib.request.Request(
        f'https://musicbrainz.org/ws/2/recording/?{params}',
        headers={'User-Agent': 'EDMAnalyzer/1.0 (edm-search)'},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    return data.get('recordings', [])


def _year_from_recording(rec: dict) -> int | None:
    """Extrai o ano: first-release-date tem prioridade; releases como fallback."""
    date = rec.get('first-release-date', '')
    if date and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    candidates: list[int] = []
    for rel in rec.get('releases', []):
        rdate = rel.get('date', '')
        if rdate and len(rdate) >= 4 and rdate[:4].isdigit():
            candidates.append(int(rdate[:4]))
    return min(candidates) if candidates else None


def _fetch_musicbrainz_data(artist: str, title: str) -> dict:
    """
    Busca no MusicBrainz e retorna
        {'year': str, 'release_mbids': [str], 'release_group_mbids': [str]}.

    - Aceita só recordings com score >= _MIN_SCORE (descarta matches falsos).
    - Coleta TODOS os release/release-group MBIDs dos matches confiáveis,
      para que fetch_cover_art possa cair em cascata até achar capa.
    - O ano vem do recording de maior score (first-release-date em prioridade).
    """
    search_title = _clean_title(title)
    primary_artist = artist.split(',')[0].split('&')[0].split(' feat')[0].strip()

    queries = [f'artist:"{artist}" recording:"{search_title}"']
    if primary_artist and primary_artist.lower() != artist.lower():
        queries.append(f'artist:"{primary_artist}" recording:"{search_title}"')

    recordings: list[dict] = []
    for q in queries:
        recordings = _query_musicbrainz(q)
        if any(r.get('score', 0) >= _MIN_SCORE for r in recordings):
            break

    confident = sorted(
        [r for r in recordings if r.get('score', 0) >= _MIN_SCORE],
        key=lambda r: r.get('score', 0),
        reverse=True,
    )

    # Ano: usa o recording de maior score (mais provável de ser o correto).
    # Não fazemos min() entre recordings diferentes — isso misturaria anos
    # de músicas distintas que passaram no threshold por coincidência.
    best_year: int | None = None
    for rec in confident:
        y = _year_from_recording(rec)
        if y is not None:
            best_year = y
            break

    # MBIDs: coleta de todos os matches confiáveis (para cascata de capa).
    release_mbids: list[str] = []
    release_group_mbids: list[str] = []
    seen_rel: set[str] = set()
    seen_rg:  set[str] = set()

    for rec in confident:
        for rel in rec.get('releases', []):
            rid = rel.get('id')
            if rid and rid not in seen_rel:
                seen_rel.add(rid)
                release_mbids.append(rid)
            rgid = (rel.get('release-group') or {}).get('id')
            if rgid and rgid not in seen_rg:
                seen_rg.add(rgid)
                release_group_mbids.append(rgid)

    result: dict = {}
    if best_year:
        result['year'] = str(best_year)
    if release_mbids:
        result['release_mbids'] = release_mbids
    if release_group_mbids:
        result['release_group_mbids'] = release_group_mbids
    return result


def fetch_year_musicbrainz(artist: str, title: str) -> str | None:
    try:
        return _fetch_musicbrainz_data(artist, title).get('year')
    except Exception:
        return None


def fetch_year(artist: str, title: str) -> str | None:
    return fetch_year_musicbrainz(artist, title)


# ── iTunes Search API ─────────────────────────────────────────────────────────

def _fetch_itunes_data(artist: str, title: str) -> dict:
    """
    Busca na iTunes Search API (gratuita, sem chave).
    Retorna {'year': str, 'cover_url': str} — campos ausentes se não encontrado.
    """
    search_title = _clean_title(title)
    params = urllib.parse.urlencode({
        'term':   f'{artist} {search_title}',
        'media':  'music',
        'entity': 'song',
        'limit':  5,
    })
    req = urllib.request.Request(
        f'https://itunes.apple.com/search?{params}',
        headers={'User-Agent': 'EDMAnalyzer/1.0 (edm-search)'},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return {}

    results = data.get('results', [])
    if not results:
        return {}

    r = results[0]
    result: dict = {}

    release_date = r.get('releaseDate', '')
    if release_date and len(release_date) >= 4 and release_date[:4].isdigit():
        result['year'] = release_date[:4]

    artwork = r.get('artworkUrl100', '')
    if artwork:
        result['cover_url'] = re.sub(r'\d+x\d+bb', '600x600bb', artwork)

    genre = r.get('primaryGenreName', '')
    if genre and genre.lower() != 'music':
        result['genre'] = genre

    return result


# ── Discogs API ───────────────────────────────────────────────────────────────

def _fetch_discogs_data(artist: str, title: str) -> dict:
    """
    Busca na Discogs API.
    Requer DISCOGS_KEY e DISCOGS_SECRET no .env — retorna {} se ausentes.
    Retorna {'year': str, 'cover_url': str} — campos ausentes se não encontrado.
    """
    from config import DISCOGS_KEY, DISCOGS_SECRET
    if not DISCOGS_KEY or not DISCOGS_SECRET:
        return {}

    search_title = _clean_title(title)
    params = urllib.parse.urlencode({
        'artist':   artist,
        'track':    search_title,
        'type':     'release',
        'per_page': 5,
        'page':     1,
    })
    req = urllib.request.Request(
        f'https://api.discogs.com/database/search?{params}',
        headers={
            'User-Agent':    'EDMAnalyzer/1.0 (edm-search)',
            'Authorization': f'Discogs key={DISCOGS_KEY}, secret={DISCOGS_SECRET}',
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return {}

    results = data.get('results', [])
    if not results:
        return {}

    r = results[0]
    result: dict = {}

    year = r.get('year')
    if year:
        result['year'] = str(year)

    cover = r.get('cover_image', '')
    # Discogs usa spacer.gif quando não há imagem
    if cover and not cover.endswith('spacer.gif'):
        result['cover_url'] = cover

    return result


# ── Deezer API ───────────────────────────────────────────────────────────────

def _fetch_deezer_data(artist: str, title: str) -> dict:
    """
    Deezer Search API (gratuita, sem chave).
    Retorna {'cover_url': str, 'year': str} — campos ausentes se não encontrado.
    cover_url é album.cover_xl (1000×1000), matched por artista+faixa.
    """
    search_title = _clean_title(title)
    params = urllib.parse.urlencode({
        'q':     f'artist:"{artist}" track:"{search_title}"',
        'limit': 3,
    })
    req = urllib.request.Request(
        f'https://api.deezer.com/search?{params}',
        headers={'User-Agent': 'EDMAnalyzer/1.0 (edm-search)'},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return {}

    tracks = data.get('data', [])
    if not tracks:
        return {}

    result: dict = {}
    album = tracks[0].get('album', {})

    cover_xl = album.get('cover_xl', '')
    if cover_xl:
        result['cover_url'] = cover_xl

    album_id = album.get('id')
    if album_id:
        try:
            req2 = urllib.request.Request(
                f'https://api.deezer.com/album/{album_id}',
                headers={'User-Agent': 'EDMAnalyzer/1.0 (edm-search)'},
            )
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                alb = json.loads(resp2.read().decode())
            rd = alb.get('release_date', '')
            if rd and len(rd) >= 4 and rd[:4].isdigit():
                result['year'] = rd[:4]
            for g in alb.get('genres', {}).get('data', []):
                name = g.get('name', '').strip()
                if name:
                    result['genre'] = name
                    break
        except Exception:
            pass

    return result


# ── Last.fm genre ─────────────────────────────────────────────────────────────

# Tags do Last.fm que não representam gênero musical
_LASTFM_SKIP_RE = re.compile(
    r'seen.live|favourit|favorit|loved?\b|amazing|awesome|beautiful|'
    r'\bbest\b|\bclassic\b|essential|\bgood\b|\bgreat\b|playlist|'
    r'^\d+s?$|\bunder\s*\d+|all.time',
    re.IGNORECASE,
)


def _fetch_lastfm_genre(artist: str, title: str) -> str | None:
    """
    Busca o gênero via Last.fm track.getTopTags (requer LASTFM_API_KEY no .env).
    Retorna o primeiro tag com votos suficientes que pareça um gênero real.
    """
    from config import LASTFM_API_KEY
    if not LASTFM_API_KEY:
        return None
    params = urllib.parse.urlencode({
        'method':      'track.getTopTags',
        'artist':      artist,
        'track':       _clean_title(title),
        'api_key':     LASTFM_API_KEY,
        'format':      'json',
        'autocorrect': 1,
    })
    req = urllib.request.Request(
        f'https://ws.audioscrobbler.com/2.0/?{params}',
        headers={'User-Agent': 'EDMAnalyzer/1.0 (edm-search)'},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return None
    for tag in data.get('toptags', {}).get('tag', []):
        name  = tag.get('name', '').strip()
        count = int(tag.get('count', 0))
        if count < 5 or len(name) < 3:
            continue
        if _LASTFM_SKIP_RE.search(name):
            continue
        return name.title()
    return None


# ── Web scraper (sem chave de API) ────────────────────────────────────────────

_YEAR_RE = re.compile(r'\b(19[8-9]\d|20[0-3]\d)\b')

_SCRAPE_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/125.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}

# Padrões que indicam que o Google está bloqueando a requisição
_GOOGLE_BLOCK_RE = re.compile(
    r'(?:unusual traffic|captcha|/sorry/|recaptcha|detected automated)',
    re.IGNORECASE,
)

_HTTP_RETRY_ATTEMPTS = 3
_HTTP_RETRY_BASE_DELAY = 2.0  # segundos; dobra a cada tentativa (backoff exponencial)


def _http_get(url: str, extra_headers: dict | None = None) -> str:
    headers = {**_SCRAPE_HEADERS, **(extra_headers or {})}
    last_exc: Exception | None = None
    for attempt in range(_HTTP_RETRY_ATTEMPTS):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                html = resp.read().decode('utf-8', errors='replace')
            if _GOOGLE_BLOCK_RE.search(html):
                raise RuntimeError('Google bloqueou a requisição (CAPTCHA/rate-limit)')
            return html
        except Exception as exc:
            last_exc = exc
            if attempt < _HTTP_RETRY_ATTEMPTS - 1:
                wait = _HTTP_RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1)
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def _find_in_json(obj, *, depth: int = 0) -> list:
    """
    Busca recursiva por lista de tracks no __NEXT_DATA__ do Beatport.
    Reconhece tracks como: lista de dicts com 'name' + ('image' ou 'artists').
    """
    if depth > 8:
        return []
    if (isinstance(obj, list) and obj
            and isinstance(obj[0], dict)
            and 'name' in obj[0]
            and ('image' in obj[0] or 'artists' in obj[0])):
        return obj
    if isinstance(obj, dict):
        for v in obj.values():
            r = _find_in_json(v, depth=depth + 1)
            if r:
                return r
    if isinstance(obj, list):
        for item in obj:
            r = _find_in_json(item, depth=depth + 1)
            if r:
                return r
    return []


def _scrape_beatport(artist: str, title: str) -> dict:
    """
    Extrai ano e capa do Beatport via __NEXT_DATA__ JSON embutido na página.
    Sem chave de API. Melhor cobertura para EDM.
    """
    search_title = _clean_title(title)
    q = urllib.parse.quote_plus(f'{artist} {search_title}')
    html = _http_get(f'https://www.beatport.com/search/tracks?q={q}')

    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html, re.DOTALL,
    )
    if not m:
        return {}

    tracks = _find_in_json(json.loads(m.group(1)))
    if not tracks:
        return {}

    track = tracks[0]
    result: dict = {}

    for key in ('publish_date', 'new_release_date', 'release_date', 'date'):
        val = str(track.get(key) or '')
        yr = _YEAR_RE.search(val)
        if yr:
            result['year'] = yr.group()
            break

    image = track.get('image') or track.get('artwork') or {}
    if isinstance(image, dict):
        url = image.get('dynamic_uri') or image.get('uri') or image.get('url', '')
        if url:
            url = re.sub(r'\{w\}', '500', url)
            url = re.sub(r'\{h\}', '500', url)
            result['cover_url'] = url
    elif isinstance(image, str) and image.startswith('http'):
        result['cover_url'] = image

    # Gênero — preferir sub_genre (mais específico para EDM)
    for key in ('sub_genres', 'subgenres', 'genres'):
        items = track.get(key) or []
        if isinstance(items, list) and items:
            g = items[0]
            name = (g.get('name') if isinstance(g, dict) else str(g)).strip()
            if name:
                result['genre'] = name
                break

    return result


def _scrape_ddg_year(artist: str, title: str) -> str | None:
    """Extrai ano dos snippets do DuckDuckGo HTML (sem chave, sem JS)."""
    search_title = _clean_title(title)
    q = urllib.parse.quote_plus(f'{artist} {search_title} release year')
    html = _http_get(f'https://html.duckduckgo.com/html/?q={q}')
    for snippet in re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL):
        clean = re.sub(r'<[^>]+>', '', snippet)
        m = _YEAR_RE.search(clean)
        if m:
            return m.group()
    return None


def _scrape_ddg_image(artist: str, title: str) -> str | None:
    """Extrai URL de capa do DuckDuckGo Images via token vqd."""
    search_title = _clean_title(title)
    q_str = f'{artist} {search_title} cover art'
    q = urllib.parse.quote_plus(q_str)

    # Passo 1: obtém token vqd da página de busca
    html = _http_get(f'https://duckduckgo.com/?q={q}&iax=images&ia=images')
    m = re.search(r'vqd=["\']{0,1}([\d\-a-zA-Z%_]+)["\']{0,1}', html)
    if not m:
        return None
    vqd = m.group(1)

    # Passo 2: busca imagens com o token
    params = urllib.parse.urlencode({
        'q': q_str, 'vqd': vqd, 'o': 'json',
        'p': '1', 's': '0', 'u': 'bing', 'f': ',,,,', 'l': 'us-en',
    })
    data = json.loads(_http_get(f'https://duckduckgo.com/i.js?{params}'))
    results = data.get('results', [])
    return results[0].get('image') if results else None


def _scrape_google_year(artist: str, title: str) -> str | None:
    """
    Extrai ano de lançamento via Google Search.
    Procura no knowledge panel ("Released: YYYY") e nos snippets de resultado.
    """
    search_title = _clean_title(title)
    q = urllib.parse.quote_plus(f'"{artist}" "{search_title}" release year')
    try:
        html = _http_get(f'https://www.google.com/search?q={q}&hl=en&gl=us')
        # Knowledge panel: "Released: 2019" ou "Release date: ..."
        m = re.search(
            r'(?:released?|release\s*date)[^<\d]{0,40}(\b(?:19[89]\d|20[0-3]\d)\b)',
            html, re.IGNORECASE,
        )
        if m:
            return m.group(1)
        # Fallback: primeiro ano em divs de snippet do Google
        for snippet in re.findall(r'<div[^>]*>([^<]{10,300})</div>', html):
            yr = _YEAR_RE.search(snippet)
            if yr:
                candidate = int(yr.group())
                if 1980 <= candidate <= 2030:
                    return yr.group()
    except Exception:
        pass
    return None


# CDNs de plataformas de música confiáveis — única origem aceita para capas do Google Images.
# Impede que imagens não relacionadas (de outros sites) sejam gravadas no arquivo.
_TRUSTED_COVER_DOMAINS = re.compile(
    r'(?:'
    r'beatport\.com|'
    r'amazon\.com|amazon\.[a-z]{2,3}|'           # loja Amazon (qualquer país)
    r'i\.scdn\.co|'                                # CDN do Spotify
    r'soundcloud\.com|'
    r'cdns-images\.dzcdn\.net|'                    # CDN do Deezer
    r'is\d+\.mzstatic\.com|a\d+\.mzstatic\.com|'  # CDN da Apple/iTunes
    r'img\.discogs\.com|discogs\.com|'
    r'junodownload\.com|juno\.co\.uk|'             # Juno Download
    r'traxsource\.com|'                            # Traxsource
    r'f4\.bcbits\.com|bandcamp\.com|'              # Bandcamp
    r'boomkat\.com'                                # Boomkat
    r')',
    re.IGNORECASE,
)


def _scrape_google_cover(artist: str, title: str) -> str | None:
    """
    Busca URL de capa via Google Images.
    Só retorna URLs de CDNs de plataformas musicais confiáveis (_TRUSTED_COVER_DOMAINS).
    Se o Google não retornar nenhuma imagem de fonte confiável, retorna None.
    """
    search_title = _clean_title(title)
    queries = [
        (f'{artist} {search_title} cover '
         'site:beatport.com OR site:amazon.com OR site:open.spotify.com OR site:soundcloud.com'),
        f'{artist} {search_title} album cover art',
    ]
    for q_str in queries:
        q = urllib.parse.quote_plus(q_str)
        try:
            html = _http_get(f'https://www.google.com/search?q={q}&tbm=isch&hl=en&gl=us')
            urls = re.findall(r'"ou"\s*:\s*"(https?://[^"]+)"', html)
            for url in urls:
                if (re.search(r'\.(jpg|jpeg|png|webp)', url, re.IGNORECASE)
                        and _TRUSTED_COVER_DOMAINS.search(url)):
                    return url
        except Exception:
            pass
    return None


def _scrape_web(artist: str, title: str) -> dict:
    """
    Scraper sem chave de API: Beatport (ano + capa) → DuckDuckGo texto (só ano).
    DDG Images removido — retornava capas sem relação com a faixa.
    Retorna {'year': str, 'cover_url': str} — campos ausentes se não encontrado.
    """
    result: dict = {}

    try:
        result.update(_scrape_beatport(artist, title))
    except Exception:
        pass

    if 'year' not in result:
        try:
            year = _scrape_ddg_year(artist, title)
            if year:
                result['year'] = year
        except Exception:
            pass

    return result


# ── Orquestrador ──────────────────────────────────────────────────────────────
#
#  ANO:    Google → Beatport → MusicBrainz → Deezer → iTunes → DDG texto → Discogs
#  CAPA:   Google Images → Beatport → Deezer → iTunes → MusicBrainz → Discogs
#  GÊNERO: Beatport → Deezer → iTunes → Last.fm → Discogs
#
def _fetch_all_metadata(artist: str, title: str) -> dict:
    """Retorna {'year': str|None, 'cover_bytes': bytes|None, 'genre': str|None}."""
    year:        str   | None = None
    cover_bytes: bytes | None = None
    genre:       str   | None = None

    # 1. Google — knowledge panel (ano) + Images com filtro Beatport/Amazon/Spotify/SoundCloud (capa)
    try:
        year = _scrape_google_year(artist, title)
    except Exception:
        pass
    try:
        goog_url = _scrape_google_cover(artist, title)
        if goog_url:
            cover_bytes = _try_cover(goog_url)
    except Exception:
        pass

    # 2. Beatport — melhor para EDM (ano + gênero precisos + capa)
    if not year or not cover_bytes or not genre:
        try:
            bp = _scrape_beatport(artist, title)
            if not year:
                year = bp.get('year')
            if not genre:
                genre = bp.get('genre')
            if not cover_bytes and bp.get('cover_url'):
                cover_bytes = _try_cover(bp['cover_url'])
        except Exception:
            pass

    # 3. Deezer — capa confiável + ano + gênero
    if not cover_bytes or not genre or not year:
        try:
            dz = _fetch_deezer_data(artist, title)
            if not year:
                year = dz.get('year')
            if not genre:
                genre = dz.get('genre')
            if not cover_bytes and dz.get('cover_url'):
                cover_bytes = _try_cover(dz['cover_url'])
        except Exception:
            pass

    # 4. iTunes — capa + ano + gênero como fallback
    if not year or not cover_bytes or not genre:
        try:
            it = _fetch_itunes_data(artist, title)
            if not year:
                year = it.get('year')
            if not genre:
                genre = it.get('genre')
            if not cover_bytes and it.get('cover_url'):
                cover_bytes = _try_cover(it['cover_url'])
        except Exception:
            pass

    # 5. MusicBrainz — Cover Art Archive + ano
    if not year or not cover_bytes:
        try:
            mb = _fetch_musicbrainz_data(artist, title)
            if not year:
                year = mb.get('year')
            if not cover_bytes:
                rel_mbids = mb.get('release_mbids', [])
                rg_mbids  = mb.get('release_group_mbids', [])
                if rel_mbids or rg_mbids:
                    cover_bytes = fetch_cover_art(rel_mbids, rg_mbids)
        except Exception:
            pass

    # 6. Last.fm — gênero via tags crowdsourced (requer LASTFM_API_KEY)
    if not genre:
        try:
            genre = _fetch_lastfm_genre(artist, title)
        except Exception:
            pass

    # 7. DuckDuckGo texto — só para ano
    if not year:
        try:
            year = _scrape_ddg_year(artist, title)
        except Exception:
            pass

    # 8. Discogs — último recurso
    if not year or not cover_bytes or not genre:
        try:
            dc = _fetch_discogs_data(artist, title)
            if not year:
                year = dc.get('year')
            if not genre:
                genre = dc.get('genre')
            if not cover_bytes and dc.get('cover_url'):
                cover_bytes = _try_cover(dc['cover_url'])
        except Exception:
            pass

    return {'year': year, 'cover_bytes': cover_bytes, 'genre': genre}


# ── Cover Art Archive ─────────────────────────────────────────────────────────

def _try_cover(url: str) -> bytes | None:
    req = urllib.request.Request(
        url, headers={'User-Agent': 'EDMAnalyzer/1.0 (edm-search)'},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
    except Exception:
        return None


def fetch_cover_art(
    release_mbids: list[str] | str,
    release_group_mbids: list[str] | None = None,
) -> bytes | None:
    """
    Baixa a capa frontal via Cover Art Archive (gratuito, sem chave).

    Tenta cada release_mbid em ordem e, se nenhum tiver capa, cai para
    release-group (cobertura bem maior). Aceita string única para compat.
    """
    if isinstance(release_mbids, str):
        release_mbids = [release_mbids]
    for mbid in release_mbids or []:
        data = _try_cover(f'https://coverartarchive.org/release/{mbid}/front-500')
        if data:
            return data
    for mbid in release_group_mbids or []:
        data = _try_cover(f'https://coverartarchive.org/release-group/{mbid}/front-500')
        if data:
            return data
    return None


def _image_mime(data: bytes) -> str:
    if data[:4] == b'\x89PNG':
        return 'image/png'
    return 'image/jpeg'


def write_cover(path: str, image_bytes: bytes) -> bool:
    """Grava a capa no arquivo de áudio. Suporta MP3, FLAC, M4A, OGG, AIFF."""
    ext  = Path(path).suffix.lower()
    mime = _image_mime(image_bytes)
    try:
        if ext in ('.mp3', '.wav'):
            from mutagen.id3 import ID3, APIC
            from mutagen.id3 import error as ID3Error
            try:
                tags = ID3(path)
            except ID3Error:
                tags = ID3()
            tags.delall('APIC')
            tags.add(APIC(encoding=3, mime=mime, type=3, desc='Cover', data=image_bytes))
            tags.save(path)

        elif ext == '.flac':
            from mutagen.flac import FLAC, Picture
            audio = FLAC(path)
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.width = pic.height = pic.depth = pic.colors = 0
            pic.data = image_bytes
            audio.clear_pictures()
            audio.add_picture(pic)
            audio.save()

        elif ext == '.m4a':
            from mutagen.mp4 import MP4, MP4Cover
            audio = MP4(path)
            fmt = MP4Cover.FORMAT_PNG if 'png' in mime else MP4Cover.FORMAT_JPEG
            if audio.tags is None:
                audio.add_tags()
            audio.tags['covr'] = [MP4Cover(image_bytes, imageformat=fmt)]
            audio.save()

        elif ext == '.ogg':
            from mutagen.oggvorbis import OggVorbis
            from mutagen.flac import Picture
            audio = OggVorbis(path)
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.width = pic.height = pic.depth = pic.colors = 0
            pic.data = image_bytes
            encoded = base64.b64encode(pic.write()).decode('ascii')
            audio['metadata_block_picture'] = [encoded]
            audio.save()

        elif ext == '.aiff':
            from mutagen.aiff import AIFF
            from mutagen.id3 import APIC
            audio = AIFF(path)
            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall('APIC')
            audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc='Cover', data=image_bytes))
            audio.save()

        else:
            return False

        return True
    except Exception:
        return False


# ── Escrita de tags ───────────────────────────────────────────────────────────

def write_tags(
    path: str,
    artist: str,
    title: str,
    year: str | None,
    genre: str | None = None,
    dry_run: bool = False,
) -> dict:
    result = {
        'path':          path,
        'file':          Path(path).name,
        'artist':        artist,
        'title':         title,
        'year':          year  or '—',
        'genre':         genre or '—',
        'written':       False,
        'cover_written': False,
        'error':         None,
    }
    if dry_run:
        result['written'] = True
        return result
    try:
        from mutagen import File as MFile
        audio = MFile(path, easy=True)
        if audio is None:
            result['error'] = 'formato não suportado'
            return result
        if audio.tags is None:
            audio.add_tags()
        audio.tags['artist'] = artist
        audio.tags['title']  = title
        if year:
            audio.tags['date']  = year
        if genre:
            audio.tags['genre'] = genre
        audio.save()
        result['written'] = True
    except Exception as e:
        result['error'] = str(e)
    return result


# ── Pipeline público ──────────────────────────────────────────────────────────

def tag_file(
    path: str,
    dry_run: bool = False,
    fetch_year_online: bool = True,
    fetch_cover: bool = True,
    fetch_genre: bool = True,
) -> dict:
    """
    Pipeline completo para um arquivo:
      1. Parse do nome → artist, title
      2. Busca de metadados: MusicBrainz → iTunes → Discogs
      3. Escrita das tags ID3/Vorbis no arquivo
      4. Download e gravação da capa

    Retorna dict com 'file', 'artist', 'title', 'year', 'written',
    'cover_written', 'error'.
    """
    artist, title = parse_filename(path)
    if not artist or not title:
        return {
            'path':          path,
            'file':          Path(path).name,
            'artist':        None,
            'title':         None,
            'year':          None,
            'written':       False,
            'cover_written': False,
            'error':         'nome não segue o padrão "Artista - Título.ext"',
        }

    title = _clean_filename_title(title)

    year:        str   | None = None
    cover_bytes: bytes | None = None
    genre:       str   | None = None

    if fetch_year_online or fetch_cover or fetch_genre:
        meta        = _fetch_all_metadata(artist, title)
        year        = meta['year']        if fetch_year_online else None
        cover_bytes = meta['cover_bytes'] if fetch_cover       else None
        genre       = meta['genre']       if fetch_genre       else None

    result = write_tags(path, artist, title, year, genre=genre, dry_run=dry_run)

    if fetch_cover and cover_bytes:
        result['cover_preview'] = cover_bytes
        if not dry_run and result.get('written'):
            result['cover_written'] = write_cover(path, cover_bytes)

    return result


def tag_folder(
    folder: str,
    dry_run: bool = False,
    fetch_year_online: bool = True,
    fetch_cover: bool = True,
    fetch_genre: bool = True,
    extensions: list[str] | None = None,
) -> list[dict]:
    """Taga todos os arquivos de áudio de uma pasta."""
    from config import SUPPORTED_FORMATS
    exts  = set(extensions or SUPPORTED_FORMATS)
    paths = [
        os.path.join(folder, f)
        for f in sorted(os.listdir(folder))
        if Path(f).suffix.lower() in exts
    ]
    results = []
    for i, p in enumerate(paths):
        results.append(tag_file(
            p, dry_run=dry_run,
            fetch_year_online=fetch_year_online,
            fetch_cover=fetch_cover,
            fetch_genre=fetch_genre,
        ))
        # Pausa entre arquivos para não acionar rate limit do Google.
        # Jitter aleatório (2–4 s) torna o padrão menos detectável como bot.
        if i < len(paths) - 1:
            time.sleep(2 + random.uniform(0, 2))
    return results
