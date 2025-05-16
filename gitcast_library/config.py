# gitcast_library/config.py
import os
import argparse
import logging
from dotenv import load_dotenv
from datetime import datetime, date

# Assuming utils.py is in the same directory or handled by PYTHONPATH
try:
    from .utils import get_monday_of_week, ensure_dir, logger, setup_logging
except ImportError:
    from utils import get_monday_of_week, ensure_dir, logger, setup_logging


class AppConfig:
    def __init__(self):
        self.library_dir = os.path.dirname(os.path.abspath(__file__))
        self.project_root = os.path.dirname(self.library_dir)

        load_dotenv(os.path.join(self.project_root, ".env"))
        self._parse_args()
        self._load_env_vars()
        self._set_derived_paths_and_values()
        self._validate_config()

    def _parse_args(self):
        parser = argparse.ArgumentParser(
            description="WizCast: Podcast summary from local git repos, release notes & blog posts.",
            formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )
        # Paths and Directories
        parser.add_argument('--repos-dir', type=str, default=os.path.join(self.project_root, "repos"),
                            help="Directory of local git repo subdirectories.")
        parser.add_argument('--output-dir', type=str, default=os.path.join(self.project_root, "output_wizcast"),
                            help="Directory for output files.")
        parser.add_argument('--prompt-dir', type=str, default=os.path.join(self.project_root, "prompts"),
                            help="Directory for prompt files.")
        parser.add_argument('--manual-inputs-dir', type=str, default=os.path.join(self.project_root, "manual_inputs"),
                            help="Directory for manually added input files like community threads.")
        
        # File Names & Identifiers
        parser.add_argument('--system-prompt-file', type=str, default="git_summary_system_prompt.md",
                            help="System prompt filename (relative to prompt-dir) for the main summary.")
        parser.add_argument('--output-basename', type=str,
                            help="Base name for output (default: wizcast_digest_YYYYMMDDHHMM).")
        parser.add_argument('--community-thread-filename', type=str, default="community_thread.txt",
                            help="Filename of the community thread text file in manual-inputs-dir to summarize.")
        parser.add_argument('--community-thread-summary-prompt-file', type=str,
                            default="community_thread_summary_system_prompt.md",
                            help="System prompt for summarizing community threads (relative to prompt-dir).")
        
        # Processing Parameters & Service Configuration (Existing)
        parser.add_argument('--days', type=int, default=7, help="Past days for git log, release notes & blog posts.")
        parser.add_argument('--include-merges', action='store_true', default=False, help="Include merge commits in git log.")
        parser.add_argument('--model', type=str, default="gemini-1.5-flash-latest", help="Gemini model.")
        parser.add_argument('--tts-voice', type=str, default="en-US-Chirp3-HD-Achernar", help="TTS voice.")
        
        # Logging Options
        parser.add_argument('--log-level', type=str, default="INFO", 
                            choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                            help="Logging level.")
        parser.add_argument('--log-file', type=str, default=None,
                            help="Optional log file path. If not specified, logs will only be written to stdout.")
        
        # Skip Flags (Existing)
        parser.add_argument('--skip-blog', action='store_true', default=False, help="Skip fetching and summarizing blog posts.")
        parser.add_argument('--skip-community-thread', action='store_true', default=False, help="Skip summarizing the manual community thread.")
        parser.add_argument('--overwrite-summary', action='store_true', default=False, help="Overwrite existing summary text file.")
        parser.add_argument('--overwrite-tts', action='store_true', default=False, help="Overwrite existing TTS audio files.")
        parser.add_argument('--skip-llm', action='store_true', default=False, help="Skip LLM summary generation.")
        parser.add_argument('--skip-tts', action='store_true', default=False, help="Skip TTS audio generation.")

        # Data Source Specific (Existing)
        parser.add_argument('--docs-repo-name', type=str, default="docs", help="Name of the docs repository.")
        parser.add_argument('--release-notes-base-path', type=str, default="packages/docs-web/content/release-notes", help="Base path for release notes.")
        parser.add_argument('--blog-url', type=str, default="https://www.wiz.io/blog", help="URL of the main blog page.")

        # RN Summarization Strategy (Existing)
        parser.add_argument('--rn-chunk-summary-prompt-file', type=str, default="rn_chunk_summary_system_prompt.md", help="System prompt for RN chunks.")
        parser.add_argument('--rn-combine-summaries-prompt-file', type=str, default="rn_combine_summaries_system_prompt.md", help="System prompt for combining RN summaries.")
        
        self.args = parser.parse_args()

    def _load_env_vars(self):
        self.gemini_api_key = os.getenv("GEMINI_API_KEY")
        self.google_application_credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    def _set_derived_paths_and_values(self):
        self.system_prompt_filepath = os.path.join(self.args.prompt_dir, self.args.system_prompt_file)
        
        _output_basename = self.args.output_basename or f"wizcast_digest_{datetime.now().strftime('%Y%m%d%H%M')}"
        self.summary_text_filepath = os.path.join(self.args.output_dir, _output_basename + "_summary.txt")
        self.mp3_base_filepath = os.path.join(self.args.output_dir, _output_basename)
        self.raw_combined_input_filepath = os.path.join(self.args.output_dir, _output_basename + "_raw_combined_input.txt")

        self.rn_chunk_summary_prompt_filepath = os.path.join(self.args.prompt_dir, self.args.rn_chunk_summary_prompt_file)
        self.rn_combine_summaries_prompt_filepath = os.path.join(self.args.prompt_dir, self.args.rn_combine_summaries_prompt_file)
        
        # New path for community thread summary prompt
        self.community_thread_summary_prompt_filepath = os.path.join(self.args.prompt_dir, self.args.community_thread_summary_prompt_file)
        # New path for the community thread input file
        self.community_thread_input_filepath = os.path.join(self.args.manual_inputs_dir, self.args.community_thread_filename)

        self.current_processing_date: date = datetime.now().date()
        self.target_monday: date = get_monday_of_week(self.current_processing_date)
        self.week_descriptor: str = f"Week of {self.target_monday.strftime('%B %d, %Y')}"

        # Limits
        self.tts_text_chunk_limit: int = 4800
        self.max_git_log_length_per_repo: int = 70000
        self.max_release_notes_length: int = 50000
        self.max_blog_post_content_length: int = 30000
        self.max_community_thread_raw_length: int = 40000 # Max length for the raw community thread text before summarization
        self.max_blog_posts_to_fetch: int = 5
        self.rn_summarization_chunk_char_limit: int = 25000

    def _validate_config(self):
        # ... (Existing API key and credentials validation)
        if not self.args.skip_llm and not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY not set. Required if LLM is not skipped.")
        if not self.args.skip_tts:
            if not self.google_application_credentials:
                raise ValueError("GOOGLE_APPLICATION_CREDENTIALS not set. Required if TTS is not skipped.")
            if not os.path.exists(self.google_application_credentials):
                 raise ValueError(f"GOOGLE_APPLICATION_CREDENTIALS path invalid: {self.google_application_credentials}")

        ensure_dir(self.args.output_dir)
        ensure_dir(self.args.prompt_dir)
        ensure_dir(self.args.manual_inputs_dir) # Ensure manual_inputs directory exists

        if not os.path.isdir(self.args.repos_dir):
            logger.warning(f"Repos dir '{self.args.repos_dir}' not found.")
        
        if not self.args.skip_llm:
            if not os.path.exists(self.system_prompt_filepath):
                raise FileNotFoundError(f"Main system prompt file not found: {self.system_prompt_filepath}.")
            if not os.path.exists(self.rn_chunk_summary_prompt_filepath):
                raise FileNotFoundError(f"RN chunk summary prompt file not found: {self.rn_chunk_summary_prompt_filepath}")
            if not os.path.exists(self.rn_combine_summaries_prompt_filepath):
                raise FileNotFoundError(f"RN combine summaries prompt file not found: {self.rn_combine_summaries_prompt_filepath}")
            # Validate new community thread prompt only if not skipping community thread
            if not self.args.skip_community_thread and not os.path.exists(self.community_thread_summary_prompt_filepath):
                raise FileNotFoundError(f"Community thread summary prompt file not found: {self.community_thread_summary_prompt_filepath}")
        
        # Validate community thread input file if not skipping
        if not self.args.skip_community_thread and not os.path.exists(self.community_thread_input_filepath):
            logger.warning(f"Community thread input file not found: {self.community_thread_input_filepath}. This source will be skipped.")


    # --- Convenience properties ---
    @property
    def repos_dir(self): return self.args.repos_dir
    @property
    def output_dir(self): return self.args.output_dir
    @property
    def prompt_dir(self): return self.args.prompt_dir
    @property
    def manual_inputs_dir(self): return self.args.manual_inputs_dir
    @property
    def days_to_scan(self): return self.args.days
    @property
    def include_merges(self): return self.args.include_merges
    @property
    def gemini_model_name(self): return self.args.model
    @property
    def tts_voice(self): return self.args.tts_voice
    @property
    def skip_blog(self): return self.args.skip_blog
    @property
    def skip_community_thread(self): return self.args.skip_community_thread
    @property
    def overwrite_summary(self): return self.args.overwrite_summary
    @property
    def overwrite_tts(self): return self.args.overwrite_tts
    @property
    def skip_llm(self): return self.args.skip_llm
    @property
    def skip_tts(self): return self.args.skip_tts
    @property
    def docs_repo_name(self): return self.args.docs_repo_name
    @property
    def release_notes_base_path(self): return self.args.release_notes_base_path
    @property
    def blog_url(self): return self.args.blog_url
    @property
    def community_thread_filename(self): return self.args.community_thread_filename
