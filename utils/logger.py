import logging
import os
from datetime import datetime
from .config import LOG_DIR

def setup_logger(name="mucli", level=logging.DEBUG):
    """Sets up a logger that writes to a timestamped file in the logs directory."""
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR, exist_ok=True)
    
    # Generate log filename with current date
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file = os.path.join(LOG_DIR, f"{name}_{timestamp}.log")
    
    # Configure logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Create file handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(level)
    
    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    
    # Add handler to logger if not already present
    if not logger.handlers:
        logger.addHandler(file_handler)
    
    # Optional: console handler for errors
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.ERROR)
    console_handler.setFormatter(formatter)
    # logger.addHandler(console_handler) # We already have rich UI showing errors

    return logger

# Global logger instance
logger = setup_logger()
