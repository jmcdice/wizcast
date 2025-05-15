# gitcast_library/datasources.py
import os
import abc
import subprocess
from datetime import datetime, timedelta, date
import calendar
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
import re

try:
    from .utils import (
        load_file_content,
        parse_date_from_release_note_filename,
        fetch_url_content_text,
        parse_blog_post_date_from_text,
        get_monday_of_week,
        urljoin
    )
    from .config import AppConfig
    from .services import LanguageModelService
except ImportError:
    from utils import (
        load_file_content,
        parse_date_from_release_note_filename,
        fetch_url_content_text,
        parse_blog_post_date_from_text,
        get_monday_of_week,
        urljoin
    )
    from config import AppConfig
    from services import LanguageModelService


class DataSource(abc.ABC):
    def __init__(self, name: str, config: AppConfig):
        self.name = name
        self.config = config 

    @abc.abstractmethod
    def fetch_content(self, 
                      reference_date: date, 
                      llm_service: Optional[LanguageModelService] = None
                     ) -> Optional[str]:
        pass

    def get_section_header(self) -> str:
        return f"--- {self.name} ---"

    def get_section_footer(self) -> str:
        return f"--- End {self.name} ---"


class GitRepoSource(DataSource):
    # ... (No changes from previous full version) ...
    def __init__(self, repo_name: str, repo_path: str, config: AppConfig):
        super().__init__(f"Repository: {repo_name} Code Updates", config)
        self.repo_name = repo_name
        self.repo_path = repo_path

    def fetch_content(self, 
                      reference_date: date, 
                      llm_service: Optional[LanguageModelService] = None 
                     ) -> Optional[str]:
        git_dir = os.path.join(self.repo_path, '.git')
        if not os.path.isdir(self.repo_path) or not os.path.isdir(git_dir):
            print(f"  Error: '{self.repo_path}' ('{self.repo_name}') is not a valid git repository.")
            return None

        days_to_scan = self.config.days_to_scan
        include_merges = self.config.include_merges
        max_length = self.config.max_git_log_length_per_repo

        since_date_display = (datetime.now().date() - timedelta(days=days_to_scan)).strftime("%Y-%m-%d")
        print(f"  Fetching git log for '{self.repo_name}' from last {days_to_scan} days (since ~{since_date_display})...")
        try:
            cmd = ['git', '-C', self.repo_path, 'log', '-p', f'--since="{days_to_scan} days ago"']
            if not include_merges:
                cmd.append('--no-merges')
            cmd.append('--pretty=format:COMMIT_START%nCommit: %h%nAuthor: %an <%ae>%nDate: %ad%nSubject: %s%n%nBody:%n%b%nPatch:%nCOMMIT_END%n')
            
            result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8', errors='ignore')
            log_output = result.stdout

            if result.stderr:
                print(f"  Git log stderr for '{self.repo_name}' (non-fatal): {result.stderr.strip()}")
            if not log_output.strip():
                print(f"  No git commits found in '{self.repo_name}' for the specified period.")
                return None

            if len(log_output) > max_length:
                print(f"  Warning: Git log for '{self.repo_name}' ({len(log_output)} chars) truncated to ~{max_length} chars.")
                last_commit_end = log_output.rfind("COMMIT_END%n", 0, max_length)
                if last_commit_end != -1:
                    log_output = log_output[:last_commit_end + len("COMMIT_END%n")]
                else: 
                    log_output = log_output[:max_length]
                log_output += f"\n\n[GIT LOG FOR {self.repo_name} TRUNCATED]\n"
            return log_output
        except subprocess.CalledProcessError as e:
            print(f"  Error getting git log for '{self.repo_name}': {e.stderr or e}")
        except Exception as e:
            print(f"  Unexpected error getting git log for '{self.repo_name}': {e}")
        return None

