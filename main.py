#!/usr/bin/env python
# main.py (in gitcast_project root)

import sys
import os

# Add the project root to sys.path to allow imports from gitcast_library
# This is useful if you run `python main.py` from the gitcast_project directory.
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from gitcast_library.config import AppConfig
from gitcast_library.orchestrator import GitCastOrchestrator

def run_gitcast():
    try:
        print("Initializing GitCast application...")
        config = AppConfig()
        orchestrator = GitCastOrchestrator(config)
        return orchestrator.run()
    except FileNotFoundError as fnf_error:
        print(f"ERROR: A required file or directory was not found: {fnf_error}")
        return 1
    except ValueError as val_error: # For config validation errors
        print(f"ERROR: Configuration validation failed: {val_error}")
        return 1
    except RuntimeError as rt_error: # For service initialization errors etc.
        print(f"ERROR: A runtime issue occurred: {rt_error}")
        return 1
    except Exception as e:
        print(f"An unexpected critical error occurred: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit_code = run_gitcast()
    sys.exit(exit_code)
