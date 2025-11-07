"""Package checking service with caching."""
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict

from models.package import PackageInfo, PackageStats
from services.repology import RepologyClient
from services.rdb import RDBClient
from services.package_merger import PackageMerger
from core.database import Database

logger = logging.getLogger(__name__)


class PackageChecker:
    """Service for checking packages with caching."""

    def __init__(
        self,
        db: Database,
        repology: RepologyClient,
        rdb: Optional[RDBClient] = None,
        rdb_mapping: Optional[Dict[str, str]] = None,
        cache_hours: int = 6
    ):
        """
        Initialize package checker.

        Args:
            db: Database instance
            repology: Repology client
            rdb: RDB client (optional)
            rdb_mapping: Email to RDB nickname mapping (optional)
            cache_hours: Cache duration in hours
        """
        self.db = db
        self.repology = repology
        self.rdb = rdb
        self.rdb_mapping = rdb_mapping or {}
        self.cache_duration = timedelta(hours=cache_hours)
        self.merger = PackageMerger()
    
    async def get_packages_for_email(
        self,
        email: str,
        repo: Optional[str] = None,
        force_refresh: bool = False
    ) -> List[PackageInfo]:
        """
        Get packages for an email with caching, merging Repology and RDB data.

        Args:
            email: Maintainer email
            repo: Filter by repository
            force_refresh: Force cache refresh

        Returns:
            List of PackageInfo objects (merged from Repology and RDB)
        """
        # Check cache if not forcing refresh
        if not force_refresh:
            cached = await self._get_from_cache(email, repo)
            if cached:
                logger.info(f"Using cached data for {email} ({len(cached)} packages)")
                return cached

        # Fetch from Repology
        logger.info(f"Fetching fresh data for {email}")
        repology_packages = await self.repology.get_projects_by_maintainer(email, repo=repo)

        # Fetch from RDB if configured
        rdb_packages = []
        logger.debug(f"RDB client: {self.rdb}, Email in mapping: {email in self.rdb_mapping}, Mapping: {self.rdb_mapping}")
        if self.rdb and email in self.rdb_mapping:
            nickname = self.rdb_mapping[email]
            logger.info(f"Fetching RDB data for {email} -> {nickname}")
            try:
                rdb_packages = await self.rdb.get_packages_by_maintainer(nickname)
                logger.info(f"Got {len(rdb_packages)} packages from RDB")
            except Exception as e:
                logger.error(f"Failed to fetch RDB data: {e}", exc_info=True)
        else:
            if not self.rdb:
                logger.warning(f"RDB client not initialized")
            if email not in self.rdb_mapping:
                logger.warning(f"Email {email} not found in RDB mapping")

        # Merge packages from both sources
        merged_packages = self.merger.merge_packages(repology_packages, rdb_packages)
        logger.info(
            f"Merged data: {len(repology_packages)} from Repology + "
            f"{len(rdb_packages)} from RDB = {len(merged_packages)} total"
        )

        # Try to find ALT names for packages that don't have rdb_pkg_name yet
        if self.rdb:
            for pkg in merged_packages:
                if not pkg.rdb_pkg_name and pkg.repo in ('altsisyphus', 'altlinux'):
                    # Try to find ALT package name
                    try:
                        alt_name = await self.rdb.find_alt_package_name(pkg.name)
                        if alt_name:
                            pkg.rdb_pkg_name = alt_name
                            logger.debug(f"Found ALT name for {pkg.name}: {alt_name}")
                    except Exception as e:
                        logger.debug(f"Failed to find ALT name for {pkg.name}: {e}")

        # Update cache
        await self._update_cache(email, merged_packages)

        return merged_packages
    
    async def get_outdated_packages(
        self,
        email: str,
        repo: Optional[str] = None,
        force_refresh: bool = False
    ) -> List[PackageInfo]:
        """
        Get only outdated packages for an email.
        
        Args:
            email: Maintainer email
            repo: Filter by repository
            force_refresh: Force cache refresh
            
        Returns:
            List of outdated PackageInfo objects
        """
        all_packages = await self.get_packages_for_email(email, repo, force_refresh)
        return [pkg for pkg in all_packages if pkg.is_outdated]
    
    async def get_package_stats(self, email: str, repo: Optional[str] = None) -> PackageStats:
        """
        Get statistics for packages of an email.
        
        Args:
            email: Maintainer email
            repo: Filter by repository
            
        Returns:
            PackageStats object
        """
        packages = await self.get_packages_for_email(email, repo)
        
        total = len(packages)
        outdated = sum(1 for pkg in packages if pkg.status == 'outdated')
        newest = sum(1 for pkg in packages if pkg.status == 'newest')
        other = total - outdated - newest
        
        # Get last check time from cache
        last_check = await self._get_last_cache_time(email, repo)
        
        return PackageStats(
            email=email,
            total=total,
            outdated=outdated,
            newest=newest,
            other=other,
            last_check=last_check
        )
    
    async def _get_from_cache(
        self,
        email: str,
        repo: Optional[str] = None
    ) -> Optional[List[PackageInfo]]:
        """Get packages from cache if not expired."""
        cutoff_time = datetime.now() - self.cache_duration

        query = """
            SELECT * FROM package_cache
            WHERE email = ? AND cached_at > ?
        """
        params = [email, cutoff_time]

        if repo:
            query += " AND repo = ?"
            params.append(repo)

        rows = await self.db.fetchall(query, tuple(params))

        if not rows:
            return None

        packages = []
        for row in rows:
            # Convert Row to dict for easier access
            row_dict = dict(row)

            pkg = PackageInfo(
                name=row_dict['package_name'],
                repo=row_dict['repo'],
                version=row_dict['current_version'] or 'unknown',
                status=row_dict['status'],
                newest_version=row_dict['latest_version'],
                # RDB fields (may not exist in old caches)
                has_rdb_data=bool(row_dict.get('has_rdb_data', 0)),
                rdb_pkg_name=row_dict.get('rdb_pkg_name'),
                rdb_new_version=row_dict.get('rdb_new_version'),
                rdb_url=row_dict.get('rdb_url'),
                rdb_date=row_dict.get('rdb_date')
            )
            packages.append(pkg)

            # Debug logging
            if pkg.status == 'outdated' and not pkg.newest_version and not pkg.rdb_new_version:
                logger.warning(f"Cached outdated package {pkg.name} has no newest_version! Row: {row_dict}")

        return packages
    
    async def _update_cache(self, email: str, packages: List[PackageInfo]):
        """Update package cache."""
        # Delete old cache entries for this email
        await self.db.execute(
            "DELETE FROM package_cache WHERE email = ?",
            (email,)
        )

        # Insert new cache entries
        for pkg in packages:
            await self.db.execute("""
                INSERT INTO package_cache
                (email, package_name, repo, current_version, latest_version, status,
                 has_rdb_data, rdb_pkg_name, rdb_new_version, rdb_url, rdb_date, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                email,
                pkg.name,
                pkg.repo,
                pkg.version,
                pkg.newest_version,
                pkg.status,
                1 if pkg.has_rdb_data else 0,
                pkg.rdb_pkg_name,
                pkg.rdb_new_version,
                pkg.rdb_url,
                pkg.rdb_date
            ))

        logger.info(f"Updated cache for {email}: {len(packages)} packages")
    
    async def _get_last_cache_time(
        self,
        email: str,
        repo: Optional[str] = None
    ) -> Optional[datetime]:
        """Get timestamp of last cache update."""
        query = "SELECT MAX(cached_at) as last_check FROM package_cache WHERE email = ?"
        params = [email]
        
        if repo:
            query += " AND repo = ?"
            params.append(repo)
        
        row = await self.db.fetchone(query, tuple(params))
        
        if row and row['last_check']:
            return datetime.fromisoformat(row['last_check'])
        return None
    
    async def cleanup_old_cache(self, days: int = 7):
        """
        Remove cache entries older than specified days.
        
        Args:
            days: Number of days to keep cache
        """
        cutoff = datetime.now() - timedelta(days=days)
        result = await self.db.execute(
            "DELETE FROM package_cache WHERE cached_at < ?",
            (cutoff,)
        )
        
        deleted = result.rowcount if hasattr(result, 'rowcount') else 0
        logger.info(f"Cleaned up {deleted} old cache entries")
