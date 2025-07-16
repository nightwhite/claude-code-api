"""
Gitignore-style pattern matching for file monitoring exclusions.
"""

import os
import re
import fnmatch
from typing import List, Pattern
from pathlib import Path
import structlog

logger = structlog.get_logger()


class GitignoreMatcher:
    """
    Gitignore-style pattern matcher for file exclusions.
    
    Supports:
    - Wildcards: *, ?, [abc], [a-z]
    - Directory patterns: dir/, */dir/, **/dir/
    - Negation: !pattern
    - Comments: # comment
    - Relative and absolute patterns
    """
    
    def __init__(self, patterns: List[str], base_path: str = ""):
        """
        Initialize the matcher with gitignore patterns.
        
        Args:
            patterns: List of gitignore-style patterns
            base_path: Base directory path for relative patterns
        """
        self.base_path = os.path.abspath(base_path) if base_path else ""
        self.patterns = []
        self.compiled_patterns = []
        
        for pattern in patterns:
            self._add_pattern(pattern)
    
    def _add_pattern(self, pattern: str):
        """Add a single pattern to the matcher."""
        # Skip empty lines and comments
        pattern = pattern.strip()
        if not pattern or pattern.startswith('#'):
            return
        
        # Handle negation
        negate = False
        if pattern.startswith('!'):
            negate = True
            pattern = pattern[1:]
        
        # Store original pattern for debugging
        self.patterns.append((pattern, negate))
        
        # Convert gitignore pattern to regex
        regex_pattern = self._gitignore_to_regex(pattern)
        compiled = re.compile(regex_pattern, re.IGNORECASE)
        self.compiled_patterns.append((compiled, negate, pattern))
    
    def _gitignore_to_regex(self, pattern: str) -> str:
        """
        Convert gitignore pattern to regex.
        
        Gitignore rules:
        - * matches any number of characters except /
        - ** matches any number of characters including /
        - ? matches any single character except /
        - [abc] matches any character in the set
        - / at the end means directory only
        - / at the start means from root
        - No / means match anywhere in path
        """
        # Escape special regex characters except our wildcards
        pattern = re.escape(pattern)
        
        # Handle directory-only patterns (ending with /)
        is_dir_only = pattern.endswith(r'\/')
        if is_dir_only:
            pattern = pattern[:-2]  # Remove escaped /
        
        # Handle root patterns (starting with /)
        is_from_root = pattern.startswith(r'\/')
        if is_from_root:
            pattern = pattern[2:]  # Remove escaped /
        
        # Convert gitignore wildcards to regex
        # ** matches any number of characters including /
        pattern = pattern.replace(r'\*\*', '.*')
        
        # * matches any number of characters except /
        pattern = pattern.replace(r'\*', '[^/]*')
        
        # ? matches any single character except /
        pattern = pattern.replace(r'\?', '[^/]')
        
        # Unescape character classes [abc]
        pattern = re.sub(r'\\(\[.*?\])', r'\1', pattern)
        
        # Build final regex
        if is_from_root:
            # Pattern must match from the start
            regex = f"^{pattern}"
        else:
            # Pattern can match anywhere in the path
            regex = f"(^|/){pattern}"

        # Add directory-only constraint
        if is_dir_only:
            # For directory patterns, also match all files within that directory
            regex += "(/.*)?$"
        else:
            regex += "(/.*)?$"
        
        return regex
    
    def should_ignore(self, file_path: str, is_directory: bool = False) -> bool:
        """
        Check if a file/directory should be ignored.
        
        Args:
            file_path: Path to check (can be absolute or relative)
            is_directory: Whether the path is a directory
            
        Returns:
            True if the file should be ignored
        """
        # Convert to relative path from base_path if needed
        if os.path.isabs(file_path) and self.base_path:
            try:
                file_path = os.path.relpath(file_path, self.base_path)
            except ValueError:
                # Paths on different drives on Windows
                pass
        
        # Normalize path separators
        file_path = file_path.replace(os.sep, '/')
        
        # Remove leading ./
        if file_path.startswith('./'):
            file_path = file_path[2:]
        
        # For directories, also check with trailing /
        paths_to_check = [file_path]
        if is_directory and not file_path.endswith('/'):
            paths_to_check.append(file_path + '/')
        
        ignored = False
        
        for path in paths_to_check:
            for compiled_pattern, negate, original_pattern in self.compiled_patterns:
                if compiled_pattern.match(path):
                    if negate:
                        ignored = False
                        logger.debug("File un-ignored by pattern", 
                                   path=path, pattern=original_pattern)
                    else:
                        ignored = True
                        logger.debug("File ignored by pattern", 
                                   path=path, pattern=original_pattern)
        
        return ignored
    
    def filter_paths(self, paths: List[str], base_path: str = None) -> List[str]:
        """
        Filter a list of paths, removing ignored ones.
        
        Args:
            paths: List of file paths to filter
            base_path: Base path for relative path resolution
            
        Returns:
            List of paths that should not be ignored
        """
        filtered = []
        
        for path in paths:
            # Determine if it's a directory
            full_path = path
            if base_path:
                full_path = os.path.join(base_path, path)
            
            is_directory = os.path.isdir(full_path) if os.path.exists(full_path) else False
            
            if not self.should_ignore(path, is_directory):
                filtered.append(path)
        
        return filtered
    
    def get_patterns(self) -> List[str]:
        """Get the list of patterns being used."""
        return [pattern for pattern, _ in self.patterns]


def create_default_matcher(base_path: str = "") -> GitignoreMatcher:
    """Create a matcher with common ignore patterns."""
    from ..core.config import settings
    
    return GitignoreMatcher(settings.file_watch_ignore_patterns, base_path)


# Convenience functions
def should_ignore_file(file_path: str, patterns: List[str] = None, 
                      base_path: str = "", is_directory: bool = False) -> bool:
    """
    Quick check if a file should be ignored.
    
    Args:
        file_path: Path to check
        patterns: Custom patterns (uses default if None)
        base_path: Base path for relative patterns
        is_directory: Whether the path is a directory
        
    Returns:
        True if the file should be ignored
    """
    if patterns is None:
        from ..core.config import settings
        patterns = settings.file_watch_ignore_patterns
    
    matcher = GitignoreMatcher(patterns, base_path)
    return matcher.should_ignore(file_path, is_directory)


def filter_file_list(file_paths: List[str], patterns: List[str] = None,
                    base_path: str = "") -> List[str]:
    """
    Filter a list of file paths using gitignore patterns.
    
    Args:
        file_paths: List of paths to filter
        patterns: Custom patterns (uses default if None)
        base_path: Base path for relative patterns
        
    Returns:
        Filtered list of paths
    """
    if patterns is None:
        from ..core.config import settings
        patterns = settings.file_watch_ignore_patterns
    
    matcher = GitignoreMatcher(patterns, base_path)
    return matcher.filter_paths(file_paths, base_path)