class ReleaseNotesSource(DataSource):
    # ... (No changes from previous full version with chunked summarization) ...
    def __init__(self, docs_repo_path: str, config: AppConfig):
        super().__init__(f"Release Notes Section ({config.week_descriptor})", config)
        self.docs_repo_path = docs_repo_path

    def _chunk_text_by_paragraphs(self, text: str, max_chars: int) -> List[str]:
        chunks = []
        current_chunk_paragraphs: List[str] = []
        current_chunk_char_count = 0
        
        paragraphs = text.split('\n\n')
        
        for paragraph in paragraphs:
            paragraph_len = len(paragraph)
            if not paragraph.strip():
                continue

            if current_chunk_paragraphs and \
               (current_chunk_char_count + paragraph_len + 2 > max_chars):
                chunks.append("\n\n".join(current_chunk_paragraphs))
                current_chunk_paragraphs = []
                current_chunk_char_count = 0
            
            current_chunk_paragraphs.append(paragraph)
            current_chunk_char_count += paragraph_len + (2 if len(current_chunk_paragraphs) > 1 else 0)

            if paragraph_len > max_chars and len(current_chunk_paragraphs) == 1:
                 chunks.append("\n\n".join(current_chunk_paragraphs))
                 current_chunk_paragraphs = []
                 current_chunk_char_count = 0

        if current_chunk_paragraphs:
            chunks.append("\n\n".join(current_chunk_paragraphs))
            
        return [chunk for chunk in chunks if chunk.strip()]

    def fetch_content(self, 
                      reference_date: date, 
                      llm_service: Optional[LanguageModelService] = None
                     ) -> Optional[str]:
        release_notes_root = os.path.join(self.docs_repo_path, self.config.release_notes_base_path)
        if not os.path.isdir(release_notes_root):
            print(f"  Info: Release Notes directory not found at '{release_notes_root}'. Skipping.")
            return None

        target_monday = get_monday_of_week(reference_date)
        week_desc_for_search = f"Week of {target_monday.strftime('%B %d, %Y')}"
        print(f"  Searching for release notes for {week_desc_for_search} in '{release_notes_root}'...")
        
        all_relevant_notes_content_list: List[str] = []
        found_files_count = 0
        for subdir_name in os.listdir(release_notes_root):
            subdir_path = os.path.join(release_notes_root, subdir_name)
            if os.path.isdir(subdir_path) and subdir_name != "templates":
                for filename in os.listdir(subdir_path):
                    if filename.endswith((".mdx", ".md")):
                        file_monday = parse_date_from_release_note_filename(filename, reference_date.year)
                        if file_monday and file_monday == target_monday:
                            print(f"    Found matching RN file: {os.path.join(subdir_name, filename)}")
                            content = load_file_content(os.path.join(subdir_path, filename))
                            if content:
                                all_relevant_notes_content_list.append(f"\n--- Notes from: {subdir_name}/{filename} ---\n{content.strip()}")
                                found_files_count +=1
        
        if not all_relevant_notes_content_list:
            print(f"  No RN files found for {week_desc_for_search}.")
            return None
        
        print(f"  Found {found_files_count} RN file(s) for {week_desc_for_search}.")
        combined_content_str = "\n\n".join(all_relevant_notes_content_list)
        
        if len(combined_content_str) <= self.config.max_release_notes_length:
             print(f"  Combined RN content ({len(combined_content_str)} chars) is within the direct processing limit.")
             return combined_content_str

        print(f"  Combined RN content ({len(combined_content_str)} chars) is large. Applying chunked summarization strategy.")
        if not llm_service:
            print("    Warning: LLM service not available for ReleaseNotesSource chunked summarization. Truncating instead.")
            return combined_content_str[:self.config.max_release_notes_length] + "\n\n[RELEASE NOTES CONTENT TRUNCATED - NO LLM FOR CHUNKING]"

        chunk_summary_prompt = load_file_content(self.config.rn_chunk_summary_prompt_filepath)
        if not chunk_summary_prompt:
            print(f"    Error: Could not load RN chunk summary prompt from '{self.config.rn_chunk_summary_prompt_filepath}'. Truncating.")
            return combined_content_str[:self.config.max_release_notes_length] + "\n\n[RN CONTENT TRUNCATED - CHUNK PROMPT MISSING]"

        text_chunks = self._chunk_text_by_paragraphs(combined_content_str, self.config.rn_summarization_chunk_char_limit)
        if not text_chunks:
             print("    Warning: RN content could not be split into chunks. Returning original (potentially truncated).")
             return combined_content_str[:self.config.max_release_notes_length] + "\n\n[RN CONTENT TRUNCATED - CHUNKING FAILED]"
        print(f"    RN content split into {len(text_chunks)} chunks for initial summarization.")

        individual_summaries: List[str] = []
        for i, chunk in enumerate(text_chunks):
            print(f"      Summarizing RN chunk {i+1}/{len(text_chunks)} (length: {len(chunk)} chars)...")
            summary_of_chunk = llm_service.generate_summary(
                system_prompt_text=chunk_summary_prompt,
                user_prompt_text=chunk
            )
            if summary_of_chunk and not summary_of_chunk.lower().startswith("error:"):
                individual_summaries.append(summary_of_chunk)
                print(f"        Chunk {i+1} summarized successfully.")
            else:
                print(f"        Warning: Failed to summarize RN chunk {i+1}. Error: {summary_of_chunk}")
                individual_summaries.append(f"[Error summarizing chunk {i+1}. Content snippet: {chunk[:150]}...]")
        
        if not individual_summaries:
            print("    Error: No summaries were generated from RN chunks. Cannot create final section.")
            return "[Error: Failed to process release notes through chunked summarization.]"

        combined_summaries_text = "\n\n---\n\n".join(individual_summaries)
        print(f"    All RN chunks summarized. Total length of combined intermediate summaries: {len(combined_summaries_text)} chars.")

        final_rn_section_prompt = load_file_content(self.config.rn_combine_summaries_prompt_filepath)
        if not final_rn_section_prompt:
            print(f"    Error: Could not load RN combine summaries prompt. Returning combined chunk summaries as is.")
            return combined_summaries_text

        print("    Generating final coherent Release Notes section from combined summaries...")
        final_release_notes_section = llm_service.generate_summary(
            system_prompt_text=final_rn_section_prompt,
            user_prompt_text=combined_summaries_text
        )

        if final_release_notes_section and not final_release_notes_section.lower().startswith("error:"):
            print("    Final Release Notes section generated successfully.")
            return final_release_notes_section
        else:
            print(f"    Error generating final Release Notes section. Error: {final_release_notes_section}. Returning combined chunk summaries as fallback.")
            return combined_summaries_text

    def get_section_header(self) -> str: 
        return f"--- Release Notes Section ({self.config.week_descriptor}) ---"
    
    def get_section_footer(self) -> str:
        return f"--- End Release Notes Section ({self.config.week_descriptor}) ---"


