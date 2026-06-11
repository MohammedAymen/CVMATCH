import sys
from loguru import logger

logger.remove()
logger.add(sys.stdout,
           level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - <level>{message}</level>")
logger.add("logs/app.log",
           rotation="1 day",
           level="DEBUG",
           format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name} - {message}")