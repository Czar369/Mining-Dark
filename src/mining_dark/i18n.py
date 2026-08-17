"""
Interface translations, shared by the graphical dashboard and the command line.

Only *chrome* is translated - the things a person reads to operate the app.
Everything the project generates stays in English by design: wallet files, log
files on disk, CSV headers, config keys, code identifiers.  Technical HUD
vocabulary (SCANNING, VERIFYING, SCAN/VERIFY/MERGE, STREAM LOG) is also left in
English in both languages, because that is jargon rather than prose.  So are the
shell commands quoted in the fix hints: `mining-dark utxo update` is typed, not
read, and translating it would make it wrong.

Each entry carries both translations on one line, so a missing or drifted
translation is visible at a glance instead of hiding in a second file.

This module lives at the top of the package, not under `gui/`, because the CLI
reads from it too and must not import the graphical package to print a message.
"""

from __future__ import annotations

from typing import Final

#: Language code -> name shown in the settings dialog.
LANGUAGES: Final[dict[str, str]] = {
    "pt": "Português (BR)",
    "en": "English",
}

DEFAULT_LANGUAGE: Final[str] = "pt"

# key: (portuguese, english)
_STRINGS: Final[dict[str, tuple[str, str]]] = {
    # ----- window / header ---------------------------------------------------
    "app.tagline": ("BITCOIN BALANCE SCANNER PRO", "BITCOIN BALANCE SCANNER PRO"),
    "header.mode": ("MODO", "MODE"),
    "header.workers": ("WORKERS", "WORKERS"),
    "header.backend": ("BACKEND", "BACKEND"),
    # Not "uptime": the clock measures the scan, not how long the window has
    # been open, and it stops while the scan is paused.
    "header.session": ("SESSAO", "SESSION"),
    "header.settings_tip": ("Configuracoes do projeto", "Project settings"),
    "header.wallets_tip": ("Wallets encontradas", "Found wallets"),

    # ----- run state ---------------------------------------------------------
    "state.stopped": ("PARADO", "STOPPED"),
    "state.starting": ("INICIANDO", "STARTING"),
    "state.running": ("EM EXECUCAO", "RUNNING"),
    "state.paused": ("PAUSADO", "PAUSED"),
    "state.stopping": ("ENCERRANDO", "STOPPING"),
    "state.error": ("ERRO", "ERROR"),

    # ----- workers panel -----------------------------------------------------
    "workers.title": ("WORKERS / ROTAS", "WORKERS / ROUTES"),
    "workers.active": ("{active}/{total} ATIVOS", "{active}/{total} ACTIVE"),

    # ----- activity panel ----------------------------------------------------
    "activity.title": ("MATRIZ DE ATIVIDADE", "ACTIVITY MATRIX"),
    "activity.subtitle": ("{nodes} nos  ·  sweep {sweep}", "{nodes} nodes  ·  sweep {sweep}"),
    "activity.sweep_on": ("ativo", "active"),
    "activity.sweep_off": ("parado", "stopped"),
    "activity.hub_label": ("CHAVES/S", "KEYS/S"),
    "tile.kps": ("CHAVES / S", "KEYS / S"),
    "tile.cps": ("ENDERECOS / S", "ADDRESSES / S"),
    "tile.found": ("WALLETS FOUND", "WALLETS FOUND"),
    "tile.keys": ("CHAVES GERADAS", "KEYS GENERATED"),
    "tile.addresses": ("ENDERECOS VERIFICADOS", "ADDRESSES CHECKED"),
    "tile.btc": ("BTC ENCONTRADO", "BTC FOUND"),
    "tile.simulated": ("SIM", "SIM"),

    # ----- log panel ---------------------------------------------------------
    "log.title": ("STREAM LOG", "STREAM LOG"),
    "log.lines": ("{count} linhas", "{count} lines"),
    "log.autoscroll": ("auto-scroll", "auto-scroll"),
    "log.clear": ("LIMPAR", "CLEAR"),
    "log.filter.all": ("TUDO", "ALL"),
    "log.filter.info": ("INFO+", "INFO+"),
    "log.filter.warn": ("AVISOS+", "WARN+"),
    "log.filter.error": ("ERROS", "ERRORS"),
    "recent.title": ("ULTIMOS ENDERECOS", "RECENT ADDRESSES"),
    "recent.subtitle": ("somente chaves publicas", "public keys only"),
    "recent.col_type": ("TIPO", "TYPE"),
    "recent.col_address": ("ENDERECO", "ADDRESS"),

    # ----- footer ------------------------------------------------------------
    "btn.start": ("INICIAR", "START"),
    "btn.pause": ("PAUSAR", "PAUSE"),
    "btn.resume": ("RETOMAR", "RESUME"),
    "btn.stop": ("PARAR", "STOP"),
    "footer.db": ("BANCO UTXO", "UTXO DATABASE"),
    "footer.addresses": ("ENDERECOS", "ADDRESSES"),
    "footer.size": ("TAMANHO", "SIZE"),
    "footer.updated": ("ATUALIZADO", "UPDATED"),
    "footer.source": ("FONTE", "SOURCE"),
    "footer.db_hint": ("abra Configuracoes > Node & UTXO para construir o banco",
                       "open Settings > Node & UTXO to build the database"),

    # ----- database status ---------------------------------------------------
    "db.unknown": ("DESCONHECIDO", "UNKNOWN"),
    "db.missing": ("AUSENTE", "MISSING"),
    "db.outdated": ("DESATUALIZADO", "OUTDATED"),
    "db.ok": ("OK", "OK"),
    "db.simulated": ("SIMULADO", "SIMULATED"),

    # ----- settings dialog ---------------------------------------------------
    "settings.title": ("CONFIGURACOES", "SETTINGS"),
    "settings.tab.scanner": ("Scanner", "Scanner"),
    "settings.tab.paths": ("Caminhos", "Paths"),
    "settings.tab.node": ("Node & UTXO", "Node & UTXO"),
    "settings.tab.appearance": ("Aparencia", "Appearance"),
    "settings.save": ("SALVAR", "SAVE"),
    "settings.close": ("FECHAR", "CLOSE"),
    "settings.reload": ("RECARREGAR", "RELOAD"),
    "settings.saved": ("Configuracao gravada em {path}", "Configuration written to {path}"),
    "settings.save_failed": ("Falha ao gravar a configuracao: {error}",
                             "Failed to write the configuration: {error}"),
    "settings.reloaded": ("Configuracao recarregada do disco.",
                          "Configuration reloaded from disk."),
    "settings.locked_while_running": (
        "Pare o scan para alterar estes campos.",
        "Stop the scan to change these fields."),

    "settings.scanner.mode": ("Modo de geracao", "Generation mode"),
    "settings.scanner.mode_tip": (
        "random = chaves aleatorias  |  hd = seeds BIP39 derivadas",
        "random = random keys  |  hd = derived BIP39 seeds"),
    "settings.scanner.workers": ("Workers paralelos", "Parallel workers"),
    "settings.scanner.queue": ("Tamanho da fila de chaves", "Key queue size"),
    "settings.scanner.min_balance": ("Salvar apenas acima de (satoshis)",
                                     "Save only above (satoshis)"),
    "settings.scanner.min_balance_tip": (
        "0 = salva qualquer saldo diferente de zero (o menor filtro possivel).",
        "0 = save any non-zero balance (the loosest possible filter)."),
    "settings.scanner.address_types": ("Formatos de endereco verificados",
                                       "Address formats checked"),
    "settings.scanner.address_types_hint": (
        "Menos formatos = mais chaves por segundo.",
        "Fewer formats = more keys per second."),
    "settings.scanner.hd_children": ("Chaves filhas por seed (modo HD)",
                                     "Child keys per seed (HD mode)"),

    "settings.paths.intro": (
        "Onde cada coisa vive nesta instalacao. Tudo e derivado da pasta do "
        "projeto, entao nada aqui precisa ser digitado.",
        "Where everything lives in this installation. It is all derived from "
        "the project folder, so nothing here needs typing."),
    "settings.paths.project": ("Pasta do projeto", "Project folder"),
    "settings.paths.data": ("Raiz dos dados", "Data root"),
    "settings.paths.found": ("Wallets encontradas", "Found wallets"),
    "settings.paths.logs": ("Arquivos de log", "Log files"),
    "settings.paths.db": ("Banco UTXO", "UTXO database"),
    "settings.paths.core": ("Datadir do Bitcoin Core", "Bitcoin Core datadir"),
    "settings.paths.snapshots": ("Snapshots assumeutxo", "assumeutxo snapshots"),
    "settings.paths.config": ("Arquivo de configuracao", "Configuration file"),
    "settings.paths.open": ("ABRIR", "OPEN"),
    "settings.paths.copy": ("COPIAR", "COPY"),
    "settings.paths.copied": ("Caminho copiado: {path}", "Path copied: {path}"),
    "settings.paths.missing": ("(ainda nao criado)", "(not created yet)"),
    "settings.paths.move_note": (
        "Para mover tudo para outro disco, defina MINING_DARK_DATA_DIR antes de "
        "abrir o programa. Para mover uma pasta so, edite a chave correspondente "
        "no config.yaml.",
        "To move everything to another disk, set MINING_DARK_DATA_DIR before "
        "launching. To move a single folder, edit the matching key in "
        "config.yaml."),

    "settings.node.status": ("2. Bitcoin Core", "2. Bitcoin Core"),
    "settings.node.running": ("rodando", "running"),
    "settings.node.stopped": ("parado", "stopped"),
    "settings.node.blocked": ("CADEIA TRAVADA", "CHAIN BLOCKED"),
    "settings.node.blocked_note": (
        "bloco marcado inválido em {height} - o nó não avança mais. "
        "Rode: mining-dark doctor",
        "block marked invalid at {height} - the node will not advance. "
        "Run: mining-dark doctor",
    ),
    "settings.node.stalled": ("ATRASADO", "BEHIND"),
    "settings.node.stalled_note": (
        "{behind} blocos atrás da rede e sem baixar. Rode: mining-dark doctor",
        "{behind} blocks behind the network and not downloading. "
        "Run: mining-dark doctor",
    ),
    "settings.node.start_failed": ("nao subiu", "failed to start"),
    "settings.node.starting": ("iniciando...", "starting..."),
    # Just the phase name: the bar shows how far along it is and the colour
    # shows which one is active, so words repeating either only add noise.
    # Nested under section 2, so they carry its number: a bare "1." and "2."
    # here read as two more top-level steps beside the three the tab has.
    "settings.node.phase_headers": ("2.1 Cabecalhos", "2.1 Headers"),
    "settings.node.phase_blocks": ("2.2 Blocos", "2.2 Blocks"),
    "settings.node.use_snapshot": (
        "Clicar em INICIAR de novo so repete o erro. O snapshot em disco esta "
        "intacto: marque \"exportar do snapshot\" abaixo e reconstrua o banco.",
        "Pressing START again only repeats the error. The snapshot on disk is "
        "intact: tick \"export from snapshot\" below and rebuild the database."),
    "settings.node.stopping": ("parando...", "stopping..."),
    "settings.node.wait_note": (
        "Aguardando o Bitcoin Core responder. Pode levar de segundos a minutos - "
        "nao clique de novo.",
        "Waiting for Bitcoin Core to answer. This takes seconds to minutes - "
        "do not click again."),
    "settings.node.start": ("INICIAR NODE", "START NODE"),
    "settings.node.stop": ("PARAR NODE", "STOP NODE"),
    "settings.node.snapshot_section": (
        "1. Snapshot assumeutxo  (opcional, mas pula dias de sincronizacao)",
        "1. assumeutxo snapshot  (optional, but skips days of syncing)"),
    "settings.node.snapshot_download": ("BAIXAR SNAPSHOT", "DOWNLOAD SNAPSHOT"),
    "settings.node.snapshot_cancel": ("PARAR DOWNLOAD", "STOP DOWNLOAD"),
    "settings.node.snapshot_load": ("CARREGAR NO NODE", "LOAD INTO NODE"),
    "settings.node.snapshot_needs_node": (
        "inicie o node para poder carregar",
        "start the node before loading"),
    "settings.node.snapshot_needs_headers": (
        "aguardando cabecalhos: {have} de {need}",
        "waiting for headers: {have} of {need}"),
    "settings.node.snapshot_start_and_load": ("INICIAR NODE E CARREGAR",
                                             "START NODE AND LOAD"),
    "settings.node.snapshot_armed_starting": (
        "iniciando o node - o carregamento comeca sozinho quando der",
        "starting the node - the load begins on its own when it can"),
    "settings.node.snapshot_armed_headers": (
        "cabecalhos {have} de {need} - carrega sozinho ao chegar",
        "headers {have} of {need} - loads by itself on arrival"),
    "settings.node.snapshot_absent": ("nao baixado", "not downloaded"),
    "settings.node.snapshot_partial": ("{done} de {total}", "{done} of {total}"),
    "settings.node.snapshot_loading_start": (
        "carregando no no  ·  iniciando",
        "loading into the node  ·  starting"),
    "settings.node.snapshot_loading": (
        "carregando no no  ·  {pct}%  ·  {done} de {total} moedas",
        "loading into the node  ·  {pct}%  ·  {done} of {total} coins"),
    "settings.node.snapshot_ready": ("pronto para carregar  ·  {total}",
                                     "ready to load  ·  {total}"),
    "settings.node.snapshot_loaded": ("ja carregado neste datadir",
                                      "already loaded in this datadir"),
    "log.snapshot_download_started": (
        "Baixando o snapshot assumeutxo (~9 GB). Retoma de onde parar.",
        "Downloading the assumeutxo snapshot (~9 GB). Resumes where it stops."),
    "log.snapshot_download_done": ("Snapshot baixado: {path}",
                                   "Snapshot downloaded: {path}"),
    "log.snapshot_download_failed": ("Falha no download do snapshot: {error}",
                                     "Snapshot download failed: {error}"),
    "log.snapshot_download_paused": ("Download interrompido em {done} de {total}. "
                                     "Clique de novo para retomar.",
                                     "Download stopped at {done} of {total}. "
                                     "Click again to resume."),
    "log.snapshot_incomplete": ("Snapshot incompleto ({done} de {total}) - "
                                "termine o download antes de carregar.",
                                "Snapshot incomplete ({done} of {total}) - "
                                "finish the download before loading."),
    "log.snapshot_load_started": (
        "Carregando o snapshot. Leva de dezenas de minutos a horas - nao interrompa.",
        "Loading the snapshot. Takes tens of minutes to hours - do not interrupt."),
    "log.snapshot_load_done": ("Snapshot carregado. O node saltou para a altura do snapshot.",
                               "Snapshot loaded. The node jumped to the snapshot height."),
    "log.snapshot_load_failed": ("Falha ao carregar o snapshot: {error}",
                                 "Failed to load the snapshot: {error}"),
    "settings.node.reindex": ("reconstruir indice de blocos", "rebuild block index"),
    "settings.node.chain": ("Chain", "Chain"),
    "settings.node.blocks": ("Blocos", "Blocks"),
    "settings.node.sync": ("Sincronizacao", "Sync"),
    "settings.node.disk": ("Em disco", "On disk"),
    "settings.node.snapshot": ("Snapshot assumeutxo ativo - validando em segundo plano",
                               "assumeutxo snapshot active - validating in background"),
    "settings.node.binaries_missing": (
        "bitcoind/bitcoin-cli nao encontrados. Rode scripts/setup_bitcoin_core.sh",
        "bitcoind/bitcoin-cli not found. Run scripts/setup_bitcoin_core.sh"),
    "settings.utxo.title": ("3. Banco UTXO local", "3. Local UTXO database"),
    "settings.utxo.rebuild": ("RECONSTRUIR BANCO", "REBUILD DATABASE"),
    "settings.utxo.rebuilding": ("RECONSTRUINDO...", "REBUILDING..."),
    "settings.node.busy_rebuild": ("em uso pela reconstrucao", "in use by the rebuild"),
    "settings.utxo.node_busy": (
        "a reconstrucao esta conduzindo o no: ela para o bitcoind para exportar "
        "e o religa no fim",
        "the rebuild is driving the node: it stops bitcoind to export and starts "
        "it again at the end"),
    "settings.utxo.force": ("forcar mesmo se atualizado", "force even if up to date"),
    "settings.utxo.from_snapshot": ("exportar do snapshot", "export from snapshot"),
    "settings.utxo.needs_node": (
        "precisa do passo 2: ligue o Bitcoin Core",
        "needs step 2: start Bitcoin Core"),
    "settings.utxo.needs_sync": (
        "precisa do passo 2: faltam {behind} blocos para o tip",
        "needs step 2: {behind} blocks short of the tip"),
    "settings.utxo.needs_rpc": (
        "o no subiu mas ainda nao respondeu ao RPC",
        "the node is up but has not answered RPC yet"),
    "settings.utxo.needs_load_done": (
        "aguarde o load do snapshot terminar",
        "wait for the snapshot load to finish"),
    "settings.utxo.orphaned_snapshot": (
        "load de snapshot interrompido: apague chainstate_snapshot/ com o no parado",
        "snapshot load was interrupted: delete chainstate_snapshot/ with the node stopped"),
    "settings.utxo.needs_verify": (
        "precisa do passo 2: a verificacao da cadeia ainda nao terminou",
        "needs step 2: chain verification has not finished"),
    # Names the step instead of saying "preparing", because this phase is the
    # long one nobody expects: it checks the node over RPC and then shuts
    # bitcoind down - the shutdown is what flushes the coins cache to disk, and
    # with a 500 MB cache it runs for minutes with nothing else to show.
    "settings.utxo.phase_starting": (
        "Checando o no e parando o bitcoind...  ({elapsed})",
        "Checking the node and stopping bitcoind...  ({elapsed})"),
    "settings.utxo.phase_export": (
        "Exportando o UTXO set:  {done} de ~{expected}  ({elapsed})",
        "Exporting the UTXO set:  {done} of ~{expected}  ({elapsed})"),
    "settings.utxo.phase_import": (
        "Importando para o banco:  {done} de ~{expected}  ({elapsed})",
        "Importing into the database:  {done} of ~{expected}  ({elapsed})"),
    "settings.utxo.disk": ("Espaco livre", "Free space"),
    "settings.utxo.disk_value": ("{free} livres de {total}", "{free} free of {total}"),
    "settings.utxo.disk_need": ("pico estimado da reconstrucao: {need}",
                                "estimated rebuild peak: {need}"),
    "settings.utxo.disk_tight": ("ESPACO INSUFICIENTE", "NOT ENOUGH SPACE"),
    "settings.utxo.busy": ("Uma tarefa ja esta em andamento.", "A task is already running."),

    "settings.appearance.language": ("Idioma da interface", "Interface language"),
    "settings.appearance.language_note": (
        "Afeta somente a interface. Arquivos gerados seguem em ingles.",
        "Affects the interface only. Generated files stay in English."),
    "settings.appearance.theme": ("Tema", "Theme"),
    "settings.appearance.font_scale": ("Escala da fonte", "Font scale"),
    # "reopen the window" was wrong and sent people in circles: closing and
    # reopening the dialog changes nothing, because Dear PyGui bakes the font
    # atlas once when the application starts.
    "settings.appearance.font_note": (
        "Salve e reinicie o Mining-Dark - a fonte e definida na abertura do programa.",
        "Save and restart Mining-Dark - the font is set when the program starts."),

    # ----- wallets dialog ----------------------------------------------------
    "wallets.title": ("WALLETS ENCONTRADAS", "FOUND WALLETS"),
    "wallets.empty": ("Nenhuma wallet com saldo encontrada ainda.",
                      "No wallet with a balance found yet."),
    "wallets.count": ("{count} arquivo(s) em {path}", "{count} file(s) in {path}"),
    "wallets.col_date": ("DESCOBERTA EM", "DISCOVERED AT"),
    "wallets.col_address": ("ENDERECO", "ADDRESS"),
    "wallets.col_type": ("TIPO", "TYPE"),
    "wallets.col_btc": ("BTC", "BTC"),
    "wallets.col_file": ("ARQUIVO", "FILE"),
    "wallets.refresh": ("ATUALIZAR", "REFRESH"),
    "wallets.open_file": ("ABRIR .TXT", "OPEN .TXT"),
    "wallets.open_folder": ("ABRIR PASTA", "OPEN FOLDER"),
    "wallets.preview": ("PREVIEW", "PREVIEW"),
    "wallets.reveal": ("revelar chaves privadas", "reveal private keys"),
    "wallets.reveal_warning": (
        "AVISO: estas linhas dao controle total dos fundos. "
        "Nao mostre em stream, screenshot ou compartilhamento de tela.",
        "WARNING: these lines grant full control of the funds. "
        "Do not show them on stream, in a screenshot or while sharing a screen."),
    "wallets.masked_note": (
        "Chaves privadas mascaradas. Marque a caixa acima para revelar.",
        "Private keys are masked. Tick the box above to reveal them."),
    "wallets.select_row": ("Selecione uma wallet na lista.", "Select a wallet from the list."),
    "wallets.opened": ("Aberto: {path}", "Opened: {path}"),
    "wallets.open_failed": ("Nao foi possivel abrir: {error}", "Could not open: {error}"),
    "wallets.csv_note": (
        "summary.csv tambem contem as chaves privadas.",
        "summary.csv also contains the private keys."),

    # ----- backend log lines -------------------------------------------------
    "log.gui_ready": ("Mining-Dark pronto.", "Mining-Dark ready."),
    "log.backend_loaded": ("Backend '{backend}' carregado - modo {mode}, {workers} workers.",
                           "Backend '{backend}' loaded - {mode} mode, {workers} workers."),
    "log.no_keys_on_screen": ("Chaves privadas nunca sao publicadas nesta interface.",
                              "Private keys are never published to this interface."),
    "log.theme_changed": ("Tema alterado para {theme}.", "Theme changed to {theme}."),
    "log.language_changed": ("Idioma alterado para {language}.", "Language changed to {language}."),
    "log.stop_requested": ("Parada solicitada.", "Stop requested."),
    "log.paused": ("Scan pausado.", "Scan paused."),
    "log.resumed": ("Scan retomado.", "Scan resumed."),
    "log.backend_failed": ("Backend falhou: {error}", "Backend failed: {error}"),

    "log.sim_started": ("Sessao simulada iniciada - modo {mode}, {workers} workers",
                        "Simulated session started - {mode} mode, {workers} workers"),
    "log.sim_stopped": ("Sessao simulada encerrada.", "Simulated session ended."),
    "log.sim_disclaimer": (
        "DADOS SIMULADOS: enderecos e saldos sao ficticios e nada e gravado em disco.",
        "SIMULATED DATA: addresses and balances are fictional and nothing is written to disk."),

    # Filler lines the simulator prints so the log looks alive.  They mimic what
    # the real backend logs, so they follow the interface language too.
    "sim.batch": ("Lote de {n} chaves derivado em {ms} ms",
                  "Batch of {n} keys derived in {ms} ms"),
    "sim.worker_checked": ("Worker {w} verificou {n} enderecos",
                           "Worker {w} checked {n} addresses"),
    "sim.cache": ("Cache UTXO: hit ratio {pct}%", "UTXO cache: hit ratio {pct}%"),
    "sim.queue": ("Fila de chaves em {pct}% da capacidade",
                  "Key queue at {pct}% of capacity"),
    "sim.checkpoint": ("Checkpoint gravado - {n} chaves acumuladas",
                       "Checkpoint written - {n} keys accumulated"),
    "sim.queue_full": ("Fila cheia - gerador aguardando os workers",
                       "Queue full - generator waiting on the workers"),
    "sim.reindex": ("Reindexando bucket {w} do indice local",
                    "Reindexing bucket {w} of the local index"),

    "log.db_missing": (
        "Banco UTXO local ausente ou vazio. Abra Configuracoes > Node & UTXO.",
        "Local UTXO database missing or empty. Open Settings > Node & UTXO."),
    "log.db_loaded": ("UTXO local carregado - {count} enderecos indexados (atualizado ha {days}d)",
                      "Local UTXO loaded - {count} addresses indexed (updated {days}d ago)"),
    "log.db_stale": ("Banco com {days} dias - considere atualizar.",
                     "Database is {days} days old - consider updating."),
    "log.scan_started": ("Scanner iniciado - modo {mode}, {workers} workers, {formats} formatos",
                         "Scanner started - {mode} mode, {workers} workers, {formats} formats"),
    "log.scan_stopped": ("Scan encerrado - {keys} chaves, {addresses} enderecos, {found} wallets",
                         "Scan ended - {keys} keys, {addresses} addresses, {found} wallets"),
    "log.wallet_found": ("WALLET ENCONTRADA [{type}] {address} = {btc} BTC",
                         "WALLET FOUND [{type}] {address} = {btc} BTC"),
    "log.wallet_saved": ("Wallet gravada em {file}", "Wallet written to {file}"),
    "log.wallet_save_failed": ("Falha ao gravar wallet: {error}", "Failed to write wallet: {error}"),

    "log.node_starting": ("Iniciando bitcoind...", "Starting bitcoind..."),
    "log.node_started": ("bitcoind iniciado em {path}", "bitcoind started at {path}"),
    "log.node_start_failed": (
        "bitcoind nao subiu: {error}",
        "bitcoind did not come up: {error}"),
    "log.node_start_no_reason": (
        "encerrou durante a inicializacao (veja debug.log)",
        "exited during startup (see debug.log)"),
    "log.node_slow_start": (
        "bitcoind ainda esta carregando o indice de blocos - use Refresh em instantes.",
        "bitcoind is still loading the block index - use Refresh shortly."),
    "log.node_stopping": ("Parando bitcoind...", "Stopping bitcoind..."),
    "log.node_stopped": ("bitcoind parou limpo.", "bitcoind stopped cleanly."),
    "log.node_stop_timeout": ("bitcoind nao parou no tempo esperado.",
                              "bitcoind did not stop in time."),
    "log.node_failed": ("Operacao do node falhou: {error}", "Node operation failed: {error}"),
    "log.node_prune_verify": (
        "A verificacao de partida pediu dados de undo que o prune apagou - isso nao "
        "e corrupcao. Subindo de novo com verificacao mais rasa (-checklevel=1).",
        "Startup verification wanted undo data that pruning deleted - this is not "
        "corruption.  Starting again with a shallower check (-checklevel=1)."),
    "log.task_refused": (
        "A operacao foi recusada. O motivo foi impresso no terminal.",
        "The operation was refused.  The reason was printed to the terminal."),
    "log.utxo_rebuild_started": (
        "Reconstrucao do banco UTXO iniciada - isso demora. Acompanhe no terminal.",
        "UTXO database rebuild started - this takes a while. Follow along in the terminal."),
    "log.utxo_rebuild_done": ("Banco UTXO reconstruido.", "UTXO database rebuilt."),
    "log.utxo_rebuild_failed": ("Reconstrucao falhou: {error}", "Rebuild failed: {error}"),
    "log.disk_tight": (
        "Espaco livre pode nao bastar: {free} livres, pico estimado {need}.",
        "Free space may not be enough: {free} free, estimated peak {need}."),

    # ═══════════════════════════════════════════════════════════════════════════
    #  Command line
    # ═══════════════════════════════════════════════════════════════════════════
    #  Unlike the GUI strings above, these keep their accents.  Dear PyGui bakes
    #  a font atlas and drops glyphs that are not in it, which is why the GUI
    #  half of this file writes "Configuracoes"; a terminal has no such limit,
    #  and stripping accents there would just look broken.

    # ----- doctor: check names -----------------------------------------------
    "doctor.name.binaries": ("Binários", "Binaries"),
    "doctor.name.conf": ("bitcoin.conf", "bitcoin.conf"),
    "doctor.name.snapshot_file": ("Arquivo do snapshot", "Snapshot file"),
    "doctor.name.snapshot_state": ("Snapshot no nó", "Snapshot in the node"),
    "doctor.name.core": ("Bitcoin Core", "Bitcoin Core"),
    "doctor.name.chain": ("Cadeia", "Chain"),
    "doctor.name.db": ("Banco UTXO", "UTXO database"),
    "doctor.name.disk": ("Espaço em disco", "Disk space"),
    "doctor.name.wallets": ("Wallets encontradas", "Found wallets"),

    # ----- doctor: binaries ---------------------------------------------------
    "doctor.binaries.missing": ("faltando: {names}", "missing: {names}"),
    "doctor.binaries.ok": ("bitcoind, bitcoin-cli e bitcoin-utxo-dump presentes",
                           "bitcoind, bitcoin-cli and bitcoin-utxo-dump present"),

    # ----- doctor: bitcoin.conf ----------------------------------------------
    "doctor.conf.missing": ("não encontrado em {dir}", "not found in {dir}"),
    "doctor.conf.full_node": ("nó completo (sem prune)", "full node (no pruning)"),
    "doctor.conf.prune_tight": ("prune={prune} MiB é apertado para assumeutxo",
                                "prune={prune} MiB is tight for assumeutxo"),
    "doctor.conf.prune_tight_fix": (
        "suba para pelo menos {min} em {path} - abaixo disso o Core apaga o undo "
        "do tip e acusa 'Corrupted block database' sem motivo",
        "raise it to at least {min} in {path} - below that Core deletes the tip's "
        "undo data and reports 'Corrupted block database' for no reason"),
    "doctor.conf.checklevel_fix": (
        "num nó podado, defina checklevel=1: os níveis 3-4 releem arquivos de "
        "undo que o prune pode ter apagado",
        "on a pruned node, set checklevel=1: levels 3-4 re-read undo files that "
        "pruning may have deleted"),

    # ----- doctor: snapshot ---------------------------------------------------
    "doctor.snapshot_file.absent": ("ausente (opcional - só faz falta antes de carregar)",
                                    "absent (optional - only needed before loading)"),
    "doctor.snapshot_file.present": ("{size} GB em {dir}", "{size} GB in {dir}"),
    "doctor.snapshot.loaded": ("carregado e validado", "loaded and validated"),
    "doctor.snapshot.loading": ("loadtxoutset em andamento", "loadtxoutset in progress"),
    "doctor.snapshot.loading_fix": ("aguarde terminar - não pare o nó",
                                    "wait for it to finish - do not stop the node"),
    "doctor.snapshot.orphaned": (
        "chainstate_snapshot/ existe sem base_blockhash (load interrompido)",
        "chainstate_snapshot/ exists without base_blockhash (interrupted load)"),
    "doctor.snapshot.orphaned_fix": ("apague com o nó parado: rm -rf {path}",
                                     "delete it with the node stopped: rm -rf {path}"),
    "doctor.snapshot.none": ("nenhum (o nó sincroniza por IBD normal)",
                             "none (the node syncs by normal IBD)"),

    # ----- doctor: the node process ------------------------------------------
    "doctor.core.prune_verify": (
        "o último start falhou na verificação que o prune tornou impossível",
        "the last start failed the verification that pruning made impossible"),
    "doctor.core.prune_verify_fix": (
        "mining-dark node start --shallow-verify  "
        "(NÃO use --reindex: isso descarta a sincronização inteira)",
        "mining-dark node start --shallow-verify  "
        "(do NOT use --reindex: it discards the entire sync)"),
    "doctor.core.see_debug_log": ("veja data/bitcoin-core/debug.log",
                                  "see data/bitcoin-core/debug.log"),
    "doctor.core.stopped": ("parado", "stopped"),
    "doctor.core.no_rpc": ("no ar, mas o RPC ainda não respondeu",
                           "up, but RPC has not answered yet"),
    "doctor.core.no_rpc_fix": ("normal logo após o start - aguarde",
                               "normal right after start - wait"),
    "doctor.core.blocks": ("{blocks} / {headers} blocos", "{blocks} / {headers} blocks"),
    "doctor.core.behind": ("{detail}  ·  faltam {behind}", "{detail}  ·  {behind} to go"),
    "doctor.core.behind_fix": ("aguarde alcançar o tip antes de reconstruir o banco",
                               "wait until it reaches the tip before rebuilding the database"),
    "doctor.core.at_tip": ("{detail}  ·  no tip", "{detail}  ·  at the tip"),

    # ----- doctor: the chain against the network ------------------------------
    #   Compares the node with its peers.  Everything above compares the node
    #   with itself, which a blocked chain passes.
    "doctor.chain.node_stopped": ("nó parado - nada a comparar",
                                  "node stopped - nothing to compare"),
    "doctor.chain.no_rpc": ("RPC ainda não respondeu", "RPC has not answered yet"),
    "doctor.chain.no_peers": ("sem peers conectados ainda",
                              "no peers connected yet"),
    "doctor.chain.detail": ("nó {blocks}  ·  rede {peers}",
                            "node {blocks}  ·  network {peers}"),
    "doctor.chain.syncing": ("{detail}  ·  baixando", "{detail}  ·  downloading"),
    "doctor.chain.at_tip": ("{detail}  ·  acompanhando a rede",
                            "{detail}  ·  keeping up with the network"),
    "doctor.chain.stalled": ("{detail}  ·  {behind} blocos atrás e sem baixar",
                             "{detail}  ·  {behind} blocks behind and not downloading"),
    "doctor.chain.stalled_fix": (
        "veja o fim de data/bitcoin-core/debug.log: o nó se diz sincronizado "
        "mas a rede seguiu sem ele",
        "check the end of data/bitcoin-core/debug.log: the node believes it is "
        "synced but the network moved on without it",
    ),
    "doctor.chain.invalid": (
        "travada: {blocks} bloco(s) marcados inválidos acima de {height}, "
        "tip parado em {tip}",
        "blocked: {blocks} block(s) marked invalid above {height}, "
        "tip stuck at {tip}",
    ),
    "doctor.chain.invalid_fix": (
        "mining-dark node cli reconsiderblock {hash}  "
        "(se falhar de novo, o chainstate perdeu dados - recarregue o snapshot)",
        "mining-dark node cli reconsiderblock {hash}  "
        "(if it fails again the chainstate has lost data - reload the snapshot)",
    ),

    # ----- doctor: the UTXO database -----------------------------------------
    "doctor.db.absent": ("não existe ainda", "does not exist yet"),
    "doctor.db.absent_fix": ("mining-dark utxo update  (precisa do nó no tip)",
                             "mining-dark utxo update  (needs the node at the tip)"),
    "doctor.db.unreadable": ("presente mas ilegível ou vazio",
                             "present but unreadable or empty"),
    "doctor.db.detail": ("{count} endereços  ·  {size} GB  ·  {days} dias",
                         "{count} addresses  ·  {size} GB  ·  {days} days"),

    # ----- doctor: disk and the wallets folder -------------------------------
    "doctor.disk.unmeasurable": ("não foi possível medir", "could not measure"),
    "doctor.disk.detail": ("{free} GB livres  ·  pico de reconstrução ~{need} GB",
                           "{free} GB free  ·  rebuild peak ~{need} GB"),
    "doctor.disk.fix": ("libere espaço antes de reconstruir o banco",
                        "free up space before rebuilding the database"),
    "doctor.wallets.unwritable": ("não dá para gravar em {dir}: {error}",
                                  "cannot write to {dir}: {error}"),
    "doctor.wallets.unwritable_fix": (
        "corrija a permissão ou aponte output.found_wallets_dir no config.yaml "
        "para uma pasta gravável",
        "fix the permission or point output.found_wallets_dir in config.yaml at "
        "a writable folder"),
    "doctor.wallets.detail": ("{dir}  ·  {saved} já salvas", "{dir}  ·  {saved} already saved"),
    "doctor.wallets.world_writable": ("{detail}  ·  pasta gravável por qualquer usuário",
                                      "{detail}  ·  folder writable by any user"),

    # ----- doctor: how the report is printed ---------------------------------
    "doctor.col.check": ("Verificação", "Check"),
    "doctor.col.state": ("Estado", "Status"),
    "doctor.mark.ok": ("OK", "OK"),
    "doctor.mark.warn": ("AVISO", "WARN"),
    "doctor.mark.fail": ("FALHA", "FAIL"),
    "doctor.next_step": ("Próximo passo", "Next step"),
    "doctor.all_set": ("Tudo pronto.", "All set."),
    "doctor.all_set_hint": ("O scanner pode rodar:", "The scanner can run:"),

    # ----- shared by several commands ----------------------------------------
    #  `--help` text is deliberately not in here.  Typer reads it when the module
    #  is imported, before any config has been loaded and so before the language
    #  is known; it stays English, like the code it documents.
    "cli.config.invalid": ("Configuração inválida", "Invalid configuration"),
    "cli.config.hint": ("Corrija o arquivo, ou renomeie-o para rodar com os padrões.",
                        "Fix the file, or rename it to run with the defaults."),
    "cli.config.invalid_value": ("Configuração inválida:", "Invalid configuration:"),
    "cli.db.busy": ("Banco ocupado", "Database busy"),
    "cli.db.missing": ("Banco UTXO local não encontrado.", "Local UTXO database not found."),
    "cli.db.missing_hint": ("Execute:", "Run:"),
    "cli.run": ("Rode:", "Run:"),

    # ----- scan ---------------------------------------------------------------
    "cli.scan.interrupted": ("Scan interrompido.", "Scan interrupted."),
    "cli.scan.db_stale": ("Banco com mais de {days} dias - considere atualizar:",
                          "Database is over {days} days old - consider updating:"),
    "cli.scan.db_loaded": ("UTXO local carregado", "Local UTXO loaded"),
    "cli.scan.db_loaded_detail": ("{count} endereços indexados (atualizado há {days}d)",
                                  "{count} addresses indexed (updated {days}d ago)"),
    "cli.scan.ended": ("Scan encerrado.", "Scan ended."),
    "cli.scan.keys": ("Chaves geradas:", "Keys generated:"),
    "cli.scan.addresses": ("Endereços verificados:", "Addresses checked:"),
    "cli.scan.found": ("Wallets encontradas:", "Wallets found:"),

    # ----- check --------------------------------------------------------------
    "cli.check.found": ("Saldo encontrado!", "Balance found!"),
    "cli.check.empty": ("Sem saldo.", "No balance."),
    "cli.check.confirmed": ("Confirmado", "Confirmed"),

    # ----- found --------------------------------------------------------------
    "cli.found.empty": ("Nenhuma wallet em {dir}/", "No wallets found in {dir}/"),
    "cli.found.title": ("Wallets encontradas ({dir})", "Found wallets ({dir})"),
    "cli.found.col_file": ("Arquivo", "File"),
    "cli.found.col_date": ("Descoberta em", "Discovered at"),
    "cli.found.col_address": ("Endereço", "Address"),
    "cli.found.col_btc": ("BTC confirmado", "Confirmed BTC"),
    "cli.found.see_txt": ("ver .txt", "see .txt"),
    "cli.found.total": ("Total de arquivos:", "Total files:"),

    # ----- keygen -------------------------------------------------------------
    "cli.keygen.title": ("Wallets geradas", "Generated wallets"),
    "cli.keygen.total": ("{count} wallets geradas.", "Generated {count} wallets."),

    # ----- utxo ---------------------------------------------------------------
    "cli.utxo.refused": ("Update recusado", "Update refused"),
    "cli.utxo.title": ("Banco UTXO:", "UTXO database:"),
    "cli.utxo.status": ("Estado", "Status"),
    "cli.utxo.addresses": ("Endereços", "Addresses"),
    "cli.utxo.size": ("Tamanho", "Size"),
    "cli.utxo.updated": ("Atualizado em", "Last updated"),
    "cli.utxo.source": ("Fonte", "Source"),
    "cli.utxo.height": ("Altura do bloco", "Block height"),

    # ----- node start / stop --------------------------------------------------
    "cli.node.verify_vs_prune": ("Verificação x prune", "Verification vs pruning"),
    "cli.node.verify_vs_prune_body": (
        "[yellow]O start anterior falhou na verificação de partida.[/yellow]\n\n"
        "Num nó podado isso normalmente [bold]não é corrupção[/bold]: o Core\n"
        "quis reler os arquivos de undo dos últimos blocos, e o prune já os\n"
        "apagou.  Não use --reindex por causa disso - ele descarta a\n"
        "sincronização inteira para reconstruir a partir de blocos que não\n"
        "existem mais em disco.\n\n"
        "Tente:  [cyan]mining-dark node start --shallow-verify[/cyan]",
        "[yellow]The previous start failed the startup verification.[/yellow]\n\n"
        "On a pruned node that is usually [bold]not corruption[/bold]: Core\n"
        "wanted to re-read the undo files of the most recent blocks, and\n"
        "pruning had already deleted them.  Do not reach for --reindex - it\n"
        "discards the entire sync to rebuild from blocks that no longer\n"
        "exist on disk.\n\n"
        "Try:  [cyan]mining-dark node start --shallow-verify[/cyan]"),
    "cli.node.started": ("bitcoind iniciado", "bitcoind started"),
    "cli.node.started_at": ("em", "at"),
    "cli.node.follow": ("Acompanhe:", "Follow it with:"),
    "cli.node.or": ("ou", "or"),
    "cli.node.stopped_clean": ("bitcoind parou limpo.", "bitcoind stopped cleanly."),
    "cli.node.stop_timeout": ("bitcoind não parou em {seconds}s.",
                              "bitcoind did not stop within {seconds}s."),
    "cli.node.stop_timeout_hint": ("Aguarde mais e re-verifique com",
                                   "Wait longer and check again with"),

    # ----- node status --------------------------------------------------------
    "cli.status.datadir": ("Datadir:", "Datadir:"),
    "cli.status.process": ("Processo:", "Process:"),
    "cli.status.running": ("rodando", "running"),
    "cli.status.stopped": ("parado", "stopped"),
    "cli.status.to_start": ("Para iniciar:", "To start it:"),
    "cli.status.no_rpc": (
        "RPC não respondeu - bitcoind está aquecendo ou as credenciais falharam.",
        "RPC did not answer - bitcoind is warming up or the credentials failed."),
    "cli.status.chain": ("Chain:", "Chain:"),
    "cli.status.pruned": ("podado", "pruned"),
    "cli.status.full": ("completo", "full"),
    "cli.status.blocks": ("Blocos:", "Blocks:"),
    "cli.status.sync": ("Sync:", "Sync:"),
    "cli.status.caught_up": ("no tip", "caught up"),
    "cli.status.size": ("Tamanho:", "Size:"),
    "cli.status.snapshot_active": ("Snapshot (assumeutxo):", "Snapshot (assumeutxo):"),
    "cli.status.active": ("ativo", "active"),
    "cli.status.snapshot_tip": ("Tip (snapshot)", "Tip (snapshot)"),
    "cli.status.snapshot_validated": ("Background validado", "Background validated"),
    "cli.status.snapshot_chainstate": ("Chainstate ativo", "Active chainstate"),
    "cli.status.snapshot_note": (
        "O UTXO set já é utilizável; o background sync revalida a cadeia desde o genesis.",
        "The UTXO set is already usable; the background sync revalidates the chain "
        "from the genesis block."),
    "cli.status.orphaned_title": ("Snapshot incompleto", "Incomplete snapshot"),
    "cli.status.orphaned_body": (
        "[bold yellow]Existe um chainstate_snapshot/ de um load interrompido.[/bold yellow]\n\n"
        "Falta o arquivo base_blockhash, então o Bitcoin Core ignora esse diretório\n"
        "e sincroniza por IBD normal - ele é apenas espaço ocupado.\n\n"
        "Para recuperar o atalho do snapshot:\n"
        "  mining-dark node stop\n"
        "  rm -rf {path}\n"
        "  mining-dark node start\n"
        "  mining-dark node snapshot <arquivo.dat>",
        "[bold yellow]A chainstate_snapshot/ is left over from an interrupted load."
        "[/bold yellow]\n\n"
        "The base_blockhash file is missing, so Bitcoin Core ignores that directory\n"
        "and syncs by normal IBD - it is nothing but occupied space.\n\n"
        "To get the snapshot shortcut back:\n"
        "  mining-dark node stop\n"
        "  rm -rf {path}\n"
        "  mining-dark node start\n"
        "  mining-dark node snapshot <file.dat>"),
    "cli.status.synced": ("Sincronização completa.", "Sync complete."),

    # ----- snapshot download / load ------------------------------------------
    "cli.snap.already": ("Snapshot já baixado:", "Snapshot already downloaded:"),
    "cli.snap.load_with": ("Carregue com:", "Load it with:"),
    "cli.snap.resuming": ("Retomando de {done} GB", "Resuming from {done} GB"),
    "cli.snap.of": ("de {total} GB", "of {total} GB"),
    "cli.snap.downloading": ("Baixando snapshot", "Downloading snapshot"),
    "cli.snap.download_failed": ("Download falhou", "Download failed"),
    "cli.snap.interrupted": ("Interrompido em {done} GB.", "Interrupted at {done} GB."),
    "cli.snap.interrupted_hint": ("Rode de novo para retomar.", "Run it again to resume."),
    "cli.snap.downloaded": ("Snapshot baixado:", "Snapshot downloaded:"),
    "cli.snap.absent_title": ("Sem snapshot", "No snapshot"),
    "cli.snap.absent": ("O snapshot não existe: {path}", "The snapshot does not exist: {path}"),
    "cli.snap.absent_hint": ("Baixe com:", "Download it with:"),
    "cli.snap.partial_title": ("Arquivo parcial", "Partial file"),
    "cli.snap.partial": ("Snapshot incompleto:", "Incomplete snapshot:"),
    "cli.snap.partial_detail": ("{done} GB de {total} GB", "{done} GB of {total} GB"),
    "cli.snap.partial_hint": ("Termine o download:", "Finish the download:"),
    "cli.snap.loading": ("Carregando snapshot:", "Loading snapshot:"),
    "cli.snap.loading_note": (
        "Isso leva de dezenas de minutos a algumas horas. Não interrompa o processo.",
        "This takes tens of minutes to a few hours. Do not interrupt it."),
    "cli.snap.loaded": ("Snapshot carregado.", "Snapshot loaded."),
    "cli.snap.background_note": (
        "O background sync roda sozinho a partir daqui. Acompanhe com:",
        "The background sync runs on its own from here. Follow it with:"),
    "cli.snap.cleanup_note": (
        "O snapshot já foi carregado; o arquivo não será mais lido ({size} GB): {path}",
        "The snapshot has been loaded; the file will never be read again "
        "({size} GB): {path}"),
    "cli.snap.cleanup_ask": (
        "Apagar o .dat agora? Ele já foi carregado e não será reutilizado ({size} GB)",
        "Delete the .dat now? It has been loaded and will not be reused ({size} GB)"),
    "cli.snap.cleanup_kept": ("Arquivo mantido: {path}", "File kept: {path}"),
    "cli.snap.cleanup_failed": ("Não foi possível apagar {path}: {error}",
                                "Could not delete {path}: {error}"),
    "cli.snap.cleanup_freed": ("{size} GB liberados:", "{size} GB freed:"),

    # ----- the database lock --------------------------------------------------
    "lock.rebuilding": (
        "O banco UTXO está sendo reconstruído. Aguarde o update terminar.",
        "The UTXO database is being rebuilt. Wait for the update to finish."),
    "lock.scanning": (
        "Há um scan em andamento usando o banco UTXO.\n"
        "Reconstruir agora deixaria esse scan lendo dados antigos sem aviso "
        "(ele mantém o arquivo antigo aberto) e o espaço em disco não seria "
        "liberado.\n"
        "Encerre o scan e execute novamente.",
        "A scan is running against the UTXO database.\n"
        "Rebuilding now would leave that scan reading stale data with no warning "
        "(it keeps the old file open) and the disk space would not be freed.\n"
        "Stop the scan and run this again."),

    # ----- snapshot download --------------------------------------------------
    "snap.attempt_error": ("{url} (tentativa {attempt}): {error}",
                           "{url} (attempt {attempt}): {error}"),
    "snap.attempt_cut": ("{url} (tentativa {attempt}): conexão encerrada antes do fim",
                         "{url} (attempt {attempt}): connection closed before the end"),
    "snap.download_failed": ("Não foi possível baixar o snapshot.",
                             "Could not download the snapshot."),

    # ----- bitcoin_node: errors the user has to act on ------------------------
    "node.err.missing_binary": ("{name} não encontrado.", "{name} not found."),
    "node.err.missing_binary_setup": (
        "{name} não encontrado. Execute: bash scripts/setup_bitcoin_core.sh",
        "{name} not found. Run: bash scripts/setup_bitcoin_core.sh"),
    "node.err.already_running": ("bitcoind já está rodando.", "bitcoind is already running."),
    "node.err.no_conf": ("bitcoin.conf não existe em {path}\n"
                         "Execute: bash scripts/setup_bitcoin_core.sh",
                         "bitcoin.conf does not exist at {path}\n"
                         "Run: bash scripts/setup_bitcoin_core.sh"),
    "node.err.not_running": ("bitcoind não está rodando. Inicie com: mining-dark node start",
                             "bitcoind is not running. Start it with: mining-dark node start"),
    "node.err.load_in_progress": (
        "Um loadtxoutset parece estar em andamento (chainstate_snapshot/ existe "
        "sem base_blockhash e o bitcoind está rodando).\n"
        "Aguarde o load terminar antes de exportar o UTXO set.",
        "A loadtxoutset appears to be in progress (chainstate_snapshot/ exists "
        "without base_blockhash and bitcoind is running).\n"
        "Wait for the load to finish before exporting the UTXO set."),
    "node.err.orphaned_chainstate": (
        "Load de snapshot interrompido: {path} existe mas não tem base_blockhash.\n"
        "O Bitcoin Core ignora esse diretório e sincroniza por IBD normal, então ele "
        "é só espaço ocupado - e exportar a partir dele geraria um UTXO set truncado.\n"
        "Apague-o com o nó parado: rm -rf {path}",
        "Interrupted snapshot load: {path} exists but has no base_blockhash.\n"
        "Bitcoin Core ignores that directory and syncs by normal IBD, so it is "
        "nothing but occupied space - and exporting from it would produce a "
        "truncated UTXO set.\n"
        "Delete it with the node stopped: rm -rf {path}"),
    "node.err.headers_behind": (
        "Headers sincronizados só até {headers}, mas o snapshot ancora em {height}.\n"
        "O node ainda está baixando os cabeçalhos - isso leva alguns minutos.\n"
        "Acompanhe com: mining-dark node status  (e tente de novo)",
        "Headers are synced only to {headers}, but the snapshot anchors at {height}.\n"
        "The node is still downloading headers - this takes a few minutes.\n"
        "Follow it with: mining-dark node status  (then try again)"),
    "node.err.snapshot_file": ("Arquivo de snapshot não encontrado: {path}",
                               "Snapshot file not found: {path}"),
    "node.err.core_too_old": (
        "Bitcoin Core {got} não tem os parâmetros assumeutxo mais recentes "
        "(precisa de {want}+).\nAtualize com: bash scripts/setup_bitcoin_core.sh",
        "Bitcoin Core {got} does not carry the latest assumeutxo parameters "
        "(needs {want}+).\nUpdate it with: bash scripts/setup_bitcoin_core.sh"),
    "node.err.snapshot_loaded": (
        "Já existe um snapshot carregado (chainstate_snapshot/ presente).\n"
        "Aguarde o background sync terminar - veja: mining-dark node status",
        "A snapshot is already loaded (chainstate_snapshot/ is present).\n"
        "Wait for the background sync to finish - see: mining-dark node status"),
    "node.err.snapshot_loading": (
        "Um loadtxoutset já está em andamento neste datadir.\n"
        "Acompanhe em: bitcoin-cli -datadir=... getchainstates",
        "A loadtxoutset is already in progress in this datadir.\n"
        "Follow it with: bitcoin-cli -datadir=... getchainstates"),
    "node.err.snapshot_orphaned": (
        "Existe um chainstate_snapshot/ incompleto de um load interrompido em {path}.\n"
        "O Core não reaproveita esse diretório - apague-o com o nó parado e tente "
        "de novo:\n  mining-dark node stop\n  rm -rf {path}\n  mining-dark node start",
        "An incomplete chainstate_snapshot/ from an interrupted load exists at {path}.\n"
        "Core will not reuse that directory - delete it with the node stopped and "
        "try again:\n  mining-dark node stop\n  rm -rf {path}\n  mining-dark node start"),

    # ═══════════════════════════════════════════════════════════════════════════
    #  utxo update - the long rebuild, and every way it can refuse to start
    # ═══════════════════════════════════════════════════════════════════════════
    "utxo.err.title": ("Erro", "Error"),
    "utxo.err.missing_binary": (
        "{name} não encontrado. Execute: bash scripts/setup_bitcoin_core.sh",
        "{name} not found. Run: bash scripts/setup_bitcoin_core.sh"),
    "utxo.err.core_down": (
        "Bitcoin Core não está rodando ou não respondeu.\n"
        "Inicie com: mining-dark node start (ou bitcoind -datadir={datadir})",
        "Bitcoin Core is not running or did not answer.\n"
        "Start it with: mining-dark node start (or bitcoind -datadir={datadir})"),

    # ----- why the node is not ready to be dumped ----------------------------
    "utxo.sync.ibd": ("O nó ainda está em initial block download.",
                      "The node is still in initial block download."),
    "utxo.sync.behind": ("Faltam [cyan]{behind}[/cyan] blocos para alcançar o tip.",
                         "[cyan]{behind}[/cyan] blocks short of the tip."),
    "utxo.sync.verifying": ("A verificação da cadeia ainda não terminou.",
                            "Chain verification has not finished yet."),
    "utxo.sync.incomplete_title": ("Sincronização incompleta", "Incomplete sync"),
    "utxo.sync.incomplete": ("Bitcoin Core ainda está sincronizando.",
                             "Bitcoin Core is still syncing."),
    "utxo.sync.progress": ("Progresso", "Progress"),
    "utxo.sync.blocks": ("Blocos", "Blocks"),
    "utxo.sync.wait": ("Aguarde a sincronização completa e execute novamente.",
                       "Wait for the sync to complete and run this again."),

    # ----- the export ---------------------------------------------------------
    "utxo.checking_core": ("Verificando Bitcoin Core...", "Checking Bitcoin Core..."),
    "utxo.net": ("Rede", "Network"),
    "utxo.blocks": ("Blocos", "Blocks"),
    "utxo.synced_pct": ("Sincronizado", "Synced"),
    "utxo.synced_go": ("Sincronizado! Iniciando export...", "Synced! Starting the export..."),
    "utxo.up_to_date": ("UTXO já está atualizado", "UTXO is already up to date"),
    "utxo.up_to_date_detail": ("(há {days} dias). Use --force para forçar.",
                               "({days} days old). Use --force to rebuild anyway."),
    "utxo.chainstate_bad": ("Chainstate inconsistente", "Inconsistent chainstate"),
    "utxo.exporting": ("Exportando UTXO set do Bitcoin Core...",
                       "Exporting the UTXO set from Bitcoin Core..."),
    "utxo.export_done": ("Export concluído", "Export finished"),
    "utxo.dump_failed": (
        "bitcoin-utxo-dump falhou (exit {code}).\n"
        "Chainstate esperado em: {chainstate}\n"
        "Verifique:\n"
        "  - Bitcoin Core parou (bitcoin-cli -datadir={datadir} stop)\n"
        "  - Sincronização está completa (verificationprogress >= 0.9999)\n\n"
        "Erro do bitcoin-utxo-dump:\n{error}",
        "bitcoin-utxo-dump failed (exit {code}).\n"
        "Chainstate expected at: {chainstate}\n"
        "Check that:\n"
        "  - Bitcoin Core has stopped (bitcoin-cli -datadir={datadir} stop)\n"
        "  - the sync is complete (verificationprogress >= 0.9999)\n\n"
        "Error from bitcoin-utxo-dump:\n{error}"),
    "utxo.dump_no_csv": (
        "bitcoin-utxo-dump terminou sem gerar o CSV.\n"
        "Chainstate lido: {chainstate}\n\n"
        "Saída do bitcoin-utxo-dump:\n{output}",
        "bitcoin-utxo-dump finished without producing the CSV.\n"
        "Chainstate read: {chainstate}\n\n"
        "Output from bitcoin-utxo-dump:\n{output}"),
    "utxo.csv_written": ("CSV gerado: {name} ({size} MB)", "CSV written: {name} ({size} MB)"),

    # ----- stopping and restarting the node ----------------------------------
    "utxo.stopping_core": ("Parando o Bitcoin Core", "Stopping Bitcoin Core"),
    "utxo.stopping_core_why": (
        "(necessário: o chainstate em disco só fica completo após o shutdown)",
        "(required: the chainstate on disk is only complete after shutdown)"),
    "utxo.stop_failed_title": ("Shutdown falhou", "Shutdown failed"),
    "utxo.stop_failed": (
        "O Bitcoin Core não parou dentro do tempo limite.\n\n"
        "Exportar com o nó rodando produziria um UTXO set incompleto, "
        "então o update foi abortado.",
        "Bitcoin Core did not stop within the timeout.\n\n"
        "Exporting with the node running would produce an incomplete UTXO set, "
        "so the update was aborted."),
    "utxo.node_stopped": ("Nó parado.", "Node stopped."),
    "utxo.restarting": ("Reiniciando o Bitcoin Core...", "Restarting Bitcoin Core..."),
    "utxo.restart_failed": ("Não foi possível reiniciar o nó automaticamente: {error}",
                            "Could not restart the node automatically: {error}"),
    "utxo.csv_kept": ("CSV mantido para inspeção ou reimportação: {path}",
                      "CSV kept for inspection or re-import: {path}"),

    # ----- the import ---------------------------------------------------------
    "utxo.discarding_temps": ("Descartando temporários de um update interrompido ({size} GB)",
                              "Discarding temporaries from an interrupted update ({size} GB)"),
    "utxo.importing": ("Importando para SQLite...", "Importing into SQLite..."),
    "utxo.importing_n": ("Importando... {count} UTXOs", "Importing... {count} UTXOs"),
    "utxo.counting": ("Contando endereços únicos...", "Counting unique addresses..."),
    "utxo.import_done": ("Import concluído", "Import finished"),
    "utxo.csv_invalid": ("Arquivo CSV inválido ou vazio: {path}",
                         "Invalid or empty CSV file: {path}"),
    "utxo.import_empty": (
        "O import não produziu nenhum endereço.\n"
        "O bitcoin-utxo-dump termina com exit 0 mesmo quando não exporta nada "
        "(chainstate travado pelo nó, diretório errado, disco cheio), então o "
        "CSV vazio é o único sinal.\n"
        "O banco anterior foi mantido.",
        "The import produced no addresses at all.\n"
        "bitcoin-utxo-dump exits 0 even when it exports nothing (chainstate locked "
        "by the node, wrong directory, full disk), so the empty CSV is the only "
        "signal.\n"
        "The previous database was kept."),
    "utxo.import_shrunk": (
        "O import tem {count} endereços, contra {previous} no banco atual - uma "
        "queda de mais de {pct}%.\n"
        "O UTXO set não encolhe assim; isso indica export parcial.\n"
        "O banco anterior foi mantido. Use 'mining-dark utxo update --file' com um "
        "CSV verificado para forçar.",
        "The import holds {count} addresses against {previous} in the current "
        "database - a drop of more than {pct}%.\n"
        "The UTXO set does not shrink like that; this points at a partial export.\n"
        "The previous database was kept. Use 'mining-dark utxo update --file' with "
        "a verified CSV to force it."),
    "utxo.imported": ("UTXO set importado com sucesso!", "UTXO set imported successfully!"),
    "utxo.imported.addresses": ("Endereços indexados", "Addresses indexed"),
    "utxo.imported.source": ("Fonte", "Source"),
    "utxo.imported.path": ("Banco salvo em", "Database written to"),
    "utxo.imported.size": ("Tamanho", "Size"),

    # ----- exporting straight from an assumeutxo snapshot --------------------
    "utxo.snap.hint": (
        "\n\n[yellow]Há um snapshot assumeutxo carregado e validado em "
        "chainstate_snapshot/.[/yellow]\n"
        "O UTXO set dele está completo mesmo com o nó parado. Para exportar a "
        "partir dele:\n"
        "  [cyan]mining-dark utxo update --from-snapshot[/cyan]",
        "\n\n[yellow]There is a loaded and validated assumeutxo snapshot in "
        "chainstate_snapshot/.[/yellow]\n"
        "Its UTXO set is complete even with the node stopped. To export from it:\n"
        "  [cyan]mining-dark utxo update --from-snapshot[/cyan]"),
    "utxo.snap.none_title": ("Sem snapshot", "No snapshot"),
    "utxo.snap.none": (
        "Não há snapshot validado para exportar (estado: {state}).\n\n"
        "--from-snapshot exige um chainstate_snapshot/ com base_blockhash, que o "
        "Bitcoin Core só grava depois de validar o snapshot inteiro contra o hash "
        "embutido.",
        "There is no validated snapshot to export from (state: {state}).\n\n"
        "--from-snapshot needs a chainstate_snapshot/ with base_blockhash, which "
        "Bitcoin Core writes only after validating the whole snapshot against its "
        "built-in hash."),
    "utxo.snap.node_up_title": ("Nó ativo", "Node running"),
    "utxo.snap.node_up": (
        "O Bitcoin Core está rodando.\n\n"
        "Com o nó ativo o chainstate em disco pode estar atrás do tip. Pare o nó e "
        "execute novamente:\n"
        "  [cyan]mining-dark node stop[/cyan]",
        "Bitcoin Core is running.\n\n"
        "With the node up, the chainstate on disk can lag the tip. Stop the node "
        "and run this again:\n"
        "  [cyan]mining-dark node stop[/cyan]"),
    "utxo.snap.suspect_title": ("Chainstate suspeito", "Suspect chainstate"),
    "utxo.snap.suspect": (
        "O Bitcoin Core reclamou do banco recentemente:\n\n"
        "  {complaint}\n\n"
        "Exportar daqui pode produzir um UTXO set errado sem nada acusar.\n"
        "A reclamação pode ser sobre outro chainstate que não o do snapshot - o log\n"
        "não diz qual. Se você verificou que o snapshot está íntegro, repita com:\n"
        "  [cyan]--ignore-node-errors[/cyan]",
        "Bitcoin Core complained about the database recently:\n\n"
        "  {complaint}\n\n"
        "Exporting from here could produce a wrong UTXO set with nothing reporting "
        "it.\nThe complaint may be about a chainstate other than the snapshot's - "
        "the log\ndoes not say which. If you have verified the snapshot is intact, "
        "repeat with:\n"
        "  [cyan]--ignore-node-errors[/cyan]"),
    "utxo.snap.ignored": ("Aviso ignorado por --ignore-node-errors:",
                          "Warning overridden by --ignore-node-errors:"),
    "utxo.snap.title": ("Snapshot", "Snapshot"),
    "utxo.snap.exporting": (
        "Exportando direto do snapshot assumeutxo.\n\n"
        "O nó está parado e as checagens por RPC foram puladas. Isso é seguro aqui "
        "porque\n"
        "o Bitcoin Core só grava [cyan]base_blockhash[/cyan] depois de desserializar "
        "cada coin e\n"
        "conferir o UTXO set inteiro contra o hash embutido - o conjunto está "
        "completo na\n"
        "altura do snapshot, ainda que a cadeia não tenha sido revalidada desde o "
        "genesis.",
        "Exporting straight from the assumeutxo snapshot.\n\n"
        "The node is stopped and the RPC checks were skipped. That is safe here "
        "because\n"
        "Bitcoin Core writes [cyan]base_blockhash[/cyan] only after deserialising "
        "every coin and\n"
        "matching the whole UTXO set against its built-in hash - the set is complete "
        "at the\n"
        "snapshot height, even though the chain has not been revalidated from the "
        "genesis block."),
    "utxo.snap.height": ("Altura do snapshot: {height}", "Snapshot height: {height}"),
    "utxo.snap.background": (
        "Background sync do assumeutxo ainda em andamento.\n\n"
        "O UTXO set exportado vem de [cyan]chainstate_snapshot/[/cyan] e está "
        "completo na altura do tip (validado contra o hash embutido no\n"
        "Bitcoin Core), mas o nó ainda não revalidou a cadeia desde o genesis.",
        "The assumeutxo background sync is still running.\n\n"
        "The exported UTXO set comes from [cyan]chainstate_snapshot/[/cyan] and is "
        "complete at the tip height (validated against the hash built into\n"
        "Bitcoin Core), but the node has not revalidated the chain from the genesis "
        "block yet."),

    # ═══════════════════════════════════════════════════════════════════════════
    #  The interactive setup menu shown before a scan
    # ═══════════════════════════════════════════════════════════════════════════
    "menu.rule.utxo": ("  Banco UTXO  ", "  UTXO Database  "),
    "menu.rule.config": ("  Configuração  ", "  Configuration  "),
    "menu.rule.hd": ("  HD Wallet  ", "  HD Wallet  "),

    "menu.utxo.title": ("  Banco UTXO Local  ", "  Local UTXO Database  "),
    "menu.utxo.status": ("Status", "Status"),
    "menu.utxo.addresses": ("Endereços indexados", "Indexed addresses"),
    "menu.utxo.updated": ("Última atualização", "Last updated"),
    "menu.utxo.size": ("Tamanho", "Size"),
    "menu.utxo.source": ("Fonte", "Source"),
    "menu.utxo.block": ("Bloco", "Block"),
    "menu.utxo.ready": ("PRONTO", "READY"),
    "menu.utxo.outdated": ("DESATUALIZADO", "OUTDATED"),
    "menu.utxo.missing": ("NÃO ENCONTRADO", "NOT FOUND"),
    "menu.utxo.run_cmd": ("[dim]Execute:[/dim] [cyan]mining-dark utxo update[/cyan]",
                          "[dim]Run:[/dim] [cyan]mining-dark utxo update[/cyan]"),
    "menu.utxo.days_ago": ("d atrás", "d ago"),
    "menu.utxo.missing_msg": (
        "  [red]O banco UTXO é obrigatório para iniciar o scan.[/red]\n"
        "  Aguarde a sincronização do nó e execute:\n"
        "  [cyan]mining-dark utxo update[/cyan]\n",
        "  [red]The UTXO database is required to start a scan.[/red]\n"
        "  Wait for the node to sync and run:\n"
        "  [cyan]mining-dark utxo update[/cyan]\n"),
    "menu.utxo.outdated_msg": (
        "  [yellow]O banco está desatualizado (mais de {days} dias).[/yellow]\n"
        "  Recomendado atualizar antes de continuar:\n"
        "  [cyan]mining-dark utxo update[/cyan]\n",
        "  [yellow]The database is out of date (over {days} days).[/yellow]\n"
        "  Updating before continuing is recommended:\n"
        "  [cyan]mining-dark utxo update[/cyan]\n"),

    "menu.confirm.continue": ("  Continuar para configuração?", "  Continue to configuration?"),
    "menu.confirm.start": ("  [bold green]Iniciar scan?[/bold green]",
                           "  [bold green]Start the scan?[/bold green]"),
    "menu.cancelled": ("\n  Cancelado.\n", "\n  Cancelled.\n"),

    "menu.mode.random": (
        "[bold]  [1][/bold] Modo [cyan]Random[/cyan]   - gera chaves privadas aleatórias",
        "[bold]  [1][/bold] [cyan]Random[/cyan] mode   - generates random private keys"),
    "menu.mode.hd": (
        "[bold]  [2][/bold] Modo [cyan]HD Wallet[/cyan] - deriva chaves de seeds "
        "BIP39 via BIP32/44/49/84/86",
        "[bold]  [2][/bold] [cyan]HD Wallet[/cyan] mode - derives keys from BIP39 "
        "seeds via BIP32/44/49/84/86"),
    "menu.mode.prompt": ("  Escolha o modo", "  Choose the mode"),

    "menu.workers.hint": (
        "  Workers = tarefas assíncronas paralelas verificando saldo.\n"
        "  [dim]Recomendado: 5-20. Mais workers = mais consultas ao banco por segundo.[/dim]",
        "  Workers = parallel async tasks checking balances.\n"
        "  [dim]Recommended: 5-20. More workers = more database queries per second.[/dim]"),
    "menu.workers.prompt": ("  Número de workers", "  Number of workers"),
    "menu.out_of_range": ("  [red]Valor fora do intervalo permitido ({low}-{high}).[/red]",
                          "  [red]Value outside the allowed range ({low}-{high}).[/red]"),

    "menu.child.hint": (
        "  Child count = endereços derivados por mnemônica.\n"
        "  [dim]Carteiras reais usam gap limit de 20 - cobre índices 0 a 19.[/dim]\n"
        "  [dim]Mais filhas = maior cobertura, porém mais lento.[/dim]",
        "  Child count = addresses derived per mnemonic.\n"
        "  [dim]Real wallets use a gap limit of 20 - that covers indices 0 to 19.[/dim]\n"
        "  [dim]More children = wider coverage, but slower.[/dim]"),
    "menu.child.prompt": ("  Child count", "  Child count"),

    "menu.summary.config": ("Configuração", "Configuration"),
    "menu.summary.addresses": ("Tipos de endereço verificados", "Address types checked"),
    "menu.summary.mode": ("Modo", "Mode"),
    "menu.summary.workers": ("Workers", "Workers"),
    "menu.summary.child": ("Child count", "Child count"),
    "menu.summary.keys_per_seed": ("chaves/mnemônica", "keys/mnemonic"),

    "menu.addr.p2pkh": ("Legacy comprimida", "Compressed legacy"),
    "menu.addr.p2pkh_u": ("Legacy não comprimida", "Uncompressed legacy"),
    "menu.addr.p2pkh_u_note": ("(era Satoshi)", "(Satoshi era)"),
    "menu.addr.p2sh": ("Nested SegWit", "Nested SegWit"),
    "menu.addr.p2wpkh": ("Native SegWit", "Native SegWit"),
    "menu.addr.p2wsh": ("Witness Script Hash", "Witness Script Hash"),
    "menu.addr.p2tr": ("Taproot", "Taproot"),

    # ═══════════════════════════════════════════════════════════════════════════
    #  The live scan dashboard (rich, `mining-dark scan`)
    # ═══════════════════════════════════════════════════════════════════════════
    #  This panel was half translated: English stat labels beside Portuguese
    #  database ones, in the same table.
    "dash.stats.title": ("Estatísticas", "Statistics"),
    "dash.stats.elapsed": ("Tempo decorrido", "Elapsed time"),
    "dash.stats.keys": ("Chaves geradas", "Keys generated"),
    "dash.stats.addresses": ("Endereços verificados", "Addresses checked"),
    "dash.stats.kps": ("Chaves / segundo", "Keys / second"),
    "dash.stats.cps": ("Verificações / segundo", "Checks / second"),
    "dash.stats.found": ("Wallets ENCONTRADAS", "Wallets FOUND"),
    "dash.stats.btc": ("Total de BTC encontrado", "Total BTC found"),

    "dash.utxo.title": ("Banco UTXO", "UTXO Database"),
    "dash.utxo.status": ("Status", "Status"),
    "dash.utxo.missing": ("Não encontrado", "Not found"),
    "dash.utxo.outdated": ("Desatualizado", "Out of date"),
    "dash.utxo.current": ("Atualizado", "Up to date"),
    "dash.utxo.updated": ("Última atualização", "Last updated"),
    "dash.utxo.addresses": ("Endereços indexados", "Indexed addresses"),
    "dash.utxo.size": ("Tamanho do banco", "Database size"),
    "dash.utxo.source": ("Fonte", "Source"),
    "dash.utxo.run_cmd": ("[dim]Execute:[/dim] [cyan]mining-dark utxo update[/cyan]",
                          "[dim]Run:[/dim] [cyan]mining-dark utxo update[/cyan]"),

    # ----- config errors ------------------------------------------------------
    "config.err.missing": ("O arquivo de configuração não existe: {path}",
                           "The configuration file does not exist: {path}"),
    "config.err.unreadable": ("Não foi possível ler {path}: {error}",
                              "Could not read {path}: {error}"),
    "config.err.bad_yaml": ("{path} não é um YAML válido.\n\n{error}",
                            "{path} is not valid YAML.\n\n{error}"),
    "config.err.not_a_mapping": (
        "{path} deveria conter um mapeamento de seções, não {got}.",
        "{path} should hold a mapping of sections, not {got}."),
    "config.err.bad_values": ("Valores inválidos em {path}:\n\n  {problems}",
                              "Invalid values in {path}:\n\n  {problems}"),
}

_current: str = DEFAULT_LANGUAGE


def set_language(code: str) -> str:
    """Switch the active language.  Unknown codes fall back to the default."""
    global _current
    _current = code if code in LANGUAGES else DEFAULT_LANGUAGE
    return _current


def get_language() -> str:
    return _current


def language_label(code: str) -> str:
    return LANGUAGES.get(code, code)


def t(key: str, **kwargs: object) -> str:
    """
    Translate `key` into the active language.

    Missing keys return the key itself rather than raising - a visible
    `settings.nope` on screen is far easier to spot and fix than a crash in a
    render loop.
    """
    entry = _STRINGS.get(key)
    if entry is None:
        return key

    text = entry[0] if _current == "pt" else entry[1]
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError):
        return text
