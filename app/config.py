import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Base config."""
    SECRET_KEY = os.getenv('SECRET_KEY', 'default-secret-key')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    HUB_API_KEY = os.getenv('HUB_API_KEY')
    
    L_KALENDER_URL = os.getenv('L_KALENDER_URL', 'https://kalender-hml.licilink.com.br')
    L_GEDO_URL = os.getenv('L_GEDO_URL', 'https://gedo-hml.licilink.com.br')
    L_HUNT_URL = os.getenv('L_HUNT_URL', 'https://hunt-hml.licilink.com.br')

class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL', 'postgresql://hub_user:hub_password@localhost:5432/hub_db')

class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:' # SQLite apenas para testes automatizados unitários se necessário

class StagingConfig(Config):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL')

class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL')

config_by_name = dict(
    development=DevelopmentConfig,
    testing=TestingConfig,
    staging=StagingConfig,
    production=ProductionConfig
)
