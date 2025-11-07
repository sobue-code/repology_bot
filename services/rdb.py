"""ALT Linux RDB API client."""
import asyncio
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
            await asyncio.wait_for(self.session.close(), timeout=2.0)

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

    async def validate_maintainer(self, nickname: str) -> bool:
        """
        Validate that a maintainer exists in RDB.

        Args:
            nickname: Maintainer nickname to validate

        Returns:
            True if maintainer exists, False otherwise
        """
        session = await self._get_session()

        url = f"{self.base_url}/watch_by_maintainer"
        params = {
            "maintainer_nickname": nickname,
            "by_acl": "none"
        }

        try:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    # If we get a valid response, maintainer exists
                    # Even if they have no packages, the endpoint will return empty list
                    logger.debug(f"Maintainer '{nickname}' validated successfully")
                    return True
                elif response.status == 404:
                    logger.debug(f"Maintainer '{nickname}' not found")
                    return False
                else:
                    # For other status codes, assume maintainer might exist
                    logger.warning(f"Unexpected status {response.status} when validating '{nickname}'")
                    return True

        except aiohttp.ClientError as e:
            logger.error(f"Failed to validate maintainer '{nickname}': {e}")
            # On network error, assume valid to allow adding
            return True
        except Exception as e:
            logger.error(f"Unexpected error validating maintainer '{nickname}': {e}")
            return True

    async def search_packages(self, query: str, limit: int = 50) -> List[dict]:
        """
        Search packages in RDB by name (full or partial).

        Args:
            query: Search query string
            limit: Maximum number of results (default: 50)

        Returns:
            List of dictionaries with package information
        """
        session = await self._get_session()

        url = f"{self.base_url}/find_packages"
        params = {
            "name": query
            # Note: branch parameter is not used in find_packages endpoint
            # We filter by branch after getting results
        }

        logger.info(f"Searching RDB packages with query: {query}")

        try:
            async with session.get(url, params=params) as response:
                if response.status == 404:
                    logger.debug(f"No packages found for query: {query}")
                    return []

                response.raise_for_status()
                data = await response.json()

                packages = []
                seen_names = set()  # For deduplication

                # Process packages from response
                for pkg in data.get('packages', []):
                    name = pkg.get('name')
                    if not name or name in seen_names:
                        continue

                    seen_names.add(name)

                    # Find sisyphus version (prefer main sisyphus, fallback to other sisyphus variants)
                    sisyphus_version = None
                    for version_info in pkg.get('versions', []):
                        branch = version_info.get('branch', '')
                        if branch == 'sisyphus':
                            sisyphus_version = version_info
                            break
                    
                    # If no main sisyphus, try any sisyphus variant
                    if not sisyphus_version:
                        for version_info in pkg.get('versions', []):
                            branch = version_info.get('branch', '')
                            if 'sisyphus' in branch.lower():
                                sisyphus_version = version_info
                                break

                    if not sisyphus_version:
                        continue  # Skip if no sisyphus version

                    packages.append({
                        "name": name,
                        "version": sisyphus_version.get('version', ''),
                        "release": sisyphus_version.get('release', ''),
                        "branch": sisyphus_version.get('branch', 'sisyphus'),
                        "maintainer": pkg.get('maintainer', ''),
                        "summary": pkg.get('summary', ''),
                        "url": pkg.get('url', '')
                    })

                logger.info(f"Found {len(packages)} packages in RDB for query: {query}")
                return packages

        except aiohttp.ClientTimeout:
            logger.error(f"RDB search timeout for query: {query}")
            return []
        except aiohttp.ClientError as e:
            logger.error(f"RDB search error for query '{query}': {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error in RDB search: {e}", exc_info=True)
            raise

    async def get_package_details(self, package_name: str, branch: str = "sisyphus") -> Optional[dict]:
        """
        Get detailed information about a specific package.

        Args:
            package_name: Package name
            branch: Repository branch (default: "sisyphus")

        Returns:
            Dictionary with detailed package information or None if not found
        """
        session = await self._get_session()

        url = f"{self.base_url}/package/{package_name}"
        params = {
            "branch": branch
        }

        logger.info(f"Fetching RDB package details: {package_name} (branch: {branch})")

        try:
            async with session.get(url, params=params) as response:
                if response.status == 404:
                    logger.debug(f"Package not found: {package_name}")
                    return None

                response.raise_for_status()
                data = await response.json()

                # Extract package information
                pkg_info = {
                    "name": data.get('name', package_name),
                    "version": data.get('version', ''),
                    "release": data.get('release', ''),
                    "epoch": data.get('epoch'),
                    "arch": data.get('arch', ''),
                    "branch": data.get('branch', branch),
                    "maintainer": data.get('maintainer', {}),
                    "summary": data.get('summary', ''),
                    "description": data.get('description', ''),
                    "license": data.get('license', ''),
                    "url": data.get('url', ''),
                    "source_rpm": data.get('source_rpm', ''),
                    "build_time": data.get('build_time', ''),
                    "packager": data.get('packager', ''),
                    "changelog": data.get('changelog', []),
                    "dependencies": data.get('dependencies', {}),
                    "files": data.get('files', [])
                }

                logger.debug(f"Successfully fetched details for {package_name}")
                return pkg_info

        except aiohttp.ClientTimeout:
            logger.error(f"RDB get_package_details timeout for: {package_name}")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"RDB get_package_details error for '{package_name}': {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in get_package_details: {e}", exc_info=True)
            raise
