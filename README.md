# AliExpress Coin Collector (Docker headless)

Automazione per raccogliere le coin giornaliere di AliExpress con un browser
**headless** che gira in **Docker**, pensata per un mini PC sempre acceso a
basso consumo.

## Cosa fa (stesso flusso della versione originale)

1. Apre la pagina delle coin
2. Effettua il **login** con comportamento "umano" (digitazione lenta,
   refusi occasionali corretti, pause casuali) — se la sessione salvata non è
   più valida
3. Cambia il **paese di destinazione in Corea** (ricompensa coin massima)
4. Clicca il pulsante **Collect** giornaliero
5. Se qualcosa fallisce, **riparte dal punto 1** fino a `MAX_ATTEMPTS` cicli

Miglioramenti rispetto alla versione Selenium:

- **Niente gestione di chromedriver**: l'immagine Docker ufficiale Playwright
  include già Chromium e i driver (multi-arch: amd64 **e** arm64)
- **Persistenza della sessione**: i cookie vengono salvati in `/data`, così
  nelle esecuzioni successive (se ancora validi) il login viene saltato
- **Schedulatore integrato**: il container resta attivo e raccoglie ogni 24h
  **allo stesso orario in cui è avvenuta la raccolta precedente** (l'orario è
  memorizzato e sopravvive ai restart), senza Task Scheduler né cron
- **Screenshot di debug** salvati automaticamente quando un passo fallisce
- **Rilevamento captcha**: se compare una sfida anti-bot lo segnala nei log

## Requisiti

- [Docker](https://docs.docker.com/get-docker/) (con Docker Compose v2)
- Un account AliExpress valido

> Su un mini PC ARM (es. Orange Pi, Raspberry Pi 4/5) funziona allo stesso
> modo: l'immagine Playwright è multi-arch.

## Avvio rapido

```bash
# 1. copia la cartella del progetto sul mini PC
# 2. crea il file .env con le tue credenziali
cp .env.example .env
nano .env            # metti email e password reali, e l'orario preferito

# 3. builda e avvia in background
docker compose up -d --build

# guarda i log
docker compose logs -f
```

Il container resta attivo 24/7: raccoglie **subito alla prima esecuzione**, poi
ogni 24h allo stesso orario in cui è avvenuta la raccolta precedente. Con
`restart: unless-stopped` torna da solo dopo un riavvio del PC (e riprende
l'orario memorizzato, anche se il PC è stato spento per qualche ora/giorno).

Per una **prova immediata** (senza aspettare lo scheduler):

```bash
RUN_ON_SCHEDULE=0 docker compose run --rm coin-collector
```

## Configurazione (variabili in `.env`)

| Variabile            | Default | Descrizione                                          |
| -------------------- | ------- | ---------------------------------------------------- |
| `ALIEXPRESS_EMAIL`   | —       | **Obbligatoria** — email dell'account                |
| `ALIEXPRESS_PASSWORD`| —       | **Obbligatoria** — password dell'account             |
| `RUN_ON_SCHEDULE`    | `1`     | `1` = resta attivo (schedulatore 24h), `0` = esegue subito ed esce |
| `TZ`                 | `Europe/Rome` | Timezone usata per l'orario e i log             |
| `JITTER_MINUTES`     | `0`     | Offset casuale (0-N minuti) aggiunto all'orario giornaliero |
| `HEADLESS`           | `true`  | `false` solo per debug locale con browser visibile   |
| `MAX_ATTEMPTS`       | `3`     | Numero massimo di cicli completi per esecuzione      |
| `COIN_URL`           | link coin | URL della pagina coin                            |

## Dati salvati (volume `coin-data`)

- `data/storage_state.json` — cookie di sessione (salta il login alle
  esecuzioni successive)
- `data/schedule.json` — orario dell'ultima raccolta (ancora del ciclo 24h)
- `data/debug/*.png` — screenshot dei punti critici (click Collect, errori)

Per recuperarli sul PC:

```bash
docker run --rm -v aliexpresscollectecoins_coin-data:/data -v "$PWD":/out alpine \
  cp -r /data /out/
```

(o `docker compose exec` con un'immagine temporanea a piacere)

## Immagine pubblica su GitHub Container Registry

Il workflow in `.github/workflows/docker-publish.yml` pubblica automaticamente
l'immagine su [GHCR](https://ghcr.io) a ogni push su `main` (tag `latest`) e a
ogni tag `v*` (tag semver), in versione **multi-arch** (`linux/amd64` +
`linux/arm64`): funziona quindi sia su mini PC x86 che ARM.

Su qualsiasi macchina con Docker:

```bash
docker pull ghcr.io/<owner>/aliexpresscollectecoins:latest

docker run -d --name aliexpress-coin-collector \
  --restart unless-stopped \
  -v coin-data:/data \
  -e TZ=Europe/Rome \
  -e ALIEXPRESS_EMAIL="..." \
  -e ALIEXPRESS_PASSWORD="..." \
  ghcr.io/<owner>/aliexpresscollectecoins:latest
```

(`<owner>` = il tuo account/organizzazione GitHub; il nome immagine segue
quello del repo, in minuscolo.)

Repo privato: `docker login ghcr.io` prima del pull, con un token GitHub con
scope `packages:pull`.

## Esecuzione locale senza Docker (debug)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
HEADLESS=false RUN_ON_SCHEDULE=0 python collect_coins.py
```

## Consumo di risorse

- Il browser è attivo solo pochi minuti al giorno (durante la raccolta)
- Nel resto del tempo il container dorme: ~30 MB di RAM
- Limiti imposti in `docker-compose.yml`: **768 MB di RAM, 1 CPU**
- Log con rotazione automatica (massimo 10 MB)

## Troubleshooting

- **`Login failed` / captcha nei log**: controlla gli screenshot in
  `data/debug/`. Se AliExpress sta mostrando sfide anti-bot, riduci la
  frequenza o verifica che l'account non richieda una verifica MFA.
- **`element not found`**: AliExpress potrebbe aver cambiato il sito.
  Controlla gli screenshot di debug e aggiorna i selettori in
  `collect_coins.py` (sono tutti raggruppati nelle funzioni dedicate).
- **Sessione sempre da rifare**: se `storage_state.json` non viene salvato,
  il login verrà ripetuto a ogni esecuzione (funziona, ma consuma di più).
- **Un'esecuzione fallisce** (captcha, rete, sito cambiato): il tentativo
  successivo è allo stesso orario del giorno dopo; controlla i log e gli
  screenshot di debug nel frattempo.
- **Contenitore in crash-loop**: `docker compose logs` mostra l'errore
  Python; di solito è un problema di rete o un selettore non trovato.

## Avvisi legali

Questo tool è fornito a scopo educativo. L'automazione di interazioni con
AliExpress potrebbe violare i loro Termini di Servizio: usalo a tuo rischio,
il creatore non è responsabile di sospensioni dell'account o altri effetti.
