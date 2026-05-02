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
import re
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


def _fetch_musicbrainz_data(artist: str, title: str) -> dict:
    """
    Busca no MusicBrainz e retorna {'year': str, 'release_mbid': str}.
    Faz UMA requisição e extrai ambos os dados.
    Escolhe o recording com o menor first-release-date (= lançamento original).
    """
    search_title = _clean_title(title)
    query = urllib.parse.urlencode({
        'query': f'artist:"{artist}" recording:"{search_title}"',
        'fmt': 'json',
        'limit': 5,
    })
    req = urllib.request.Request(
        f'https://musicbrainz.org/ws/2/recording/?{query}',
        headers={'User-Agent': 'EDMAnalyzer/1.0 (edm-search)'},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())

    best_year: int | None = None
    best_mbid: str | None = None

    for rec in data.get('recordings', []):
        date = rec.get('first-release-date', '')
        if not (date and len(date) >= 4 and date[:4].isdigit()):
            continue
        year = int(date[:4])
        releases = rec.get('releases', [])
        mbid = releases[0].get('id') if releases else None
        if best_year is None or year < best_year:
            best_year = year
            best_mbid = mbid

    result: dict = {}
    if best_year:
        result['year'] = str(best_year)
    if best_mbid:
        result['release_mbid'] = best_mbid
    return result


def fetch_year_musicbrainz(artist: str, title: str) -> str | None:
    try:
        return _fetch_musicbrainz_data(artist, title).get('year')
    except Exception:
        return None


def fetch_year(artist: str, title: str) -> str | None:
    return fetch_year_musicbrainz(artist, title)


# ── Cover Art Archive ─────────────────────────────────────────────────────────

def fetch_cover_art(release_mbid: str) -> bytes | None:
    """Baixa a capa frontal via Cover Art Archive (gratuito, sem chave)."""
    url = f'https://coverartarchive.org/release/{release_mbid}/front-500'
    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'EDMAnalyzer/1.0 (edm-search)'},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
    except Exception:
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

def write_tags(path: str, artist: str, title: str, year: str | None, dry_run: bool = False) -> dict:
    result = {
        'path':          path,
        'file':          Path(path).name,
        'artist':        artist,
        'title':         title,
        'year':          year or '—',
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
            audio.tags['date'] = year
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
) -> dict:
    """
    Pipeline completo para um arquivo:
      1. Parse do nome → artist, title
      2. Busca no MusicBrainz → ano + release_mbid (uma só requisição)
      3. Escrita das tags ID3/Vorbis no arquivo
      4. Download e gravação da capa via Cover Art Archive

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

    year         = None
    release_mbid = None

    if fetch_year_online or fetch_cover:
        try:
            mb_data      = _fetch_musicbrainz_data(artist, title)
            year         = mb_data.get('year') if fetch_year_online else None
            release_mbid = mb_data.get('release_mbid') if fetch_cover else None
        except Exception:
            pass

    result = write_tags(path, artist, title, year, dry_run=dry_run)

    if fetch_cover and release_mbid and not dry_run and result.get('written'):
        image_bytes = fetch_cover_art(release_mbid)
        if image_bytes:
            result['cover_written'] = write_cover(path, image_bytes)

    return result


def tag_folder(
    folder: str,
    dry_run: bool = False,
    fetch_year_online: bool = True,
    fetch_cover: bool = True,
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
    return [
        tag_file(p, dry_run=dry_run, fetch_year_online=fetch_year_online, fetch_cover=fetch_cover)
        for p in paths
    ]
