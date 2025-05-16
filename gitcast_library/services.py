# gitcast_library/services.py
import os
import re
import time
import subprocess
from typing import List, Optional

import google.generativeai as genai
from google.cloud import texttospeech as google_cloud_tts # Alias to avoid confusion

# Assuming utils.py and config.py are in the same package directory
try:
    from .utils import markdown_to_plain_text, logger
    from .config import AppConfig
except ImportError:
    # Fallback for different execution context or testing
    from utils import markdown_to_plain_text, logger
    from config import AppConfig


class LanguageModelService:
    def __init__(self, config: AppConfig):
        self.config = config
        if not config.gemini_api_key:
            # This check is also in AppConfig, but good for direct service instantiation
            raise ValueError("LanguageModelService requires GEMINI_API_KEY to be set in AppConfig.")
        genai.configure(api_key=config.gemini_api_key)
        self.safety_settings = [
            {"category": c, "threshold": "BLOCK_NONE"} for c in [
                "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"
            ]
        ]

    def generate_summary(self, system_prompt_text: str, user_prompt_text: str) -> Optional[str]:
        logger.info(f"Sending request to Gemini ('{self.config.gemini_model_name}'). User prompt: ~{len(user_prompt_text)} chars. System prompt: ~{len(system_prompt_text)} chars.")
        try:
            model_instance = genai.GenerativeModel(
                self.config.gemini_model_name,
                system_instruction=system_prompt_text
            )
            # Generation config can be added here if needed, e.g., temperature, top_p
            # generation_config = genai.types.GenerationConfig(temperature=0.7)
            response = model_instance.generate_content(
                user_prompt_text, 
                safety_settings=self.safety_settings
                # generation_config=generation_config 
            )

            if response.prompt_feedback and response.prompt_feedback.block_reason:
                error_msg = f"Error: Prompt blocked by Gemini. Reason: {response.prompt_feedback.block_reason}"
                logger.error(error_msg)
                return error_msg
            
            # More robust check for response content
            if not response.candidates:
                error_msg = "Error: Gemini response contained no candidates."
                logger.error(f"{error_msg} Full response: {response}")
                return error_msg
            
            candidate = response.candidates[0]
            if not candidate.content or not candidate.content.parts:
                error_msg = "Error: Gemini response candidate has no content or parts."
                logger.error(f"{error_msg} Candidate: {candidate}")
                return error_msg

            # Assuming the first part is the text we need.
            # Some models might return multiple parts, e.g., if function calling was involved.
            if hasattr(candidate.content.parts[0], 'text'):
                return candidate.content.parts[0].text
            else:
                error_msg = "Error: Gemini response part does not contain text."
                logger.error(f"{error_msg} Part: {candidate.content.parts[0]}")
                return error_msg
                
        except Exception as e:
            error_msg = f"Error: Exception during Gemini API call - {str(e)}"
            logger.error(error_msg)
            # import traceback
            # traceback.print_exc() # For more detailed debugging if needed
            return error_msg


