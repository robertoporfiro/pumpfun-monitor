# Use uma imagem base oficial do Python
FROM python:3.10-slim

# Define o diretório de trabalho dentro do container
WORKDIR /app

# Copia o arquivo de dependências PRIMEIRO para aproveitar o cache do Docker
COPY requirements.txt ./

# Instala as dependências
RUN pip install --no-cache-dir --default-timeout=100 -r requirements.txt

# Copia o código da aplicação (o pacote inteiro) para dentro do container
COPY ./pumpfun_portal_monitor ./pumpfun_portal_monitor

# O VOLUME /data será gerenciado pelo docker-compose, não precisa criar aqui
# RUN mkdir /data
# VOLUME /data # Declarar no compose é mais comum

# Define o comando padrão para rodar a aplicação usando o módulo
CMD ["python", "-u", "-m", "pumpfun_portal_monitor.monitor"]
# Adicionado -u para unbuffered output, melhor para ver logs com docker-compose logs