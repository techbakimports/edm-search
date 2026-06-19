# EDM Analyzer

EDM Analyzer é uma ferramenta em Python para identificação de BPM, tom e gêneros musicais (com foco em música eletrônica). A ferramenta suporta análise de arquivos individuais, análise em lote, comparação entre faixas e possui interface gráfica.

## Funcionalidades
- **Identificação de BPM e Tom**: Analisa a música e identifica o andamento e o tom dominante.
- **Classificação de Gêneros e Subgêneros**: Identifica o estilo musical (Psytrance, Techno, House, Trance, etc.) e subgêneros com grau de confiança.
- **Análise Espectral e Waveform**: Exibe uma visualização gráfica no terminal das frequências (Sub-bass, Bass, Mid, High).
- **Interface Gráfica e CLI**: Use via linha de comando ou pela interface gráfica integrada.
- **Exportação**: Suporte a exportação dos resultados em lote para `csv` ou `json`.
- **Comparação de Faixas**: Compare atributos (BPM, graves, energia) de duas músicas lado a lado.

## Uso

### CLI (Linha de Comando)
```bash
# Analisa um arquivo
python main.py <arquivo>

# Analisa todos os arquivos de uma pasta
python main.py <pasta>

# Exibe visualização gráfica adicional (requer pacotes de plotagem instalados)
python main.py <arquivo> --plot

# Exporta resultados da análise em lote
python main.py <pasta> --export csv
python main.py <pasta> --export json

# Compara duas músicas
python main.py --compare <arq1> <arq2>

# Abre a interface gráfica
python main.py --gui
```

## Instalação

Recomenda-se o uso de um ambiente virtual:
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Estrutura do Projeto
- `main.py`: Ponto de entrada (CLI e chamadas).
- `analyzer.py`: Processamento de áudio (extração de features).
- `classifier.py`: Regras e heurísticas para classificação de gênero e subgênero.
- `gui.py`: Interface gráfica.
- `visualizer.py`: Geração de gráficos.
- `config.py`: Configurações e definições.
- `trainer.py`: Funcionalidades adicionais para treinamento/ajustes de regras.