class BlogDataSource(DataSource):
    # ... (No changes from previous full version) ...
    def __init__(self, config: AppConfig):
        super().__init__("Recent Blog Posts", config)
        self.blog_url = config.blog_url

    def _fetch_single_post_content(self, post_url: str, post_title: str) -> Optional[Dict[str, str]]:
        print(f"      Fetching content for blog post: '{post_title}' from {post_url}")
        post_page_html = fetch_url_content_text(post_url)
        if not post_page_html: return None

        post_soup = BeautifulSoup(post_page_html, "html.parser")
        content_element = post_soup.find("div", class_=re.compile(r"content|entry|post-body|article-body", re.I)) or \
                          post_soup.find("article") or \
                          post_soup.find("main")

        if content_element:
            for SCRIPT in content_element.find_all('script'): SCRIPT.decompose()
            for STYLE in content_element.find_all('style'): STYLE.decompose()
            for NAV in content_element.find_all('nav'): NAV.decompose()
            for HEADER in content_element.find_all('header'): HEADER.decompose()
            for FOOTER in content_element.find_all('footer'): FOOTER.decompose()
            for ASIDE in content_element.find_all('aside'): ASIDE.decompose()
            for FORM in content_element.find_all('form'): FORM.decompose()
            for TAG_WITH_CLASS in content_element.find_all(class_=re.compile(r"comment|share|related|sidebar|author-bio|social|meta(-data)?", re.I)):
                TAG_WITH_CLASS.decompose()

            post_text = content_element.get_text(separator="\n", strip=True)
            post_text = re.sub(r'\n\s*\n', '\n\n', post_text)
            max_len = self.config.max_blog_post_content_length

            if len(post_text) > max_len:
                print(f"        Warning: Blog post '{post_title}' content long ({len(post_text)} chars), truncating.")
                post_text = post_text[:max_len] + "\n\n[BLOG POST CONTENT TRUNCATED]"
            return {"title": post_title, "url": post_url, "content": post_text}
        else:
            print(f"        Could not extract main content from {post_url}")
            return None

    def fetch_content(self, 
                      reference_date: date, 
                      llm_service: Optional[LanguageModelService] = None
                     ) -> Optional[str]:
        target_monday = get_monday_of_week(reference_date)
        target_sunday = target_monday + timedelta(days=6)
        week_of_str = target_monday.strftime('%B %d, %Y')
        print(f"  Fetching recent blog posts from {self.blog_url} for the week of {week_of_str}...")
        
        main_page_html = fetch_url_content_text(self.blog_url)
        if not main_page_html: return None

        soup = BeautifulSoup(main_page_html, "html.parser")
        collected_posts: List[Dict[str, str]] = []
        
        article_elements = soup.find_all("article") 
        if not article_elements:
             article_elements = soup.find_all("div", class_=re.compile(r"post|entry|item|card|preview|blog-post", re.I))
        print(f"    Found {len(article_elements)} potential article elements on the main blog page.")

        for article_el in article_elements:
            if len(collected_posts) >= self.config.max_blog_posts_to_fetch:
                print(f"    Reached limit of {self.config.max_blog_posts_to_fetch} blog posts for the week.")
                break

            title_text = "Untitled Post"
            post_url: Optional[str] = None
            
            title_tag = article_el.find(['h1','h2','h3','h4','a'], class_=re.compile(r"title|heading|headline|link", re.I))
            if not title_tag: title_tag = article_el.find(['h1','h2','h3','h4','a']) 

            if title_tag:
                title_text = title_tag.get_text(strip=True)
                if title_tag.name == 'a' and title_tag.get('href'):
                    post_url = title_tag.get('href')
                else: 
                    link_in_title = title_tag.find('a', href=True)
                    if link_in_title: post_url = link_in_title.get('href')
            
            if not post_url: 
                anchor = article_el.find("a", href=True, class_=re.compile(r"read-more|full-post|link", re.I))
                if not anchor: anchor = article_el.find("a", href=True) 
                if anchor: post_url = anchor.get("href")
            
            if not post_url: continue 
            post_url = urljoin(self.blog_url, post_url) 

            date_container_text: Optional[str] = None
            time_tag = article_el.find("time")
            if time_tag: date_container_text = time_tag.get("datetime") or time_tag.get_text(strip=True)
            
            if not date_container_text:
                date_elements = article_el.find_all(['p', 'div', 'span'], 
                                                   class_=re.compile(r"meta|byline|date|published|info|timestamp", re.I), 
                                                   limit=3) 
                if not date_elements: date_elements = article_el.find_all(['p', 'div', 'span'], limit=5)
                for el_date in date_elements:
                    text_content = el_date.get_text(separator=" ", strip=True)
                    if 5 < len(text_content) < 150: 
                        if any(month.lower() in text_content.lower() for month in calendar.month_name[1:] + list(calendar.month_abbr)[1:]):
                            date_container_text = text_content
                            break
            
            if not date_container_text: continue 

            post_date = parse_blog_post_date_from_text(date_container_text)
            if post_date and (target_monday <= post_date <= target_sunday):
                print(f"    Found relevant blog post: '{title_text}' (Published: {post_date}, URL: {post_url})")
                post_details = self._fetch_single_post_content(post_url, title_text)
                if post_details:
                    collected_posts.append(post_details)
        
        if not collected_posts:
            print(f"  No relevant blog posts found for the week of {week_of_str}.")
            return None

        output_parts = [
            f"--- Blog Post: {post['title']} ({post['url']}) ---\n{post['content'].strip()}\n--- End Blog Post ---"
            for post in collected_posts
        ]
        print(f"  Successfully gathered {len(collected_posts)} recent blog post(s).")
        return "\n\n".join(output_parts) if output_parts else None


