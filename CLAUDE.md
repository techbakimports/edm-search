# edm-search

## Objetivo
Analisador de músicas eletrônicas: identifica BPM, tom, gênero/subgênero EDM (70+ categorias) e faz auto-tagging via APIs externas.

## Stack
- Python 3.13
- Audio: librosa, pydub, soundfile
- ML: scikit-learn (Random Forest), numpy
- GUI: DearPyGui 2.x (desktop, com threading)
- APIs: Last.fm, Spotify, Discogs (tagging online)

## Dependências
```bash
pip install -r requirements.txt
```

## Arquivos principais
- `main.py` — CLI principal com argparse (ponto de entrada)
- `analyzer.py` — extração de features (BPM, espectro, tom)
- `classifier.py` — classificação de gêneros (rule-based + ML)
- `gui.py` — interface gráfica DearPyGui (82KB, threading interno)
- `config.py` — taxonomia de 70+ gêneros com BPM ranges
- `tagger.py` — auto-tagging via Last.fm/Spotify/Discogs
- `enricher.py` — enriquecimento de metadados via APIs
- `trainer.py` — treina o modelo Random Forest
- `train_checkpoint.pkl` — modelo ML persistido (3.95MB, não commitar outro sem testar)

## Comandos
```bash
python main.py <arquivo>                        # Análise de arquivo único
python main.py <pasta>                          # Análise em lote
python main.py <arquivo> --plot                 # Com visualização gráfica
python main.py <pasta> --export csv|json        # Exporta resultados
python main.py --compare <arq1> <arq2>          # Compara duas faixas
python main.py --gui                            # Interface gráfica
python main.py --tag <arquivo|pasta>            # Auto-tagging
python main.py --tag <arquivo|pasta> --dry-run  # Preview sem gravar
python trainer.py --dataset ./dataset --output model.pkl  # Treina novo modelo
```

## Formatos suportados
`.mp3 .wav .flac .ogg .m4a .aiff`

## Variáveis de ambiente (.env)
```
LASTFM_API_KEY=
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
DISCOGS_KEY=
DISCOGS_SECRET=
```
Todas as APIs são opcionais — sem elas, o tagging usa apenas dados locais.

## Regras
- Nunca commitar `.env` (já no .gitignore)
- `train_checkpoint.pkl` está no repo — substituir só após validar novo modelo
- GUI usa threads internas; não chamar funções DearPyGui fora da thread principal
