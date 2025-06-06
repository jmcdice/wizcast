# gitcast_library/orchestrator.py
import os
import logging
from typing import List, Optional

# Attempt project-relative imports first
try:
    from .config import AppConfig
    from .utils import load_file_content # Removed logger import, use logging.getLogger()
    from .datasources import DataSource, GitRepoSource, ReleaseNotesSource, BlogDataSource, CommunityThreadSource
    from .services import LanguageModelService, TextToSpeechService
except ImportError:
    # Fallback for scenarios where the package structure isn't recognized (e.g. direct script execution)
    from config import AppConfig
    from utils import load_file_content
    from datasources import DataSource, GitRepoSource, ReleaseNotesSource, BlogDataSource, CommunityThreadSource
    from services import LanguageModelService, TextToSpeechService

# It's generally better to get the logger instance this way
logger = logging.getLogger(__name__) # Or your specific application logger name e.g., 'wizcast.orchestrator'

class GitCastOrchestrator:
    def __init__(self, config: AppConfig):
        self.config = config
        self.data_sources: List[DataSource] = []
        self.llm_service: Optional[LanguageModelService] = None
        self.tts_service: Optional[TextToSpeechService] = None
        
        self._initialize_services()
        self._initialize_data_sources()

    def _initialize_services(self):
        if not self.config.skip_llm:
            try:
                self.llm_service = LanguageModelService(self.config)
                logger.info("Language Model Service initialized.")
            except ValueError as e:
                logger.error(f"Error initializing Language Model Service: {e}")
                self.llm_service = None # Ensure it's None on failure
        
        if not self.config.skip_tts:
            try:
                self.tts_service = TextToSpeechService(self.config)
                logger.info("Text-to-Speech Service initialized.")
            except RuntimeError as e: # Catch specific errors TextToSpeechService might raise
                logger.error(f"Error initializing Text-to-Speech Service: {e}")
                self.tts_service = None # Ensure it's None on failure

    def _initialize_data_sources(self):
        logger.info("Initializing data sources...")
        # Release Notes Source
        docs_repo_full_path = os.path.join(self.config.repos_dir, self.config.docs_repo_name)
        if os.path.isdir(docs_repo_full_path):
            self.data_sources.append(ReleaseNotesSource(docs_repo_path=docs_repo_full_path, config=self.config))
            logger.info(f"Added ReleaseNotesSource for '{self.config.docs_repo_name}'.")
        else:
            logger.info(f"Docs repo '{docs_repo_full_path}' not found. ReleaseNotesSource skipped.")

        # Blog Data Source
        if not self.config.skip_blog:
            if self.config.blog_url:
                self.data_sources.append(BlogDataSource(config=self.config))
                logger.info(f"Added BlogDataSource for URL: {self.config.blog_url}.")
            else:
                logger.info("No blog URL configured. BlogDataSource skipped.")
        else:
            logger.info("BlogDataSource skipped via --skip-blog.")

        # Community Thread Source
        if not self.config.skip_community_thread:
            # self.config.community_thread_input_filepath should be an absolute path or resolvable
            if os.path.exists(self.config.community_thread_input_filepath):
                self.data_sources.append(CommunityThreadSource(config=self.config))
                logger.info(f"Added CommunityThreadSource for file: {self.config.community_thread_input_filepath}.")
            else:
                logger.info(f"Community thread file '{self.config.community_thread_input_filepath}' not found. CommunityThreadSource skipped.")
        else:
            logger.info("CommunityThreadSource skipped via --skip-community-thread.")

        # Git Repository Sources
        if os.path.isdir(self.config.repos_dir): # Ensure repos_dir itself exists
            logger.info(f"Scanning for Git repositories in '{self.config.repos_dir}'...")
            for item_name in sorted(os.listdir(self.config.repos_dir)):
                item_path = os.path.join(self.config.repos_dir, item_name)
                if os.path.isdir(item_path) and os.path.isdir(os.path.join(item_path, '.git')):
                    self.data_sources.append(GitRepoSource(repo_name=item_name, repo_path=item_path, config=self.config))
                    logger.info(f"Added GitRepoSource for '{item_name}'.")
        else:
            logger.warning(f"Repositories directory '{self.config.repos_dir}' not found. Skipping GitRepoSource initialization.")
        
        logger.info(f"Initialized {len(self.data_sources)} data source(s).")

    def _collect_content_from_sources(self) -> Optional[str]:
        logger.info(f"--- Step 1: Collecting Content (for week of {self.config.target_monday.strftime('%B %d, %Y')}) ---")
        all_fetched_content_parts: List[str] = []
        
        if not self.data_sources:
            logger.warning("No data sources initialized. Nothing to collect.")
            return None

        for source in self.data_sources:
            logger.info(f"Fetching from data source: {source.name}...")
            # Pass llm_service if the source might need it (e.g., for summarization within the source)
            content = source.fetch_content(
                reference_date=self.config.current_processing_date,
                llm_service=self.llm_service 
            )
            if content and content.strip():
                all_fetched_content_parts.append(
                    f"{source.get_section_header()}\n{content.strip()}\n{source.get_section_footer()}"
                )
                logger.info(f"Successfully gathered content from {source.name}.")
            else:
                logger.info(f"No content gathered from {source.name}.")
        
        if not all_fetched_content_parts:
            logger.warning("No content collected from any data source. Nothing to summarize.")
            return None
        
        final_llm_input = "\n\n".join(all_fetched_content_parts)
        try:
            # Ensure the directory for the raw input file exists
            os.makedirs(os.path.dirname(self.config.raw_combined_input_filepath), exist_ok=True)
            with open(self.config.raw_combined_input_filepath, "w", encoding="utf-8") as f:
                f.write(final_llm_input)
            logger.info(f"Combined raw input for LLM saved to: {self.config.raw_combined_input_filepath}")
        except IOError as e:
            logger.warning(f"Could not save combined raw input to '{self.config.raw_combined_input_filepath}': {e}")
        return final_llm_input

    def _generate_summary_script(self, llm_input_text: str) -> Optional[str]:
        logger.info("--- Step 2: Generating Podcast Script via LLM ---")
        summary_filepath = self.config.summary_text_filepath # This should be an absolute path or resolvable

        if self.config.skip_llm:
            logger.info("LLM step skipped via --skip-llm flag.")
            if os.path.exists(summary_filepath):
                logger.info(f"Loading existing script from: {summary_filepath}")
                return load_file_content(summary_filepath)
            logger.warning(f"LLM skipped and no existing script file found at: {summary_filepath}")
            return None

        if not self.llm_service:
            logger.error("LLM Service not initialized (or failed to initialize), but LLM step not skipped. Cannot generate script.")
            return None

        system_prompt = load_file_content(self.config.system_prompt_filepath)
        if not system_prompt: 
            logger.critical(f"Main system prompt '{self.config.system_prompt_filepath}' is missing or empty. Cannot generate script.")
            return None

        generated_script: Optional[str] = None
        if os.path.exists(summary_filepath) and not self.config.overwrite_summary:
            logger.info(f"Podcast script file '{summary_filepath}' already exists. Loading it.")
            generated_script = load_file_content(summary_filepath)
            if not generated_script: 
                logger.warning(f"Existing script at '{summary_filepath}' was empty or unreadable. Will attempt to regenerate.")
        
        if not generated_script or self.config.overwrite_summary: 
            if self.config.overwrite_summary and generated_script: # Log if overwriting
                logger.info(f"Overwriting existing summary file: {summary_filepath}")
            
            generated_script = self.llm_service.generate_summary(
                system_prompt_text=system_prompt,
                user_prompt_text=llm_input_text
            )
            if generated_script and not generated_script.lower().startswith("error:"): # Check for explicit error markers
                try:
                    os.makedirs(os.path.dirname(summary_filepath), exist_ok=True)
                    with open(summary_filepath, "w", encoding="utf-8") as f:
                        f.write(generated_script)
                    logger.info(f"Successfully saved podcast script to: {summary_filepath}")
                except IOError as e:
                    logger.error(f"Error writing script to file '{summary_filepath}': {e}")
                    generated_script = None # Indicate failure
            else:
                logger.error(f"Failed to generate script from LLM. Response: {generated_script or 'N/A'}")
                generated_script = None # Indicate failure
        
        return generated_script

    def _generate_audio_from_script(self, podcast_script_text: str) -> List[str]:
        logger.info("--- Step 3: Generating TTS Audio ---")
        # Determine the expected full MP3 path for skip_tts scenario
        # self.config.mp3_base_filepath should be a base path for outputs, e.g., output_dir/timestamp_base
        expected_full_mp3_path = self.config.mp3_base_filepath + "_full.mp3"
        expected_single_mp3_path = self.config.mp3_base_filepath + ".mp3"

        if self.config.skip_tts:
            logger.info("TTS step skipped via --skip-tts flag.")
            # Check if an existing MP3 (full or single part) can be used
            if os.path.exists(expected_full_mp3_path):
                logger.info(f"Using existing full MP3: {expected_full_mp3_path}")
                return [expected_full_mp3_path]
            if os.path.exists(expected_single_mp3_path): # if no full, check for single
                logger.info(f"Using existing single part MP3: {expected_single_mp3_path}")
                return [expected_single_mp3_path]
            logger.warning(f"TTS skipped and no existing MP3 found at '{expected_full_mp3_path}' or '{expected_single_mp3_path}'.")
            return []

        if not self.tts_service:
            logger.error("TTS Service not initialized (or failed to initialize), but TTS step not skipped. Cannot generate audio.")
            return []
        
        if not podcast_script_text or not podcast_script_text.strip():
            logger.warning("No podcast script text available. Skipping TTS.")
            return []

        # Assuming synthesize_to_mp3 returns a list of *absolute* paths to the generated files
        audio_files = self.tts_service.synthesize_to_mp3(podcast_script_text)
        if audio_files:
            # Log the basenames for conciseness, but audio_files contains full paths
            logger.info(f"TTS audio generation complete. File(s) in '{self.config.output_dir}': {', '.join(map(os.path.basename, audio_files))}")
        else:
            logger.warning("TTS generation failed or produced no audio files.")
        return audio_files # Returns list of absolute paths

    def run(self) -> Optional[str]: # Changed return type
        logger.info("--- WizCast Processing Start ---")
        
        final_audio_path: Optional[str] = None # To store the path of the final MP3

        llm_input = self._collect_content_from_sources()
        if not llm_input:
            logger.warning("Exiting: No content collected for LLM processing.")
            return None # Return None on failure to collect content

        script_text = self._generate_summary_script(llm_input)
        
        if script_text:
            generated_audio_files = self._generate_audio_from_script(script_text)
            if generated_audio_files:
                # Find the main audio file (e.g., the one ending in "_full.mp3" or the only one if not chunked)
                # This assumes tts_service.synthesize_to_mp3 and config.mp3_base_filepath are consistent
                
                # Priority: Look for a file ending with "_full.mp3"
                # self.config.mp3_base_filepath is the base for naming, e.g., "output_wizcast/wizcast_digest_202506050913"
                # The full file would be "output_wizcast/wizcast_digest_202506050913_full.mp3"
                # The _generate_audio_from_script method is already expected to return this path if it exists
                # and if skip_tts is true.

                # The paths in generated_audio_files are absolute.
                # We need to find the one that corresponds to the combined/final audio.
                # A simple heuristic: if a file ends with "_full.mp3", that's likely it.
                # Otherwise, if only one file, that's it.
                # If multiple parts but no "_full.mp3" (e.g., combining failed), this logic might need refinement
                # or the TTS service should clearly indicate the main output file.

                main_mp3_abs_path = None
                for f_path in generated_audio_files:
                    if f_path.endswith("_full.mp3"):
                        main_mp3_abs_path = f_path
                        break
                if not main_mp3_abs_path and len(generated_audio_files) == 1: # If no "_full" but only one file
                    main_mp3_abs_path = generated_audio_files[0]
                elif not main_mp3_abs_path and generated_audio_files: # Fallback if no "_full" but multiple files (take last one as a guess)
                    logger.warning("Could not definitively identify a '_full.mp3' file, using the last generated audio file.")
                    main_mp3_abs_path = generated_audio_files[-1]


                if main_mp3_abs_path:
                    try:
                        # Convert to relative path from the current working directory.
                        # run.sh ensures that main.py is run from the project root.
                        project_root_cwd = os.getcwd()
                        relative_mp3_path = os.path.relpath(main_mp3_abs_path, project_root_cwd)
                        final_audio_path = relative_mp3_path
                        logger.info(f"Successfully generated audio. Relative path: {final_audio_path}")
                    except ValueError as e:
                        logger.error(f"Error creating relative path for '{main_mp3_abs_path}' from '{project_root_cwd}': {e}")
                        # Fallback: return absolute path if relative path generation fails
                        final_audio_path = main_mp3_abs_path
                        logger.warning(f"Falling back to absolute path: {final_audio_path}")
                else:
                    logger.warning("Audio files were generated, but could not identify the main MP3 output file to return.")
            # If generated_audio_files is empty, it means TTS failed or was skipped and no existing file was found.
            # final_audio_path remains None.

        elif not self.config.skip_tts : # If script_text is None AND we are not skipping TTS
            logger.warning("Skipping TTS because podcast script generation/loading failed.")
        
        logger.info("--- WizCast Processing Complete ---")
        return final_audio_path # Return the relative path string or None
