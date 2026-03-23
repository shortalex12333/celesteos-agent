"""
File system watcher using watchdog.
Detects new/modified/deleted files and adds them to processing queue.
"""

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from pathlib import Path
from typing import Callable, Set, Optional
import time


class DocumentWatcher(FileSystemEventHandler):
    """
    Watches directories for document changes.

    Features:
        - Monitors PDF, DOCX, XLSX files
        - Debounces rapid changes (e.g., during file copy)
        - Ignores temporary files
        - Calls callback for each file event
    """

    # Supported file extensions
    SUPPORTED_EXTENSIONS = {'.pdf', '.docx', '.xlsx'}

    # Ignore patterns
    IGNORE_PATTERNS = {
        '~$',  # Office temp files
        '.tmp',
        '.temp',
        '.crdownload',  # Chrome downloads
        '.part',  # Partial downloads
        '.DS_Store',
        'desktop.ini',
        'thumbs.db'
    }

    def __init__(
        self,
        on_file_created: Callable[[Path], None],
        on_file_modified: Callable[[Path], None],
        on_file_deleted: Callable[[Path], None],
        debounce_seconds: float = 2.0
    ):
        """
        Initialize watcher.

        Args:
            on_file_created: Callback for new files
            on_file_modified: Callback for modified files
            on_file_deleted: Callback for deleted files
            debounce_seconds: Wait time before processing (prevents duplicate events)
        """
        super().__init__()
        self.on_file_created = on_file_created
        self.on_file_modified = on_file_modified
        self.on_file_deleted = on_file_deleted
        self.debounce_seconds = debounce_seconds

        # Track recent events for debouncing
        self._recent_events: Set[tuple] = set()
        self._last_cleanup = time.time()

    def on_created(self, event: FileSystemEvent):
        """Handle file creation event."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        if self._should_process(file_path, 'created'):
            self.on_file_created(file_path)

    def on_modified(self, event: FileSystemEvent):
        """Handle file modification event."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        if self._should_process(file_path, 'modified'):
            self.on_file_modified(file_path)

    def on_deleted(self, event: FileSystemEvent):
        """Handle file deletion event."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)

        if self._should_process(file_path, 'deleted'):
            self.on_file_deleted(file_path)

    def _should_process(self, file_path: Path, event_type: str) -> bool:
        """
        Check if file should be processed.

        Args:
            file_path: File path
            event_type: Type of event (created/modified/deleted)

        Returns:
            True if should process
        """
        # Check extension
        if file_path.suffix.lower() not in self.SUPPORTED_EXTENSIONS:
            return False

        # Check ignore patterns
        filename = file_path.name.lower()
        for pattern in self.IGNORE_PATTERNS:
            if pattern in filename:
                return False

        # Debounce: check if we recently processed this file
        event_key = (str(file_path), event_type)
        current_time = time.time()

        # Cleanup old events periodically
        if current_time - self._last_cleanup > 60:
            self._cleanup_recent_events()

        # Check if this event was recent
        if event_key in self._recent_events:
            return False

        # Record this event
        self._recent_events.add((event_key, current_time))

        return True

    def _cleanup_recent_events(self):
        """Remove old events from debounce tracking."""
        current_time = time.time()
        cutoff = current_time - (self.debounce_seconds * 2)

        self._recent_events = {
            (key, timestamp)
            for key, timestamp in self._recent_events
            if timestamp > cutoff
        }

        self._last_cleanup = current_time


class FileWatcher:
    """
    File system watcher manager.

    Manages watchdog observer and event handling.
    """

    def __init__(
        self,
        watch_paths: list,
        on_file_created: Callable[[Path], None],
        on_file_modified: Callable[[Path], None],
        on_file_deleted: Callable[[Path], None],
        recursive: bool = True
    ):
        """
        Initialize file watcher.

        Args:
            watch_paths: List of paths to watch
            on_file_created: Callback for new files
            on_file_modified: Callback for modified files
            on_file_deleted: Callback for deleted files
            recursive: Whether to watch subdirectories
        """
        self.watch_paths = watch_paths
        self.recursive = recursive

        # Create event handler
        self.event_handler = DocumentWatcher(
            on_file_created=on_file_created,
            on_file_modified=on_file_modified,
            on_file_deleted=on_file_deleted
        )

        # Create observer
        self.observer = Observer()
        self._watching = False

    def start(self):
        """Start watching file system."""
        if self._watching:
            return

        # Schedule watches
        for watch_path in self.watch_paths:
            path = Path(watch_path)
            if path.exists():
                self.observer.schedule(
                    self.event_handler,
                    str(path),
                    recursive=self.recursive
                )

        self.observer.start()
        self._watching = True

    def stop(self):
        """Stop watching file system."""
        if not self._watching:
            return

        self.observer.stop()
        self.observer.join(timeout=5)
        self._watching = False

    def is_watching(self) -> bool:
        """Check if currently watching."""
        return self._watching


def create_watcher(
    watch_paths: list,
    on_file_created: Callable[[Path], None],
    on_file_modified: Callable[[Path], None],
    on_file_deleted: Callable[[Path], None]
) -> FileWatcher:
    """
    Convenience function to create file watcher.

    Args:
        watch_paths: Paths to watch
        on_file_created: Callback for new files
        on_file_modified: Callback for modified files
        on_file_deleted: Callback for deleted files

    Returns:
        FileWatcher instance
    """
    return FileWatcher(
        watch_paths=watch_paths,
        on_file_created=on_file_created,
        on_file_modified=on_file_modified,
        on_file_deleted=on_file_deleted
    )
