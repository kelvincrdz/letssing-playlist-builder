# LetsSing Playlist Builder

Aplicação local para buscar músicas, montar uma playlist e gerar um DLC de
**Let's Sing 2026** (Switch + Atmosphere/LayeredFS) usando o
[Let's Sing UltraStar Converter (ultrastar2singIt)](https://github.com/ratnapatka/ultrastar2singIt).

> Status: **F1–F5 prontas** — busca (USDB e YouTube, com "Carregar mais"),
> importação de playlist do YouTube, importação manual (UltraStar-ES), download, .txt normalizado, .txt gerado por IA
> (UltraSinger) quando não há outro, conversão com o ultrastar2singIt e
> .zip pronto para o SD.

## Instalação das ferramentas (Windows)

1. **FFmpeg** "full" do [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)
   (ou `winget install Gyan.FFmpeg`). Se estiver no PATH, a app acha sozinha.
2. **RAD Video Tools** — [radgametools.com/bnkdown.htm](https://www.radgametools.com/bnkdown.htm).
   Obrigatório no Let's Sing 2024+ (vídeos `.bk2`), inclusive no modo "só capa".
   Instalado no caminho padrão (`C:\Program Files (x86)\RADVideo\radvideo64.exe`),
   a app acha sozinha; senão, configure o caminho.
   O RAD atual não aceita parte dos parâmetros que o conversor v1.0.2 envia
   (`/(1280 /)720`, `/Q`): ele abre a janela de ajuda e trava. Por isso a app
   passa ao conversor o `letssing-rad.exe` (criado pelo `uv sync`), que traduz
   os parâmetros para os equivalentes documentados (`/Scale`, `/DataRateByQuality`,
   `/DataRateByPercent`, `/NoSound`), chama o RAD real e o encerra após 30 min
   sem resposta. Cada chamada fica registrada em `workspace/tools/rad-shim.jsonl`.
3. **Let's Sing UltraStar Converter** — baixe o `.exe` em
   [releases](https://github.com/ratnapatka/ultrastar2singIt/releases) e
   configure o caminho. A app usa uma cópia em `workspace/tools/converter/`
   (com `config.yml` próprio), então sua instalação não é alterada.
   *Alternativa:* apontar para a pasta do código-fonte; nesse caso instale
   as dependências dele no `.venv`:
   `uv pip install --python .venv\Scripts\python.exe --group converter-src -r pyproject.toml`.
4. **UltraSinger** (opcional: só para músicas sem .txt) —
   [github.com/rakuri255/UltraSinger](https://github.com/rakuri255/UltraSinger).
   Clone o repositório e rode `install\CPU\windows_cpu.bat` (precisa de
   Python 3.12; baixa alguns GB: PyTorch, WhisperX, Demucs). Em Configurações,
   informe a pasta do repositório; o `python.exe` padrão é o `.venv` dela.
5. Em **Configurações**: versão do jogo, TitleID do jogo, TitleID do DLC e
   DLC JSON Name (do `romfs/name.txt`, lido pelo DBI).

## Copiar para o Switch

1. Na aba **Gerar DLC**, clique em "Gerar DLC" e depois em "Baixar …zip".
2. Com o Switch desligado, descompacte o .zip na **raiz** do cartão SD: ele
   cria `atmosphere/contents/<TitleID do DLC>/romfs/…`.
3. O DLC configurado precisa estar instalado. Ligue no Atmosphere e abra o jogo.

Cada pacote contém **todas** as músicas convertidas naquele lote (o
conversor monta o `songs_xx.json` do zero); música fora do lote some do jogo.

## .txt gerado (F5)

Música sem .txt importado é gerada depois do download do áudio, nesta ordem:

1. **Legendas do YouTube** (pode desligar em Configurações): a app baixa a
   legenda do vídeo em json3 — a automática no idioma original traz o tempo de
   cada palavra; a manual, o de cada linha, dividido entre as palavras. Com ela
   monta um .txt com letra e tempo e notas neutras, e o UltraSinger, no modo
   *re-pitch* (entrada .txt), calcula só as notas. Sem o Whisper, é bem mais
   rápido, e a letra é a do vídeo. A legenda automática às vezes vem com o
   idioma detectado errado (ex.: `es-orig` numa música em português) e a letra
   sai inventada: quando o LRCLIB tem a letra, a app compara as duas e, se
   menos de 40% das palavras da legenda estão na letra, usa o LRCLIB.
2. **Letra sincronizada do LRCLIB** (lrclib.net; grátis, sem chave; pode
   desligar): busca por artista, título e duração (±3 s, para não pegar versão
   ao vivo). O LRC dá o início de cada linha; o tempo das palavras é estimado
   pelo tamanho delas, e o re-pitch calcula as notas. "Tempo Perdido", que não
   tem legenda em nenhum vídeo, tem letra sincronizada lá (álbum "Dois", 302 s).
   O banco da usabledatabases só tem letra sem tempo (e é pago), o Last.fm não
   tem letras e o Lyrics.com bloqueou a leitura da página da API. Letra sem
   tempo não serve: o UltraSinger não tem opção de entrada de letra.
3. **UltraSinger completo**, quando não há legenda nem letra sincronizada (muitos
   vídeos de música não têm; nos testes, nenhum vídeo de "Tempo Perdido"
   tinha), a legenda é de outro idioma ou tem menos de 20 palavras.

Todas ficam marcadas **gerado por IA — revisar** (as notas são da IA). Falha
ao baixar legendas (ex.: HTTP 429 do YouTube) só faz cair no passo 2.

### UltraSinger

Música sem .txt (fonte YouTube, sem importação) é gerada pelo UltraSinger
depois do download do áudio, e fica marcada **gerado por IA — revisar**.

- Opções usadas (lidas no código do UltraSinger): `--whisper <modelo>`,
  `--force_cpu`, `--format_version 1.0.0` (sem `#INSTRUMENTAL`/`#VOCALS`),
  `--disable_karaoke`, `--language <código>` quando o idioma da música é
  conhecido, `--ffmpeg <pasta>`.
- O modelo do Whisper é configurável. O padrão do UltraSinger é `large-v2`,
  lento demais sem GPU; a app usa `small`. Conte vários minutos por música.
- Roda numa pasta própria (`workspace/ultrasinger/`), apagada ao terminar.
  Só o .txt vai para a pasta da música; artista e título voltam a ser os da
  playlist (o UltraSinger troca pelos do MusicBrainz).
- Não é refeito nas próximas execuções, a não ser que o áudio mude.
  Importar um .txt depois substitui o gerado.
- O UltraSinger não aceita uma letra pronta: a letra sempre vem do Whisper.
- Sem o UltraSinger configurado, essas músicas são puladas com o motivo.

## Conversão (F4)

- **Staging** (`workspace/stage/`): é a pasta de entrada que o conversor vê.
  As entradas se chamam `00_input.*` (o conversor grava cache com extensões
  de entrada na mesma pasta e pega o primeiro arquivo de cada tipo); uma
  impressão digital de cada entrada faz apagar só o cache afetado quando
  algo muda. O `.bk2` (lento) é reaproveitado entre execuções.
- Músicas fora do lote ficam como `_off_<pasta>` (o conversor ignora `_`).
- Antes de rodar, a app confere TitleIDs, JSON name, FFmpeg, RAD e o
  conversor; faltando algo, as músicas ficam "preparadas" e o motivo aparece.
- O resultado por música é conferido pelos arquivos gerados em
  `workspace/output/` (áudio, prévia, capa, vxla, vídeo).

## Busca

- **USDB** (usdb.animux.de): busca e download do `.txt` exigem conta gratuita,
  até para buscar (conferido sem login em 2026-11-22: "You are not logged in").
  Usuário em Configurações; a senha fica no Cofre de Credenciais do Windows
  (`keyring`), nunca no `config.json` nem nos logs. O `.txt` é feito por
  pessoas e traz os links de mídia na tag `#VIDEO` (`a=`, `v=`, `co=`), que a
  app usa quando a música não tem link escolhido.
- **YouTube e USDB**: 30 resultados por vez; **Carregar mais** pede os próximos 30
  (até 150). As fontes não têm paginação própria: a app pede um limite maior e
  mostra só a parte nova, sem apagar o que você já editou nos cartões.
- **Playlist do YouTube**: cole o link (`youtube.com/playlist?list=…` ou um
  vídeo aberto dentro dela). Até 200 vídeos; privados/removidos ficam de fora.
  Artista e título vêm do nome do vídeo — confira antes de "Adicionar marcadas".
  Duplicatas são puladas.
- **Minhas playlists do YouTube**: com o login do YouTube em Configurações, lista
  as playlists da conta (`feed/playlists`, inclusive privadas), Vídeos curtidos
  (`LL`) e Assistir mais tarde (`WL`). O login é a sessão de um navegador
  (`--cookies-from-browser` do yt-dlp) ou um `cookies.txt` exportado. Só os
  cookies de youtube.com entram na sessão do yt-dlp, o arquivo não é regravado,
  e o login só é usado para ler playlists (downloads continuam sem login).
  Chrome/Edge atuais criptografam os cookies de um jeito que o yt-dlp não lê:
  use Firefox ou exporte um cookies.txt de uma janela anônima (e feche-a).
- **Spotify** ("Do meu Spotify"): escolha uma playlist sua (ou colaborativa) ou
  as Músicas Curtidas e marque as faixas. Do Spotify vêm artista, título e
  duração; o áudio vem do YouTube, preferindo o vídeo com a mesma duração.
  Login PKCE: só o **Client ID** em Configurações (sem Client secret). No app do
  Spotify (developer.spotify.com/dashboard → app → Settings → Redirect URIs),
  cadastre `http://127.0.0.1:8000/api/spotify/callback` — o Spotify não aceita
  `localhost`. Em modo de desenvolvimento o dono do app precisa ter Premium e
  cada conta que usar a app precisa estar em "Users Management" (até 5). O
  Spotify só libera as faixas de playlists suas ou colaborativas. O refresh
  token fica no Cofre de Credenciais do Windows e vale 6 meses.
- Outros serviços: no yt-dlp usado, as buscas de música são só YouTube e
  SoundCloud (só áudio, sem vídeo nem legenda); Spotify/Deezer não permitem
  baixar. Por isso não entraram.

## .txt UltraStar (F3)

- **USDB**: baixado antes da mídia e guardado como `.letssing.original`;
  não é baixado de novo enquanto o ID não mudar.
- **UltraStar-ES / manual**: o site bloqueia acesso automático (Cloudflare).
  Baixe pelo navegador e use "Importar do UltraStar-ES" (cria a música a
  partir do `#ARTIST`/`#TITLE`) ou "importar .txt" numa linha da playlist.
  Mídia dentro do .zip é usada; o resto vem do YouTube.
- **Normalização**: o original fica em `.letssing.original` (sem extensão
  `.txt`, para o conversor não pegá-lo). O `<pasta>.txt` final é gravado em
  UTF-8 **com BOM** — o conversor detecta encoding com `chardet` nos primeiros
  4 KB e, sem BOM, um início todo ASCII faria os acentos sumirem. As tags
  `#MP3`/`#AUDIO`/`#VIDEO`/`#COVER` apontam para os arquivos locais, e o
  `#GAP` da playlist (se preenchido) substitui o original.

## Como o download funciona (F2)

- Busca: `ytsearch5:` via yt-dlp. Artista/título são deduzidos do título do
  vídeo e podem ser corrigidos antes de adicionar.
- "Gerar DLC" baixa, por música: áudio `.mp3`, capa `.jpg` (thumbnail ou link
  próprio), vídeo `.mp4` (H.264 preferido, até a altura configurada) e
  legendas `.srt` pt/en quando existirem.
- Música sem link de áudio: usa o 1º resultado do YouTube e salva o link.
- Retomável: `.letssing.json` em cada pasta registra a origem; só é baixado
  o que falta ou cujo link/qualidade mudou. "Sem vídeo" guarda o vídeo em
  `_sem_video/` (o conversor ignora subpastas) para reaproveitar depois.
- Erro numa música marca só ela como `erro`; o lote continua.
- O yt-dlp precisa de um runtime JavaScript para o YouTube: o pacote pip
  `deno` (já nas dependências) fornece um, sem instalação global.

## Rodar (Windows)

Pré-requisito: [uv](https://docs.astral.sh/uv/) (ele instala o Python 3.12). `uv sync` cria o `.venv`, instala
as dependências e o `letssing-rad.exe` (intermediário do RAD); rode de novo quando o `pyproject.toml` mudar.
O projeto fica no Python 3.12 instalado pelo uv (`.python-version` e `python-preference = "only-managed"`):
o Python da Microsoft Store esconde `AppData\Roaming` dos processos que abre, e o UltraSinger falharia
com "No Python at …".

```powershell
uv sync
.venv\Scripts\python.exe run.py
```

Abra http://localhost:8000. Host/porta ficam no topo de `run.py`.

Testes:

```powershell
.venv\Scripts\python.exe -m pytest -q
```

## Levar para outro PC (ex.: com GPU NVIDIA)

O repositório leva só o código. Ficam de fora (`.gitignore`): `.venv/`, `data/`
(configurações e playlist) e `workspace/` (downloads, ferramentas, saídas).

1. No PC atual, **Playlist → Exportar JSON** (a playlist vai junto num arquivo).
2. No PC novo: `git clone …`, `uv sync` e as ferramentas da seção
   "Instalação das ferramentas". Para o UltraSinger com GPU, use
   `install\CUDA\windows_cuda_gpu.bat` (em vez do de CPU) dentro de
   `workspace\tools\UltraSinger` — a app acha essa pasta sozinha.
3. Inicie a app, marque **Configurações → Usar GPU NVIDIA no UltraSinger**,
   refaça os logins (USDB, Spotify, YouTube: senhas e tokens ficam no Cofre do
   Windows de cada PC) e **Playlist → Importar JSON**.
4. Os `.txt` já gerados ficam em `workspace/input/` do PC antigo; para não
   gerar de novo, copie essa pasta para o mesmo lugar no PC novo.

## Onde ficam os dados

| Caminho | Conteúdo |
|---|---|
| `data/config.json` | Configurações |
| `data/playlist.db` | Playlist (SQLite) |
| `workspace/` | Única pasta onde a app cria/apaga arquivos do pipeline |

## Configuração do jogo/DLC

Na aba **Configurações** informe TitleID do jogo, TitleID do DLC e o
**DLC JSON Name** — este último vem de `[TITLEID DO DLC]/romfs/name.txt`, lido
no console pelo DBI. A lista de sugestões vem do banco do próprio conversor
(v1.0.2); quando o DLC está nessa lista, o conversor força esses valores, e a
UI avisa se o que você digitou for diferente.

## Particularidades do conversor (levadas em conta)

- A pasta de saída é **apagada** a cada execução → a app usa só `workspace/output/`.
- O conversor **renomeia** as pastas de entrada (tira acentos, parênteses,
  apóstrofos…). A app já gera nomes nesse formato (`letssing/naming.py`).
- No modo `--cli`, `--still-video`, `--max-video-size` e `--pitch-correction`
  são ignorados se o `config.yml` não tiver `conversion_tweaks.enable: true`;
  na F4 a app gera esse arquivo numa cópia do conversor em `workspace/tools/`.