class CommunityThreadSource(DataSource):
    def __init__(self, config: AppConfig):
        super().__init__("Community Discussion Highlight", config)
        self.thread_filepath = config.community_thread_input_filepath # Path from AppConfig
        self.summary_prompt_filepath = config.community_thread_summary_prompt_filepath

    def _preprocess_thread_text(self, raw_text: str) -> str:
        # Basic preprocessing:
        # - Remove lines that are just timestamps like "[5:02 PM]" or "8 replies"
        # - Normalize excessive newlines
        # - Could add more sophisticated cleaning (e.g., placeholder for links)
        
        lines = raw_text.splitlines()
        processed_lines = []
        for line in lines:
            # Regex to match common timestamp/metadata patterns on their own line
            if re.fullmatch(r"\[\s*\d{1,2}:\d{2}(?:\s*(?:AM|PM))?\s*\]", line.strip()): # Matches [5:02 PM]
                continue
            if re.fullmatch(r"\d+\s+repl(y|ies)", line.strip().lower()): # Matches "8 replies"
                continue
            if line.strip().lower() == "(edited)":
                continue
            # Simple link placeholder - can be improved
            line = re.sub(r"https?://\S+", "[link]", line)
            processed_lines.append(line)
        
        text = "\n".join(processed_lines)
        text = re.sub(r'\n\s*\n', '\n\n', text) # Normalize multiple newlines
        return text.strip()

    def fetch_content(self, 
                      reference_date: date, # Not used by this source, but part of interface
                      llm_service: Optional[LanguageModelService] = None
                     ) -> Optional[str]:
        
        if not os.path.exists(self.thread_filepath):
            print(f"  Info: Community thread file not found at '{self.thread_filepath}'. Skipping this source.")
            return None

        print(f"  Processing manual community thread from: {self.thread_filepath}")
        raw_thread_content = load_file_content(self.thread_filepath)

        if not raw_thread_content or not raw_thread_content.strip():
            print(f"  Warning: Community thread file '{self.thread_filepath}' is empty. Skipping.")
            return None
        
        # Optional: Truncate raw thread if it's excessively long before even preprocessing/LLM
        if len(raw_thread_content) > self.config.max_community_thread_raw_length:
            print(f"  Warning: Raw community thread content ({len(raw_thread_content)} chars) exceeds limit, truncating.")
            raw_thread_content = raw_thread_content[:self.config.max_community_thread_raw_length] + \
                                 "\n\n[RAW THREAD CONTENT TRUNCATED BEFORE SUMMARIZATION]"


        print("    Preprocessing community thread text...")
        processed_thread_text = self._preprocess_thread_text(raw_thread_content)

        if not processed_thread_text:
            print("    Warning: Community thread text is empty after preprocessing. Skipping.")
            return None

        if not llm_service:
            print("    Warning: LLM service not available to CommunityThreadSource. Cannot summarize. Skipping.")
            return None # Or return preprocessed text if that's ever useful as a fallback

        system_prompt = load_file_content(self.summary_prompt_filepath)
        if not system_prompt:
            print(f"    Error: Could not load community thread summary prompt from '{self.summary_prompt_filepath}'. Skipping.")
            return None

        print("    Summarizing community thread using LLM...")
        summary = llm_service.generate_summary(
            system_prompt_text=system_prompt,
            user_prompt_text=processed_thread_text # Send the preprocessed thread text
        )

        if summary and not summary.lower().startswith("error:"):
            print("    Community thread summarized successfully.")
            # Optional: Add a small intro/outro here if needed for the podcast context
            # e.g., "From our community discussions this week:\n" + summary
            return summary
        else:
            print(f"    Warning: Failed to summarize community thread. Error: {summary}")
            return f"[Error summarizing community thread from file: {os.path.basename(self.thread_filepath)}]"
