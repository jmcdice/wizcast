#!/usr/bin/env python
# main.py (in gitcast_project root)

import sys
import os
import logging

# Add the project root to sys.path to allow imports from gitcast_library
# This is useful if you run `python main.py` from the gitcast_project directory.
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from gitcast_library.config import AppConfig
from gitcast_library.orchestrator import GitCastOrchestrator
from gitcast_library.utils import setup_logging

def run_gitcast():
    try:
        # Initialize configuration
        config = AppConfig()
        
        # Setup logging with configured options
        log_level = getattr(logging, config.args.log_level)
        logger = setup_logging(log_level=log_level, log_file=config.args.log_file)
        
        logger.info("Initializing GitCast application...")
        orchestrator = GitCastOrchestrator(config)
        return orchestrator.run()
    except FileNotFoundError as fnf_error:
        logging.error(f"A required file or directory was not found: {fnf_error}")
        return 1
    except ValueError as val_error: # For config validation errors
        logging.error(f"Configuration validation failed: {val_error}")
        return 1
    except RuntimeError as rt_error: # For service initialization errors etc.
        logging.error(f"A runtime issue occurred: {rt_error}")
        return 1
    except Exception as e:
        logging.critical(f"An unexpected critical error occurred: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit_code = run_gitcast()
    sys.exit(exit_code)