class TextToSpeechService:
    def __init__(self, config: AppConfig):
        self.config = config
        try:
            self.tts_client = google_cloud_tts.TextToSpeechClient()
        except Exception as e:
            # This error handling is crucial as GOOGLE_APPLICATION_CREDENTIALS might be misconfigured
            raise RuntimeError(f"Failed to initialize Google Cloud TextToSpeechClient: {e}. Ensure GOOGLE_APPLICATION_CREDENTIALS is set correctly and points to a valid JSON key file.")
        self.tts_voice_name = config.tts_voice
        self.tts_chunk_limit_bytes = config.tts_text_chunk_limit # From AppConfig

    def _chunk_text(self, text: str) -> List[str]:
        if not text: return []
        chunks: List[str] = []
        current_chunk = ""
        # Split by paragraphs first, as they are natural breakpoints.
        paragraphs = text.split('\n\n')

        for paragraph in paragraphs:
            paragraph = paragraph.strip() # Ensure no leading/trailing whitespace on paragraph itself
            if not paragraph: continue

            paragraph_bytes = paragraph.encode('utf-8')
            current_chunk_bytes = current_chunk.encode('utf-8')

            # Check if adding this paragraph (plus a potential separator) exceeds the limit
            if len(current_chunk_bytes) + (2 if current_chunk else 0) + len(paragraph_bytes) <= self.tts_chunk_limit_bytes:
                if current_chunk: current_chunk += "\n\n" + paragraph
                else: current_chunk = paragraph
            else:
                # Current chunk is full (or adding the new paragraph would make it too full)
                if current_chunk: # Finalize the current_chunk
                    chunks.append(current_chunk)
                    current_chunk = "" # Reset for the next chunk

                # Now deal with the paragraph that didn't fit.
                # If the paragraph itself is too large, it needs to be split.
                if len(paragraph_bytes) > self.tts_chunk_limit_bytes:
                    logger.debug(f"TTS Chunker: Paragraph too long ({len(paragraph_bytes)} bytes), splitting by sentences.")
                    sentences = re.split(r'(?<=[.!?])\s+', paragraph) # Split by sentences
                    temp_sentence_chunk = ""
                    for sentence in sentences:
                        sentence = sentence.strip()
                        if not sentence: continue
                        sentence_bytes = sentence.encode('utf-8')
                        temp_sentence_chunk_bytes = temp_sentence_chunk.encode('utf-8')

                        if len(temp_sentence_chunk_bytes) + (1 if temp_sentence_chunk else 0) + len(sentence_bytes) <= self.tts_chunk_limit_bytes:
                            if temp_sentence_chunk: temp_sentence_chunk += " " + sentence
                            else: temp_sentence_chunk = sentence
                        else:
                            if temp_sentence_chunk: chunks.append(temp_sentence_chunk)
                            # If a single sentence is still too long (rare for TTS limits, but possible)
                            if len(sentence_bytes) > self.tts_chunk_limit_bytes:
                                logger.debug(f"TTS Chunker: Sentence too long ({len(sentence_bytes)} bytes), hard splitting.")
                                # Hard split the oversized sentence
                                # This is a fallback; ideal would be more intelligent splitting.
                                start_idx = 0
                                while start_idx < len(sentence):
                                    # Estimate max characters based on bytes (very rough)
                                    # Assuming average 1-2 bytes per char for English text after UTF-8 encoding
                                    estimated_max_chars = self.tts_chunk_limit_bytes // 1.5 
                                    sub_sentence = sentence[start_idx : int(start_idx + estimated_max_chars)]
                                    
                                    # Refine to ensure byte limit
                                    while len(sub_sentence.encode('utf-8')) > self.tts_chunk_limit_bytes:
                                        sub_sentence = sub_sentence[:-1] # Chop off last char
                                        if not sub_sentence: break # Avoid infinite loop if first char is multi-byte > limit
                                    
                                    if sub_sentence:
                                        chunks.append(sub_sentence)
                                        start_idx += len(sub_sentence)
                                    else: # Should not happen if logic is correct
                                        logger.warning("TTS Chunker: Sub-sentence became empty during hard split. Breaking.")
                                        break 
                                temp_sentence_chunk = "" # Reset after hard split
                            else: # Current sentence becomes the new temp_sentence_chunk
                                temp_sentence_chunk = sentence
                    if temp_sentence_chunk: chunks.append(temp_sentence_chunk)
                    current_chunk = "" # Ensure current_chunk is reset after handling oversized paragraph
                else: # Paragraph is not oversized itself, so it starts the new current_chunk
                    current_chunk = paragraph
        
        if current_chunk: # Add any remaining part in current_chunk
            chunks.append(current_chunk)
        
        return [c for c in chunks if c.strip()] # Filter out any empty strings

    def _synthesize_single_chunk(self, text_chunk: str, output_filename: str) -> bool:
        try:
            input_text_proto = google_cloud_tts.SynthesisInput(text=text_chunk)
            voice_params = google_cloud_tts.VoiceSelectionParams(
                language_code="en-US", # Make this configurable if supporting other languages
                name=self.tts_voice_name 
            )
            audio_config_params = google_cloud_tts.AudioConfig(
                audio_encoding=google_cloud_tts.AudioEncoding.MP3
                # Can add speaking_rate, pitch, effects here if desired
            )
            logger.info(f"Synthesizing speech for: {os.path.basename(output_filename)} ({len(text_chunk)} chars, {len(text_chunk.encode('utf-8'))} bytes)")
            
            request = google_cloud_tts.SynthesizeSpeechRequest(
                input=input_text_proto,
                voice=voice_params,
                audio_config=audio_config_params
            )
            response = self.tts_client.synthesize_speech(request=request)
            
            with open(output_filename, "wb") as out_file:
                out_file.write(response.audio_content)
            logger.info(f"Audio content written to file: {output_filename}")
            return True
        except Exception as e:
            logger.error(f"Failed to synthesize speech for {os.path.basename(output_filename)}: {e}")
            # import traceback
            # traceback.print_exc() # For detailed error during development
            return False

    def synthesize_to_mp3(self, text_to_speak: str) -> List[str]:
        output_base = self.config.mp3_base_filepath # From AppConfig
        overwrite = self.config.overwrite_tts     # From AppConfig
        logger.info(f"Processing TTS for base: {os.path.basename(output_base)}")

        if not text_to_speak or not text_to_speak.strip():
            logger.warning("No text content provided for TTS. Skipping.")
            return []

        plain_text = markdown_to_plain_text(text_to_speak)
        if not plain_text.strip():
            logger.warning("No text after Markdown conversion. Skipping TTS.")
            return []

        text_chunks = self._chunk_text(plain_text)
        if not text_chunks:
            logger.warning("No text chunks to synthesize after chunking. Skipping TTS.")
            return []
        logger.info(f"Text divided into {len(text_chunks)} chunk(s) for TTS.")

        part_mp3_files: List[str] = []
        synthesis_successful_for_all = True

        for i, chunk in enumerate(text_chunks):
            part_filename = f"{output_base}_part{i+1}.mp3"
            part_mp3_files.append(part_filename)
            # Check if file exists and has content, and if overwrite is false
            if not overwrite and os.path.exists(part_filename) and os.path.getsize(part_filename) > 0:
                logger.info(f"MP3 part exists and is not empty: {os.path.basename(part_filename)}. Skipping synthesis.")
                continue
            if not self._synthesize_single_chunk(chunk, part_filename):
                synthesis_successful_for_all = False
            # API rate limits might require a delay. Google TTS usually allows decent QPS.
            # If hitting limits, uncomment the time.sleep below.
            # time.sleep(0.5) # e.g., 0.5 second delay between requests
        
        # Filter for parts that were actually created and have content
        valid_part_files = [f for f in part_mp3_files if os.path.exists(f) and os.path.getsize(f) > 0]

        if not valid_part_files:
            logger.error("TTS failed to produce any valid audio parts.")
            return []
        if not synthesis_successful_for_all and len(valid_part_files) < len(text_chunks):
            logger.warning("Synthesis of one or more TTS chunks may have failed.")

        if len(valid_part_files) > 1:
            combined_mp3_filepath = output_base + "_full.mp3"
            # If combined file exists and we are not overwriting, return it.
            if not overwrite and os.path.exists(combined_mp3_filepath):
                logger.info(f"Combined MP3 exists: {os.path.basename(combined_mp3_filepath)}. Skipping combination.")
                # Optionally, clean up individual parts if the combined one exists and is preferred
                # for part_f_cleanup in valid_part_files:
                #     if os.path.exists(part_f_cleanup): try: os.remove(part_f_cleanup) except OSError: pass
                return [combined_mp3_filepath]

            logger.info(f"Attempting to combine {len(valid_part_files)} MP3 parts into {os.path.basename(combined_mp3_filepath)}...")
            concat_list_filename = output_base + "_concat_list.txt"
            ffmpeg_error_msg = None
            try:
                with open(concat_list_filename, "w", encoding="utf-8") as f_list:
                    for mp3_file in valid_part_files:
                        # Ensure paths are absolute or correctly relative for ffmpeg
                        f_list.write(f"file '{os.path.abspath(mp3_file)}'\n")
                
                # --- MODIFIED FFMPEG COMMAND ---
                # Instead of -c copy, we let ffmpeg re-encode to fix headers.
                # Using a common bitrate like 128k for speech.
                # -y: overwrite output files without asking
                # -f concat: input format is a concat list
                # -safe 0: needed if paths in concat list are absolute/complex
                # -i: input file (the list)
                # -ar 44100: Set audio sample rate (optional, but good for consistency)
                # -ac 1: Set audio channels to mono (speech is usually mono)
                # -b:a 128k: Set audio bitrate to 128 kbps for MP3
                # Using libmp3lame which is a high-quality MP3 encoder.
                ffmpeg_command = [
                    'ffmpeg', '-y', 
                    '-f', 'concat', '-safe', '0', '-i', concat_list_filename,
                    '-ar', '44100', '-ac', '1', '-b:a', '128k', # Re-encode options
                    combined_mp3_filepath
                ]
                # print(f"      Executing FFmpeg command: {' '.join(ffmpeg_command)}") # For debugging
                process = subprocess.run(ffmpeg_command, capture_output=True, text=True, check=False) # check=False to inspect stderr
                
                if process.returncode != 0:
                    ffmpeg_error_msg = f"ffmpeg failed with return code {process.returncode}: {process.stderr}"
                else:
                    logger.info(f"Successfully combined MP3s: {os.path.basename(combined_mp3_filepath)}")
                    # Cleanup part files after successful combination
                    for part_f_cleanup in valid_part_files:
                        if os.path.exists(part_f_cleanup): 
                            try: os.remove(part_f_cleanup)
                            except OSError as e_del: logger.warning(f"Could not delete part file {part_f_cleanup}: {e_del}")
                    return [combined_mp3_filepath]

            except FileNotFoundError: 
                ffmpeg_error_msg = "ffmpeg command not found. Please ensure it's installed and in PATH."
            except subprocess.CalledProcessError as e: # Should be caught by check=False now
                ffmpeg_error_msg = f"ffmpeg execution error: {e.stderr}"
            except Exception as e_gen: # Catch any other unexpected error during ffmpeg process
                ffmpeg_error_msg = f"Unexpected error during ffmpeg combination: {e_gen}"
            finally:
                if os.path.exists(concat_list_filename): os.remove(concat_list_filename)
            
            if ffmpeg_error_msg:
                logger.error(f"ERROR combining MP3s: {ffmpeg_error_msg} Individual part files are kept.")
                return valid_part_files # Return parts if combination failed
        
        elif len(valid_part_files) == 1:
            # Only one part, rename it to the final name (if not already correct)
            single_part_file = valid_part_files[0]
            # Standardize final name for single part to not have "_part1"
            final_single_mp3_filepath = output_base + ".mp3" 

            if os.path.abspath(single_part_file) == os.path.abspath(final_single_mp3_filepath):
                 logger.info(f"Single audio part is already correctly named: {os.path.basename(final_single_mp3_filepath)}")
                 return [final_single_mp3_filepath]

            if os.path.exists(final_single_mp3_filepath) and not overwrite:
                logger.info(f"Target MP3 {os.path.basename(final_single_mp3_filepath)} exists. Original part {os.path.basename(single_part_file)} kept.")
                # Decide whether to return the existing final or the new part.
                # For consistency, if not overwriting, the existing final is what user expects.
                return [final_single_mp3_filepath] 
            try:
                if os.path.exists(final_single_mp3_filepath) and overwrite:
                    logger.info(f"Overwriting existing target MP3: {os.path.basename(final_single_mp3_filepath)}")
                    os.remove(final_single_mp3_filepath)
                
                os.rename(single_part_file, final_single_mp3_filepath)
                logger.info(f"Single audio part renamed to: {os.path.basename(final_single_mp3_filepath)}")
                return [final_single_mp3_filepath]
            except OSError as e_rename:
                logger.error(f"Error renaming part {os.path.basename(single_part_file)} to {os.path.basename(final_single_mp3_filepath)}: {e_rename}. Part kept with original name.")
                return [single_part_file] # Return the original part name if rename fails
        
        return [] # Should not be reached if valid_part_files had items.
