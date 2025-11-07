"""ALT Linux RDB API client."""
import logging
from typing import List, Optional, Dict
from datetime import datetime

import aiohttp

logger = logging.getLogger(__name__)


class RDBPackageInfo:
    """Information about a package from RDB."""

    def __init__(
        self,
        pkg_name: str,
        old_version: str,
        new_version: str,
        repology_name: str,
        url: str,
        date_update: str
    ):
        self.pkg_name = pkg_name
        self.old_version = old_version
        self.new_version = new_version
        self.repology_name = repology_name
        self.url = url
        self.date_update = date_update

    @classmethod
    def from_dict(cls, data: dict) -> 'RDBPackageInfo':
        """Create instance from API response dict."""
        return cls(
            pkg_name=data['pkg_name'],
            old_version=data['old_version'],
            new_version=data['new_version'],
            repology_name=data['repology_name'],
            url=data['url'],
            date_update=data['date_update']
        )


class RDBClient:
    """Client for ALT Linux RDB API."""

    def __init__(
        self,
        base_url: str = "https://rdb.altlinux.org/api/site",
        timeout: int = 30
    ):
        """
        Initialize RDB client.

        Args:
            base_url: RDB API base URL
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=self.timeout)
        return self.session

    async def close(self):
        """Close the client session."""
        if self.session and not self.session.closed:
            await self.session.close()

    async def get_packages_by_maintainer(
        self,
        maintainer_nickname: str,
        by_acl: str = "none"
    ) -> List[RDBPackageInfo]:
        """
        Get outdated packages for a maintainer from RDB.

        Args:
            maintainer_nickname: Maintainer nickname in RDB
            by_acl: ACL filter (none, default, all)

        Returns:
            List of RDBPackageInfo objects
        """
        session = await self._get_session()

        url = f"{self.base_url}/watch_by_maintainer"
        params = {
            "maintainer_nickname": maintainer_nickname,
            "by_acl": by_acl
        }

        logger.info(f"Fetching RDB data for maintainer: {maintainer_nickname}")

        try:
            async with session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()

                packages = []
                for pkg_data in data.get('packages', []):
                    try:
                        pkg = RDBPackageInfo.from_dict(pkg_data)
                        packages.append(pkg)
                    except (KeyError, ValueError) as e:
                        logger.warning(f"Failed to parse RDB package: {e}")
                        continue

                logger.info(f"Fetched {len(packages)} packages from RDB for {maintainer_nickname}")
                return packages

        except aiohttp.ClientError as e:
            logger.error(f"RDB API request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching from RDB: {e}", exc_info=True)
            return []

    async def find_alt_package_name(
        self,
        repology_name: str,
        branch: str = "sisyphus"
    ) -> Optional[str]:
        """
        Find ALT package name from Repology project name.

        Args:
            repology_name: Repology project name (e.g. "python:xdoctest")
            branch: Branch name (sisyphus, p10, etc.)

        Returns:
            ALT package name or None if not found
        """
        session = await self._get_session()

        # Extract the base name from repology format
        # For "python:xdoctest" -> try "xdoctest" and "python3-module-xdoctest"
        base_name = repology_name.split(':')[-1] if ':' in repology_name else repology_name

        # Try different name patterns
        search_patterns = []

        if repology_name.startswith('python:'):
            # Python packages: try with python3-module- prefix
            search_patterns = [
                f"python3-module-{base_name}",
                base_name
            ]
        elif repology_name.startswith('perl:'):
            search_patterns = [
                f"perl-{base_name}",
                base_name
            ]
        else:
            search_patterns = [base_name]

        url = f"{self.base_url}/find_packages"

        for search_name in search_patterns:
            params = {
                "name": search_name,
                "branch": branch
            }

            try:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        packages = data.get('packages', [])

                        if packages:
                            # Return the first exact or closest match
                            pkg_name = packages[0].get('name')
                            logger.debug(f"Found ALT name for '{repology_name}': {pkg_name} (searched: {search_name})")
                            return pkg_name

            except Exception as e:
                logger.debug(f"Failed to search for '{search_name}': {e}")
                continue

        logger.debug(f"No ALT package found for '{repology_name}'")
        return None
