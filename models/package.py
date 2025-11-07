"""Package data models."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any


@dataclass
class PackageInfo:
    """Information about a package from Repology and/or RDB."""

    name: str
    repo: str
    version: str
    status: str  # outdated, newest, devel, unique, legacy, etc.
    newest_version: Optional[str] = None

    # Additional metadata
    summary: Optional[str] = None
    categories: Optional[List[str]] = None
    licenses: Optional[List[str]] = None

    # Links
    srcurl: Optional[str] = None

    # RDB specific fields
    rdb_pkg_name: Optional[str] = None  # Package name in RDB (may differ from repology name)
    rdb_new_version: Optional[str] = None  # New version from RDB
    rdb_url: Optional[str] = None  # Update URL from RDB
    rdb_date: Optional[str] = None  # Last update date from RDB
    has_rdb_data: bool = False  # Whether this package has RDB data

    @property
    def is_outdated(self) -> bool:
        """Check if package is outdated."""
        return self.status == 'outdated'

    @property
    def repology_url(self) -> str:
        """Get Repology project URL."""
        return f"https://repology.org/project/{self.name}/versions"

    @property
    def best_newest_version(self) -> Optional[str]:
        """Get the best available newest version (prefer RDB over Repology)."""
        return self.rdb_new_version or self.newest_version

    def __str__(self) -> str:
        """String representation."""
        source = "RDB" if self.has_rdb_data else "Repology"
        newest = self.best_newest_version
        if self.is_outdated and newest:
            return f"{self.name}: {self.version} → {newest} ({self.status}) [{source}]"
        return f"{self.name}: {self.version} ({self.status}) [{source}]"


@dataclass
class CachedPackage:
    """Cached package information from database."""
    
    id: int
    email: str
    package_name: str
    repo: str
    current_version: Optional[str]
    latest_version: Optional[str]
    status: str
    data_json: Optional[str]
    cached_at: datetime
    
    def to_package_info(self) -> PackageInfo:
        """Convert to PackageInfo object."""
        return PackageInfo(
            name=self.package_name,
            repo=self.repo,
            version=self.current_version or "unknown",
            status=self.status,
            newest_version=self.latest_version
        )


@dataclass
class PackageStats:
    """Statistics about packages for an email."""
    
    email: str
    total: int
    outdated: int
    newest: int
    other: int
    last_check: Optional[datetime] = None
    
    @property
    def outdated_percentage(self) -> float:
        """Calculate percentage of outdated packages."""
        if self.total == 0:
            return 0.0
        return (self.outdated / self.total) * 100
