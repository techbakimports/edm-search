"""
Enriquecimento de metadados via Last.fm + Spotify.
Lê artist/title dos metadados do arquivo e busca tags/gêneros externos.
"""
import base64
import json
import time
import urllib.request
import urllib.parse

from config import LASTFM_API_KEY, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

# (tag lastfm lowercase) → (genre, subgenre) da taxonomia do projeto
# Ordenado do mais específico para o mais genérico dentro de cada grupo
_TAG_MAP = [
    # Ambient / Downtempo
    ("drone ambient",            ("Ambient",       "Drone")),
    ("dark ambient",             ("Ambient",       "Dark Ambient")),
    ("ambient",                  ("Ambient",       "Ambient")),
    ("trip-hop",                 ("Downtempo",     "Trip Hop")),
    ("trip hop",                 ("Downtempo",     "Trip Hop")),
    ("dub techno",               ("Downtempo",     "Dub Techno")),
    ("electronica",              ("Downtempo",     "Electronica")),
    ("chillout",                 ("Downtempo",     "Chillout")),
    ("chill out",                ("Downtempo",     "Chillout")),
    ("lo-fi hip hop",            ("Downtempo",     "Lo-fi Hip Hop")),
    ("lofi hip hop",             ("Downtempo",     "Lo-fi Hip Hop")),
    # Eletrônico alternativo
    ("synthwave",                ("Eletrônico",    "Synthwave")),
    ("outrun",                   ("Eletrônico",    "Synthwave")),
    ("retrowave",                ("Eletrônico",    "Synthwave")),
    ("darkwave",                 ("Eletrônico",    "Darkwave")),
    ("dark wave",                ("Eletrônico",    "Darkwave")),
    ("vaporwave",                ("Eletrônico",    "Vaporwave")),
    ("vapourwave",               ("Eletrônico",    "Vaporwave")),
    ("chiptune",                 ("Eletrônico",    "Chiptune")),
    ("chip tune",                ("Eletrônico",    "Chiptune")),
    ("8-bit",                    ("Eletrônico",    "Chiptune")),
    ("hyperpop",                 ("Eletrônico",    "Hyperpop")),
    ("witch house",              ("Eletrônico",    "Witch House")),
    ("electronic body music",    ("Eletrônico",    "EBM")),
    ("ebm",                      ("Eletrônico",    "EBM")),
    ("industrial",               ("Eletrônico",    "Industrial")),
    ("electropop",               ("Eletrônico",    "Electropop")),
    ("synth-punk",               ("Eletrônico",    "Synth-punk")),
    ("idm",                      ("Eletrônico",    "IDM")),
    ("intelligent dance music",  ("Eletrônico",    "IDM")),
    ("electro",                  ("Eletrônico",    "Electro")),
    # House
    ("deep house",               ("House",         "Deep House")),
    ("afro house",               ("House",         "Afro House")),
    ("afrohouse",                ("House",         "Afro House")),
    ("organic house",            ("House",         "Organic House")),
    ("progressive house",        ("House",         "Progressive House")),
    ("tech house",               ("House",         "Tech House")),
    ("electro house",            ("House",         "Electro House")),
    ("minimal house",            ("House",         "Minimal House")),
    ("chicago house",            ("House",         "Chicago House")),
    ("funky house",              ("House",         "Funky House")),
    ("acid house",               ("House",         "Acid House")),
    ("house music",              ("House",         "Deep House")),
    ("house",                    ("House",         "Deep House")),
    # Techno
    ("detroit techno",           ("Techno",        "Detroit Techno")),
    ("minimal techno",           ("Techno",        "Minimal Techno")),
    ("industrial techno",        ("Techno",        "Industrial Techno")),
    ("acid techno",              ("Techno",        "Acid Techno")),
    ("hard techno",              ("Techno",        "Hard Techno")),
    ("hardtechno",               ("Techno",        "Hard Techno")),
    ("hypnotic techno",          ("Techno",        "Hypnotic Techno")),
    ("melodic techno",           ("Techno",        "Melodic Techno")),
    ("melodic techno and house", ("Techno",        "Melodic Techno")),
    ("techno",                   ("Techno",        "Detroit Techno")),
    # Trance
    ("progressive trance",       ("Trance",        "Progressive Trance")),
    ("uplifting trance",         ("Trance",        "Uplifting Trance")),
    ("dark trance",              ("Trance",        "Dark Trance")),
    ("tech trance",              ("Trance",        "Tech Trance")),
    ("acid trance",              ("Trance",        "Acid Trance")),
    ("vocal trance",             ("Trance",        "Vocal Trance")),
    ("trance",                   ("Trance",        "Progressive Trance")),
    # Psytrance
    ("full on psytrance",        ("Psytrance",     "Full On")),
    ("full on",                  ("Psytrance",     "Full On")),
    ("progressive psy",          ("Psytrance",     "Progressive Psy")),
    ("progressive psytrance",    ("Psytrance",     "Progressive Psy")),
    ("dark psy",                 ("Psytrance",     "Dark Psy")),
    ("dark psytrance",           ("Psytrance",     "Dark Psy")),
    ("forest psytrance",         ("Psytrance",     "Forest")),
    ("forest",                   ("Psytrance",     "Forest")),
    ("suomi",                    ("Psytrance",     "Suomi")),
    ("zenonesque",               ("Psytrance",     "Zenonesque")),
    ("hi-tech psytrance",        ("Psytrance",     "Hi-Tech")),
    ("hi-tech",                  ("Psytrance",     "Hi-Tech")),
    ("psytrance",                ("Psytrance",     "Full On")),
    ("psy-trance",               ("Psytrance",     "Full On")),
    ("psychedelic trance",       ("Psytrance",     "Full On")),
    # Drum and Bass
    ("liquid drum and bass",     ("Drum and Bass", "Liquid DnB")),
    ("liquid dnb",               ("Drum and Bass", "Liquid DnB")),
    ("neurofunk",                ("Drum and Bass", "Neurofunk")),
    ("jump up",                  ("Drum and Bass", "Jump Up")),
    ("jungle",                   ("Drum and Bass", "Jungle")),
    ("techstep",                 ("Drum and Bass", "Techstep")),
    ("minimal dnb",              ("Drum and Bass", "Minimal DnB")),
    ("halftime",                 ("Drum and Bass", "Halftime")),
    ("drum and bass",            ("Drum and Bass", "Liquid DnB")),
    ("drum & bass",              ("Drum and Bass", "Liquid DnB")),
    ("dnb",                      ("Drum and Bass", "Liquid DnB")),
    ("d&b",                      ("Drum and Bass", "Liquid DnB")),
    # Dubstep / Bass
    ("brostep",                  ("Dubstep",       "Brostep")),
    ("riddim",                   ("Dubstep",       "Riddim")),
    ("melodic dubstep",          ("Dubstep",       "Melodic Dubstep")),
    ("dubstep",                  ("Dubstep",       "Classic Dubstep")),
    ("future bass",              ("Bass Music",    "Future Bass")),
    ("trap edm",                 ("Bass Music",    "Trap EDM")),
    ("trap",                     ("Bass Music",    "Trap EDM")),
    ("grime",                    ("Bass Music",    "Grime")),
    ("uk garage",                ("Bass Music",    "UK Garage")),
    # Hardcore
    ("happy hardcore",           ("Hardcore",      "Happy Hardcore")),
    ("uk hardcore",              ("Hardcore",      "UK Hardcore")),
    ("gabber",                   ("Hardcore",      "Gabber")),
    ("terrorcore",               ("Hardcore",      "Terrorcore")),
    ("speedcore",                ("Hardcore",      "Speedcore")),
    ("frenchcore",               ("Hardcore",      "Frenchcore")),
    # Experimental
    ("breakcore",                ("Experimental",  "Breakcore")),
    ("noise music",              ("Experimental",  "Noise")),
    ("noise",                    ("Experimental",  "Noise")),
    ("glitch",                   ("Experimental",  "Glitch")),
]

