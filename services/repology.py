"""Repology API client."""
import asyncio
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

import aiohttp

from models.package import PackageInfo
from core.config import RepologyConfig

logger = logging.getLogger(__name__)


class RepologyClient:
    """Client for Repology API."""
    
    def __init__(self, config: RepologyConfig):
        """
        Initialize Repology client.
        
        Args:
            config: Repology configuration
        """
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
        self._last_request_time: Optional[datetime] = None
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
    
    async def start(self):
        """Initialize HTTP session."""
        if self.session is None:
            headers = {
                'User-Agent': 'repology-bot/0.1.0 (https://github.com/yourname/repology-bot)'
            }
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.config.request_timeout),
                headers=headers
            )
            logger.info("Repology client session started")
    
    async def close(self):
        """Close HTTP session."""
        if self.session:
            await asyncio.wait_for(self.session.close(), timeout=2.0)
            self.session = None
            logger.info("Repology client session closed")
    
    async def _rate_limit(self):
        """Apply rate limiting between requests."""
        if self._last_request_time is not None:
            elapsed = (datetime.now() - self._last_request_time).total_seconds()
            if elapsed < self.config.rate_limit_delay:
                await asyncio.sleep(self.config.rate_limit_delay - elapsed)
        self._last_request_time = datetime.now()
    
    async def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """
        Make API request with rate limiting.
        
        Args:
            endpoint: API endpoint
            params: Query parameters
            
        Returns:
            JSON response
            
        Raises:
            aiohttp.ClientError: On HTTP errors
        """
        if self.session is None:
            await self.start()
        
        await self._rate_limit()
        
        url = f"{self.config.api_base_url}/{endpoint}"
        
        try:
            logger.debug(f"Requesting: {url} with params: {params}")
            async with self.session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                logger.debug(f"Received {len(data) if isinstance(data, list) else 1} items")
                return data
        except aiohttp.ClientError as e:
            logger.error(f"Request failed: {url} - {e}")
            raise
    
    async def get_projects_by_maintainer(
        self,
        maintainer: str,
        repo: Optional[str] = None,
        outdated: bool = False
    ) -> List[PackageInfo]:
        """
        Get projects maintained by a specific email.

        Args:
            maintainer: Maintainer email
            repo: Filter by repository (optional)
            outdated: Filter only outdated packages (applied after fetching)

        Returns:
            List of PackageInfo objects
        """
        params = {"maintainer": maintainer}
        if repo:
            params["inrepo"] = repo
        # Note: We don't use outdated=1 param because it filters out 'newest' packages
        # We'll filter outdated packages ourselves after getting all data

        try:
            # Get projects data (returns dict where keys are project names)
            data = await self._request("projects/", params=params)

            if not data or not isinstance(data, dict):
                logger.info(f"No packages found for maintainer: {maintainer}")
                return []

            packages = []

            # Iterate through projects (data is dict: {project_name: [packages]})
            for project_name, project_packages in data.items():
                # Find newest version across all packages in this project
                newest_version = None

                # First try to find package with 'newest' status
                for pkg in project_packages:
                    if pkg.get('status') == 'newest':
                        newest_version = pkg.get('version')
                        break

                # If no 'newest', try to find max version from all non-legacy packages
                if not newest_version:
                    versions = []
                    for pkg in project_packages:
                        status = pkg.get('status', '')
                        if status not in ['legacy', 'incorrect', 'untrusted']:
                            versions.append(pkg.get('version'))

                    if versions:
                        # Sort versions and take the last one (simple string sort, may not be perfect)
                        newest_version = sorted(versions)[-1]
                        logger.debug(f"Project {project_name}: using max version {newest_version} (no 'newest' status)")

                for pkg_data in project_packages:
                    # Filter by maintainer if specified
                    if maintainer:
                        maintainers = pkg_data.get('maintainers', [])
                        if maintainer not in maintainers:
                            continue

                    # Filter by repo if specified
                    pkg_repo = pkg_data.get('repo', '')
                    if repo and pkg_repo != repo:
                        continue

                    # Create PackageInfo object
                    package = PackageInfo(
                        name=project_name,
                        repo=pkg_repo,
                        version=pkg_data.get('version', 'unknown'),
                        status=pkg_data.get('status', 'unknown'),
                        newest_version=newest_version,  # Use found newest version
                        summary=pkg_data.get('summary'),
                        categories=pkg_data.get('categories', []),
                        licenses=pkg_data.get('licenses', []),
                        srcurl=pkg_data.get('srcurl')
                    )
                    packages.append(package)

            logger.info(f"Found {len(packages)} packages for {maintainer}")
            return packages

        except Exception as e:
            logger.error(f"Failed to get projects for {maintainer}: {e}", exc_info=True)
            return []
    
    async def get_project_packages(
        self,
        project: str,
        maintainer: Optional[str] = None,
        repo: Optional[str] = None
    ) -> List[PackageInfo]:
        """
        Get package information for a specific project.
        
        Args:
            project: Project name
            maintainer: Filter by maintainer (optional)
            repo: Filter by repository (optional)
            
        Returns:
            List of PackageInfo objects for different repositories
        """
        try:
            data = await self._request(f"project/{project}")
            
            packages = []
            for pkg_data in data:
                # Filter by maintainer if specified
                if maintainer:
                    maintainers = pkg_data.get('maintainers', [])
                    if maintainer not in maintainers:
                        continue
                
                # Filter by repo if specified
                pkg_repo = pkg_data.get('repo', '')
                if repo and pkg_repo != repo:
                    continue
                
                # Create PackageInfo object
                package = PackageInfo(
                    name=project,
                    repo=pkg_repo,
                    version=pkg_data.get('version', 'unknown'),
                    status=pkg_data.get('status', 'unknown'),
                    newest_version=pkg_data.get('newest_upstream_release'),
                    summary=pkg_data.get('summary'),
                    categories=pkg_data.get('categories', []),
                    licenses=pkg_data.get('licenses', []),
                    srcurl=pkg_data.get('srcurl')
                )
                packages.append(package)
            
            return packages
            
        except Exception as e:
            logger.error(f"Failed to get project {project}: {e}")
            return []
    
    async def get_outdated_packages(
        self,
        maintainer: str,
        repo: Optional[str] = None
    ) -> List[PackageInfo]:
        """
        Get only outdated packages for a maintainer.
        
        Args:
            maintainer: Maintainer email
            repo: Filter by repository (optional)
            
        Returns:
            List of outdated PackageInfo objects
        """
        all_packages = await self.get_projects_by_maintainer(
            maintainer,
            repo=repo,
            outdated=True
        )
        
        # Filter to only outdated status
        outdated = [pkg for pkg in all_packages if pkg.is_outdated]
        
        logger.info(f"Found {len(outdated)} outdated packages for {maintainer}")
        return outdated
    
    async def get_project_info(self, project: str) -> Optional[Dict[str, List[dict]]]:
        """
        Get full information about a project with all distributions.
        
        Args:
            project: Project name
            
        Returns:
            Dictionary where keys are repository names and values are lists of package dicts,
            or None if project not found
        """
        try:
            data = await self._request(f"project/{project}")
            
            if not data or not isinstance(data, list):
                logger.debug(f"Project '{project}' not found or empty response")
                return None
            
            # Group packages by repository
            repo_dict = {}
            for pkg_data in data:
                repo = pkg_data.get('repo', '')
                if repo not in repo_dict:
                    repo_dict[repo] = []
                
                repo_dict[repo].append({
                    "repo": repo,
                    "name": pkg_data.get('name', project),
                    "version": pkg_data.get('version', ''),
                    "status": pkg_data.get('status', ''),
                    "maintainers": pkg_data.get('maintainers', []),
                    "licenses": pkg_data.get('licenses', []),
                    "summary": pkg_data.get('summary', ''),
                    "categories": pkg_data.get('categories', []),
                    "links": pkg_data.get('links', [])
                })
            
            logger.info(f"Found project '{project}' in {len(repo_dict)} repositories")
            return repo_dict
            
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                logger.debug(f"Project '{project}' not found in Repology")
                return None
            logger.error(f"Repology API error for project '{project}': {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to get project info for '{project}': {e}", exc_info=True)
            return None
