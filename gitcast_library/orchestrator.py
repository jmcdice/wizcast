# gitcast_library/orchestrator.py
import os
from typing import List, Optional

try:
    from .config import AppConfig
    from .utils import load_file_content
    from .datasources import DataSource, GitRepoSource, ReleaseNotesSource, BlogDataSource, CommunityThreadSource # Added CommunityThreadSource
    from .services import LanguageModelService, TextToSpeechService
except ImportError:
    from config import AppConfig
    from utils import load_file_content
    from datasources import DataSource, GitRepoSource, ReleaseNotesSource, BlogDataSource, CommunityThreadSource
    from services import LanguageModelService, TextToSpeechService


class GitCastOrchestrator:
    def __init__(self, config: AppConfig):
        self.config = config
        self.data_sources: List[DataSource] = []
        self.llm_service: Optional[LanguageModelService] = None
        self.tts_service: Optional[TextToSpeechService] = None
        
        self._initialize_services()
        self._initialize_data_sources()

    def _initialize_services(self):
        # ... (No changes from previous full version) ...
        if not self.config.skip_llm:
            try:
                self.llm_service = LanguageModelService(self.config)
                print("Language Model Service initialized.")
            except ValueError as e:
                print(f"Error initializing Language Model Service: {e}")
                self.llm_service = None
        
        if not self.config.skip_tts:
            try:
                self.tts_service = TextToSpeechService(self.config)
                print("Text-to-Speech Service initialized.")
            except RuntimeError as e:
                print(f"Error initializing Text-to-Speech Service: {e}")
                self.tts_service = None


    def _initialize_data_sources(self):
        print("Initializing data sources...")
        # Release Notes Source
        docs_repo_full_path = os.path.join(self.config.repos_dir, self.config.docs_repo_name)
        if os.path.isdir(docs_repo_full_path):
            self.data_sources.append(ReleaseNotesSource(docs_repo_path=docs_repo_full_path, config=self.config))
            print(f"  Added ReleaseNotesSource for '{self.config.docs_repo_name}'.")
        else:
            print(f"  Info: Docs repo '{docs_repo_full_path}' not found. ReleaseNotesSource skipped.")

        # Blog Data Source
        if not self.config.skip_blog:
            if self.config.blog_url:
                self.data_sources.append(BlogDataSource(config=self.config))
                print(f"  Added BlogDataSource for URL: {self.config.blog_url}.")
            else: # Should not happen if default is set, but good check
                print("  Info: No blog URL configured. BlogDataSource skipped.")
        else:
            print("  BlogDataSource skipped via --skip-blog.")

        # Community Thread Source (New)
        if not self.config.skip_community_thread:
            if os.path.exists(self.config.community_thread_input_filepath): # Check if the specific file exists
                self.data_sources.append(CommunityThreadSource(config=self.config))
                print(f"  Added CommunityThreadSource for file: {self.config.community_thread_input_filepath}.")
            else:
                # Warning already printed in AppConfig._validate_config if file doesn't exist
                # but we can add another note here if needed or just let it skip.
                print(f"  Info: Community thread file '{self.config.community_thread_input_filepath}' not found. CommunityThreadSource skipped.")
        else:
            print("  CommunityThreadSource skipped via --skip-community-thread.")


        # Git Repository Sources
        if os.path.isdir(self.config.repos_dir):
            print(f"  Scanning for Git repositories in '{self.config.repos_dir}'...")
            for item_name in sorted(os.listdir(self.config.repos_dir)):
                item_path = os.path.join(self.config.repos_dir, item_name)
                if os.path.isdir(item_path) and os.path.isdir(os.path.join(item_path, '.git')):
                    self.data_sources.append(GitRepoSource(repo_name=item_name, repo_path=item_path, config=self.config))
                    print(f"    Added GitRepoSource for '{item_name}'.")
        else: # Warning for repos_dir already in AppConfig._validate_config
            pass # No need to repeat warning if AppConfig already handled it
        
        print(f"Initialized {len(self.data_sources)} data source(s).")

    def _collect_content_from_sources(self) -> Optional[str]:
        # ... (No changes from previous full version - it correctly passes llm_service) ...
        print(f"\n--- Step 1: Collecting Content (for week of {self.config.target_monday.strftime('%B %d, %Y')}) ---")
        all_fetched_content_parts: List[str] = []
        
        if not self.data_sources:
            print("No data sources initialized. Nothing to collect.")
            return None

        for source in self.data_sources:
            print(f"\nFetching from data source: {source.name}...")
            content = source.fetch_content(
                reference_date=self.config.current_processing_date,
                llm_service=self.llm_service
            )
            if content and content.strip():
                all_fetched_content_parts.append(
                    f"{source.get_section_header()}\n{content.strip()}\n{source.get_section_footer()}"
                )
                print(f"  Successfully gathered content from {source.name}.")
            else:
                print(f"  No content gathered from {source.name}.")
        
        if not all_fetched_content_parts:
            print("\nNo content collected from any data source. Nothing to summarize.")
            return None
        
        final_llm_input = "\n\n".join(all_fetched_content_parts)
        try:
            os.makedirs(os.path.dirname(self.config.raw_combined_input_filepath), exist_ok=True)
            with open(self.config.raw_combined_input_filepath, "w", encoding="utf-8") as f:
                f.write(final_llm_input)
            print(f"\nCombined raw input for LLM saved to: {self.config.raw_combined_input_filepath}")
        except IOError as e:
            print(f"Warning: Could not save combined raw input: {e}")
        return final_llm_input


    def _generate_summary_script(self, llm_input_text: str) -> Optional[str]:
        # ... (No changes from previous full version) ...
        print("\n--- Step 2: Generating Podcast Script via LLM ---")
        summary_filepath = self.config.summary_text_filepath

        if self.config.skip_llm:
            print("  LLM step skipped via --skip-llm flag.")
            if os.path.exists(summary_filepath):
                print(f"  Loading existing script from: {summary_filepath}")
                return load_file_content(summary_filepath)
            print("  No existing script file found, and LLM is skipped.")
            return None

        if not self.llm_service:
            print("  Error: LLM Service not initialized (or failed to initialize), but LLM step not skipped.")
            return None

        system_prompt = load_file_content(self.config.system_prompt_filepath)
        if not system_prompt: 
            print(f"  Critical Error: Main system prompt '{self.config.system_prompt_filepath}' is missing or empty.")
            return None

        generated_script: Optional[str] = None
        if os.path.exists(summary_filepath) and not self.config.overwrite_summary:
            print(f"  Podcast script file '{summary_filepath}' already exists. Loading it.")
            generated_script = load_file_content(summary_filepath)
            if not generated_script: 
                print(f"  Warning: Existing script at '{summary_filepath}' was empty or unreadable. Will attempt to regenerate.")
        
        if not generated_script or self.config.overwrite_summary: 
            if self.config.overwrite_summary and generated_script:
                print(f"  Overwriting existing summary file: {summary_filepath}")
            
            generated_script = self.llm_service.generate_summary(
                system_prompt_text=system_prompt,
                user_prompt_text=llm_input_text
            )
            if generated_script and not generated_script.lower().startswith("error:"):
                try:
                    os.makedirs(os.path.dirname(summary_filepath), exist_ok=True)
                    with open(summary_filepath, "w", encoding="utf-8") as f:
                        f.write(generated_script)
                    print(f"  Successfully saved podcast script to: {summary_filepath}")
                except IOError as e:
                    print(f"  Error writing script to file '{summary_filepath}': {e}")
                    generated_script = None 
            else:
                print(f"  Failed to generate script from LLM. Response: {generated_script or 'N/A'}")
                generated_script = None 
        
        return generated_script

    def _generate_audio_from_script(self, podcast_script_text: str) -> List[str]:
        # ... (No changes from previous full version) ...
        print("\n--- Step 3: Generating TTS Audio ---")
        if self.config.skip_tts:
            print("  TTS step skipped via --skip-tts flag.")
            expected_full = self.config.mp3_base_filepath + "_full.mp3"
            expected_single = self.config.mp3_base_filepath + ".mp3"
            if os.path.exists(expected_full): return [expected_full]
            if os.path.exists(expected_single): return [expected_single]
            return []

        if not self.tts_service:
            print("  Error: TTS Service not initialized (or failed to initialize), but TTS step not skipped.")
            return []
        
        if not podcast_script_text or not podcast_script_text.strip():
            print("  No podcast script text available. Skipping TTS.")
            return []

        audio_files = self.tts_service.synthesize_to_mp3(podcast_script_text)
        if audio_files:
            print(f"  TTS audio generation complete. File(s) in '{self.config.output_dir}': {', '.join(map(os.path.basename, audio_files))}")
        else:
            print("  TTS generation failed or produced no audio files.")
        return audio_files

    def run(self) -> int:
        # ... (No changes from previous full version) ...
        print("--- WizCast Processing Start ---")
        
        llm_input = self._collect_content_from_sources()
        if not llm_input:
            print("Exiting: No content collected for LLM processing.")
            return 1

        script_text = self._generate_summary_script(llm_input)
        
        if script_text:
            self._generate_audio_from_script(script_text)
        elif not self.config.skip_tts :
            print("Skipping TTS because podcast script generation/loading failed.")
        
        print("\n--- WizCast Processing Complete ---")
        return 0
