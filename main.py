#!/usr/bin/env python
# main.py (in gitcast_project root)

import sys
import os
import logging
import traceback # Import traceback at the top

# Add the project root to sys.path to allow imports from gitcast_library
# This is useful if you run `python main.py` from the gitcast_project directory.
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from gitcast_library.config import AppConfig
from gitcast_library.orchestrator import GitCastOrchestrator
from gitcast_library.utils import setup_logging

def run_gitcast():
    """
    Initializes and runs the GitCast application.
    On success, prints the relative path of the generated MP3 and exits with 0.
    On failure, logs an error and exits with 1.
    """
    logger = None # Initialize logger to None for broader scope
    try:
        # Initialize configuration
        config = AppConfig()
        
        # Setup logging with configured options
        # Ensure log_level is a valid attribute of logging
        log_level_str = config.args.log_level.upper()
        log_level = getattr(logging, log_level_str, logging.INFO) # Default to INFO if invalid
        
        # It's good practice to get the logger from setup_logging
        logger = setup_logging(log_level=log_level, log_file=config.args.log_file)
        
        logger.info("Initializing GitCast application...")
        orchestrator = GitCastOrchestrator(config)
        
        # Assume orchestrator.run() now returns the relative MP3 path on success
        # and raises an exception on failure.
        mp3_relative_path = orchestrator.run()
        
        if mp3_relative_path and isinstance(mp3_relative_path, str):
            print(mp3_relative_path) # Output the relative path to stdout
            return 0 # Success exit code
        elif mp3_relative_path is None: # If orchestrator explicitly returns None for non-error completion without output
             logger.info("Orchestrator completed without generating an MP3 file.")
             return 0 # Still a successful run, just no file.
        else:
            # This case handles if orchestrator.run() returns something unexpected
            # without raising an exception (e.g., an integer or boolean by mistake).
            logger.error(f"Orchestrator finished but returned an unexpected value: {mp3_relative_path}")
            return 1 # Indicate an issue


    except FileNotFoundError as fnf_error:
        if logger:
            logger.error(f"A required file or directory was not found: {fnf_error}")
        else:
            # Fallback logging if logger setup failed
            logging.error(f"A required file or directory was not found: {fnf_error}")
        return 1
    except ValueError as val_error: # For config validation errors
        if logger:
            logger.error(f"Configuration validation failed: {val_error}")
        else:
            logging.error(f"Configuration validation failed: {val_error}")
        return 1
    except RuntimeError as rt_error: # For service initialization errors etc.
        if logger:
            logger.error(f"A runtime issue occurred: {rt_error}")
        else:
            logging.error(f"A runtime issue occurred: {rt_error}")
        return 1
    except Exception as e:
        # Use the logger if available, otherwise fallback to standard logging
        # and ensure traceback is available
        log_message = f"An unexpected critical error occurred: {e}"
        if logger:
            logger.critical(log_message)
            logger.critical(traceback.format_exc()) # Log full traceback
        else:
            logging.critical(log_message)
            traceback.print_exc() # Print traceback to stderr
        return 1

if __name__ == "__main__":
    # Note: The logger configured in run_gitcast is local to that function's scope.
    # If you need to log before or after run_gitcast (e.g., about the exit code),
    # you might want a more global logger or pass it around.
    # However, for this script's structure, handling logging primarily within run_gitcast is fine.
    
    exit_code = run_gitcast()
    
    # Optional: log the exit if needed, though typically the console shows this.
    # For instance, if you had a global logger:
    # global_logger.info(f"GitCast application finished with exit code {exit_code}.")
    
    sys.exit(exit_code)

