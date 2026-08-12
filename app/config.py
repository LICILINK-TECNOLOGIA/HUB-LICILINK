import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    L_KALENDER_URL = os.environ.get("L_KALENDER_URL")
    L_GEDO_URL = os.environ.get("L_GEDO_URL")
    # Define se o ambiente é produção para forçar HTTPS (por padrão, assume True a menos que FLASK_ENV=development)
    IS_PRODUCTION = os.environ.get("FLASK_ENV", "production") != "development"
