#!/bin/bash

# Get the script's absolute directory
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)

# Source environment variables
if [ -f "$SCRIPT_DIR/env.sh" ]; then
  source "$SCRIPT_DIR/env.sh"
elif [ -f "env.sh" ]; then
  source "env.sh"
else
  echo "Warning: env.sh not found. Skipping."
fi

# Activate Python virtual environment
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
  source "$SCRIPT_DIR/.venv/bin/activate"
elif [ -f ".venv/bin/activate" ]; then
  source ".venv/bin/activate"
else
  echo "Warning: .venv/bin/activate not found. Python environment may not be set up correctly."
fi

# Define the repositories directory, relative to the script's location
REPOS_DIR="$SCRIPT_DIR/repos"

# Check if the repositories directory exists
if [ -d "$REPOS_DIR" ]; then
  echo "Updating repositories in $REPOS_DIR..."
  for dir_item in "$REPOS_DIR"/*; do # Iterate over all items
    if [ -d "$dir_item" ]; then # Check if it's a directory
      cd "$dir_item" || { echo "Failed to cd into $dir_item. Skipping."; continue; } # Enter the directory

      if [ -d ".git" ]; then
        echo "Pulling updates for $(basename "$dir_item")..."
        git pull # Git pull itself will output status
      else
        echo "Skipping $(basename "$dir_item") (not a git repository)."
      fi
    fi
  done
  echo "Repository updates complete."
else
  echo "Warning: Directory $REPOS_DIR not found. Skipping repository updates."
fi

# Return to the script's original directory before running main.py
cd "$SCRIPT_DIR" || { echo "Critical error: Failed to cd back to $SCRIPT_DIR. Exiting."; exit 1; }

# Run the main Python script
MAIN_SCRIPT="./main.py" # This is now relative to SCRIPT_DIR
if [ -f "$MAIN_SCRIPT" ]; then
  echo "Running $MAIN_SCRIPT..."
  python "$MAIN_SCRIPT"
else
  echo "Error: $SCRIPT_DIR/$MAIN_SCRIPT not found."
  exit 1
fi

echo "Script execution finished."