_TAG_LOOKUP: dict[str, tuple[str, str]] = {tag: genre for tag, genre in _TAG_MAP}


def read_metadata(path: str) -> tuple[str | None, str | None]:
    """Lê artist e title dos metadados do arquivo."""
    try:
        from mutagen import File
        audio = File(path, easy=True)
        if audio is None or not audio.tags:
            return None, None
        tags = audio.tags
        artist = tags.get('artist', [None])[0]
        title  = tags.get('title',  [None])[0]
        return artist, title
    except Exception:
        return None, None


def _lastfm_request(params: dict) -> dict:
    params.update({'api_key': LASTFM_API_KEY, 'format': 'json'})
    url = 'https://ws.audioscrobbler.com/2.0/?' + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=6) as resp:
        return json.loads(resp.read().decode())


def fetch_track_tags(artist: str, title: str) -> list[tuple[str, int]]:
    """Retorna [(tag_lowercase, count)] ordenados por count desc."""
    try:
        data = _lastfm_request({
            'method': 'track.getTopTags',
            'artist': artist,
            'track': title,
            'autocorrect': '1',
        })
        tags = data.get('toptags', {}).get('tag', [])
        return sorted(
            [(t['name'].lower().strip(), int(t['count'])) for t in tags],
            key=lambda x: x[1], reverse=True
        )
    except Exception:
        return []


