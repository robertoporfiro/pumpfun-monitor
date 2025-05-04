# src/main.py
import sys
import os
import logging
from flask import Flask, render_template, send_from_directory

# Adiciona o diretório 'src' ao path para encontrar 'routes'
sys.path.insert(0, os.path.dirname(__file__))

# Importar o blueprint APÓS ajustar o path
from routes.api import api_bp

# Configuração básica de logging para a app Flask
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

# Ajusta o path para encontrar a pasta 'static' que está um nível acima de 'src'
static_folder_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static')
app = Flask(__name__, static_folder=static_folder_path, static_url_path='')

# Registar os blueprints
app.register_blueprint(api_bp)

# Rota principal para servir o index.html da pasta static definida
@app.route("/")
def index():
    # send_static_file busca dentro da static_folder configurada na app
    return app.send_static_file("index.html")

# Rota para servir outros ficheiros estáticos se necessário (ex: CSS, JS)
# O static_url_path='' já deve fazer isso, mas podemos ser explícitos
# @app.route('/<path:filename>')
# def serve_static(filename):
#     return send_from_directory(app.static_folder, filename)

if __name__ == "__main__":
    logger.info("Iniciando servidor Flask para Gestor do Bot...")
    # host='0.0.0.0' é crucial para Docker
    # debug=False é importante para produção
    app.run(host="0.0.0.0", port=5000, debug=True)