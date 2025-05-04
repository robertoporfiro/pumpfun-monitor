# Use uma imagem base oficial do Python (pode ser slim)
FROM python:3.10-slim

# Define o diretório de trabalho
WORKDIR /app

# Instala dependências
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código da aplicação
COPY ./src /app/src
COPY ./static /app/static

# Expõe a porta que o Flask vai usar
EXPOSE 5000

# Comando para iniciar a aplicação (usando o main.py dentro de src)
CMD ["python", "src/main.py"]