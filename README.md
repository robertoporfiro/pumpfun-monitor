# PumpFun Portal Monitor

Este projeto é um monitor assíncrono para "tokens graduados" da plataforma Pump.fun na blockchain Solana. Ele utiliza websockets para receber notificações em tempo real, verifica a segurança dos tokens usando APIs externas (como RugCheck.xyz) e pode, opcionalmente, executar estratégias de compra automática através da API Sniperoo ou monitorizar a atividade de mercado através da API DexScreener.

## Funcionalidades

*   Monitorização em tempo real de eventos de "migração" (graduação) de tokens via WebSocket do Pump Portal.
*   Verificação de segurança de tokens utilizando a API RugCheck.xyz (configurável).
*   Filtros de segurança pré-compra/monitorização (score RugCheck, liquidez inicial, posse do criador, posse de holders individuais, insiders detectados, etc.).
*   Modo de compra automática (AutoBuy) via API Sniperoo, com configuração de estratégia.
*   Modo de monitorização de mercado pós-graduação, com critérios configuráveis (volume, número de compras, variação de preço, FDV, etc.) para disparo de compra via Sniperoo.
*   Gestão de estado para evitar processamento duplicado de tokens.
*   Configuração flexível através de variáveis de ambiente (ficheiro `.env`).
*   Logging detalhado das operações.

## Configuração

1.  **Clonar o repositório (ou extrair os ficheiros).**
2.  **Instalar dependências:**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Criar um ficheiro `.env` na raiz do projeto** (ao lado de `docker-compose.yml`) com as seguintes variáveis (ajuste os valores conforme necessário):

    ```dotenv
    # URLs e Endpoints
    WS_URL="wss://pumpportal.fun/api/data"
    RUGCHECK_API_ENDPOINT="https://api.rugcheck.xyz/v3/tokens/{}/report" # O {} é onde o mint address será inserido
    SNIPEROO_BUY_ENDPOINT="https://api.sniperoo.app/trading/buy-token?toastFrontendId=0"

    # Chaves de API e Wallet
    SNIPEROO_API_KEY="SUA_CHAVE_API_SNIPEROO"
    SNIPEROO_WALLET_ADDRESS="SEU_ENDERECO_WALLET_SOLANA"

    # Configurações Gerais
    LOG_LEVEL="INFO" # DEBUG, INFO, WARNING, ERROR
    API_TIMEOUT=15 # Segundos
    DATA_DIR="/data" # Diretório para logs e ficheiros de estado (dentro do container Docker)

    # Configurações de Verificação (RugCheck)
    CHECK_RETRY_DELAY_SECONDS=10
    CHECK_MAX_DURATION_SECONDS=120

    # Configurações de Estado
    SAVE_INTERVAL_SECONDS=300

    # --- Filtros de Segurança Pré-Monitoramento/Compra ---
    MIN_RUGCHECK_SCORE=0.85
    MIN_INITIAL_LIQUIDITY=3000 # Em USD (via RugCheck)
    FILTER_MAX_INSIDERS_DETECTED=0
    FILTER_MAX_SINGLE_HOLDER_PCT=15.0 # Percentagem máxima para um único holder (excluindo LP/Criador)
    FILTER_MAX_CREATOR_HOLDING_PCT=1.0 # Percentagem máxima para o criador

    # --- Configurações Sniperoo --- 
    SNIPEROO_BUY_AMOUNT_SOL=0.05
    SNIPEROO_AUTOSELL_ENABLED=True
    SNIPEROO_AUTOSELL_PROFIT_PCT=30.0
    SNIPEROO_AUTOSELL_STOPLOSS_PCT=15.0
    SNIPEROO_PRIORITY_FEE=100000 # MicroLamports
    SNIPEROO_SLIPPAGE_BPS=1500 # 15%
    SNIPEROO_MAX_RETRIES=2

    # --- Modo de Operação --- 
    # Defina UM destes como True
    SNIPEROO_USE_AUTOBUY_MODE=False # Se True, usa a estratégia AutoBuy da Sniperoo
    # Se SNIPEROO_USE_AUTOBUY_MODE=False, o bot usará o Market Monitor interno

    # --- Configurações do Market Monitor (Usado APENAS se SNIPEROO_USE_AUTOBUY_MODE=False) ---
    MARKET_MONITOR_DURATION=300 # Segundos
    MARKET_POLL_INTERVAL=10 # Segundos
    MARKET_MIN_VOLUME_M5=1000 # USD
    MARKET_MIN_BUYS_M5=10
    MARKET_PRICE_DROP_TOLERANCE=0.20 # 20% de queda máxima permitida desde o preço inicial
    MARKET_MIN_BUY_SELL_RATIO=0.60 # 60% de compras nos últimos 5 min
    MARKET_MAX_FDV=200000 # USD
    MARKET_MIN_H1_PRICE_CHANGE=-15.0 # Variação mínima de preço na última hora (-15%)

    # --- Configurações da Estratégia AutoBuy Sniperoo (Usado APENAS se SNIPEROO_USE_AUTOBUY_MODE=True) ---
    # (Ajuste conforme a documentação da Sniperoo)
    SNIPEROO_AUTOBUY_PRICE_METRIC_TYPE="price_change"
    SNIPEROO_AUTOBUY_PRICE_METRIC_PLUSMINUS="minus"
    SNIPEROO_AUTOBUY_PRICE_METRIC_ENABLED=True
    SNIPEROO_AUTOBUY_PRICE_METRIC_MIN=0
    SNIPEROO_AUTOBUY_PRICE_METRIC_MAX=0
    SNIPEROO_AUTOBUY_EXPIRES_VALUE=10
    SNIPEROO_AUTOBUY_EXPIRES_UNIT="minutes"
    ```

## Execução

### Usando Docker (Recomendado)

O projeto inclui um `Dockerfile` e um `docker-compose.yml` para facilitar a execução.

1.  Certifique-se de que tem o Docker e o Docker Compose instalados.
2.  Certifique-se de que criou o ficheiro `.env` na raiz do projeto.
3.  Execute o seguinte comando na raiz do projeto:

    ```bash
    docker-compose up --build -d
    ```

4.  Para ver os logs:

    ```bash
    docker-compose logs -f
    ```

5.  Para parar o monitor:

    ```bash
    docker-compose down
    ```

### Diretamente (Para Desenvolvimento/Teste)

1.  Certifique-se de que criou e configurou o ficheiro `.env`.
2.  Execute o script principal:

    ```bash
    python -m pumpfun_portal_monitor.monitor
    ```
    *(Nota: Pode ser necessário ajustar `DATA_DIR` no `.env` para um caminho local como `./data` se não estiver a usar Docker)*

## Disclaimer

Este software é fornecido "como está", sem garantias de qualquer tipo. A negociação de criptomoedas envolve riscos significativos. Utilize por sua conta e risco. O autor não se responsabiliza por quaisquer perdas financeiras.