def map_tags(tags: list[tuple[str, int]]) -> tuple[str | None, str | None, float]:
    """
    Encontra o melhor match (genre, subgenre) nas top-10 tags.
    Retorna (genre, subgenre, confidence 0–1) ou (None, None, 0).
    confidence é proporcional ao peso da tag entre as top tags reconhecidas.
    """
    top10 = tags[:10]
    total = sum(c for _, c in top10) or 1

    best_genre = best_sub = None
    best_score = 0.0

    for tag, count in top10:
        if tag in _TAG_LOOKUP:
            score = count / total
            if score > best_score:
                best_score = score
                best_genre, best_sub = _TAG_LOOKUP[tag]

    return best_genre, best_sub, round(best_score, 3)


# ── Spotify ──────────────────────────────────────────────────────────────────

_sp_token: str | None = None
_sp_token_expires: float = 0.0


def _spotify_token() -> str | None:
    global _sp_token, _sp_token_expires
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None
    if _sp_token and time.time() < _sp_token_expires - 60:
        return _sp_token
    creds = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
    req = urllib.request.Request(
        'https://accounts.spotify.com/api/token',
        data=urllib.parse.urlencode({'grant_type': 'client_credentials'}).encode(),
        headers={
            'Authorization': f'Basic {creds}',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        data = json.loads(resp.read().decode())
    _sp_token = data['access_token']
    _sp_token_expires = time.time() + data.get('expires_in', 3600)
    return _sp_token


def _spotify_get(endpoint: str, token: str) -> dict:
    req = urllib.request.Request(
        f'https://api.spotify.com/v1/{endpoint}',
        headers={'Authorization': f'Bearer {token}'},
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        return json.loads(resp.read().decode())


def fetch_spotify_data(artist: str, title: str) -> dict | None:
    """
    Busca faixa no Spotify.
    Retorna {'genres', 'audio_features', 'track_name', 'artist_name'} ou None.
    """
    try:
        token = _spotify_token()
        if not token:
            return None

        q = urllib.parse.urlencode({
            'q': f'artist:{artist} track:{title}',
            'type': 'track',
            'limit': 1,
        })
        search = _spotify_get(f'search?{q}', token)
        items = search.get('tracks', {}).get('items', [])
        if not items:
            return None

        track     = items[0]
        track_id  = track['id']
        artist_id = track['artists'][0]['id']

        features    = _spotify_get(f'audio-features/{track_id}', token)
        artist_data = _spotify_get(f'artists/{artist_id}', token)

        return {
            'track_name':     track['name'],
            'artist_name':    track['artists'][0]['name'],
            'genres':         [g.lower() for g in artist_data.get('genres', [])],
            'audio_features': {
                'tempo':           features.get('tempo'),
                'energy':          features.get('energy'),
                'danceability':    features.get('danceability'),
                'valence':         features.get('valence'),
                'instrumentalness': features.get('instrumentalness'),
                'acousticness':    features.get('acousticness'),
                'loudness':        features.get('loudness'),
            },
        }
    except Exception:
        return None


# ── Pipeline principal ────────────────────────────────────────────────────────

def enrich(path: str) -> dict | None:
    """
    Combina Last.fm + Spotify para obter gênero externo confiável.
    Prioridade: Last.fm (tags de faixa) > Spotify (gêneros de artista).
    """
    artist, title = read_metadata(path)
    if not artist or not title:
        return None

    result: dict = {'artist': artist, 'title': title}

    # ── Last.fm ──
    lfm_genre = lfm_sub = None
    lfm_confidence = 0.0
    lfm_tags: list[str] = []
    if LASTFM_API_KEY:
        tags = fetch_track_tags(artist, title)
        if tags:
            lfm_tags = [t for t, _ in tags[:5]]
            lfm_genre, lfm_sub, lfm_confidence = map_tags(tags)

    # ── Spotify ──
    sp_genre = sp_sub = None
    sp_confidence = 0.0
    sp_features: dict = {}
    sp_genres: list[str] = []
    sp_data = fetch_spotify_data(artist, title)
    if sp_data:
        sp_genres  = sp_data['genres']
        sp_features = sp_data['audio_features']
        # Mapeia gêneros do artista usando o mesmo lookup de tags
        for g in sp_genres:
            if g in _TAG_LOOKUP:
                sp_genre, sp_sub = _TAG_LOOKUP[g]
                sp_confidence = 0.4  # gênero de artista é menos específico que tag de faixa
                break

    # ── Escolha final ──
    # Last.fm ganha se tiver confiança razoável; senão usa Spotify
    if lfm_genre and lfm_confidence >= 0.10:
        result.update({
            'genre': lfm_genre, 'subgenre': lfm_sub,
            'confidence': lfm_confidence, 'method': 'lastfm',
            'top_tags': lfm_tags,
        })
    elif sp_genre:
        result.update({
            'genre': sp_genre, 'subgenre': sp_sub,
            'confidence': sp_confidence, 'method': 'spotify',
            'top_tags': sp_genres[:5],
        })
    else:
        return None  # nenhuma fonte retornou gênero mapeável

    result['spotify_features'] = sp_features  # sempre inclui se disponível
    return result
