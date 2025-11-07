"""Package data merger - combines RDB and Repology data."""
import logging
from typing import List, Dict
from models.package import PackageInfo
from services.rdb import RDBPackageInfo

logger = logging.getLogger(__name__)


class PackageMerger:
    """Merges package information from RDB and Repology."""

    @staticmethod
    def merge_packages(
        repology_packages: List[PackageInfo],
        rdb_packages: List[RDBPackageInfo]
    ) -> List[PackageInfo]:
        """
        Merge packages from RDB and Repology.

        Logic:
        1. Create a dict of Repology packages by name
        2. Deduplicate RDB packages (same repology_name) - pick best version
        3. For each RDB package, try to match with Repology by repology_name
        4. If match found, enrich Repology package with RDB data
        5. If no match, create new PackageInfo from RDB data
        6. Return combined list

        Args:
            repology_packages: List of packages from Repology
            rdb_packages: List of packages from RDB

        Returns:
            Merged list of PackageInfo objects
        """
        # Create lookup dict by (repology_name, repo) to handle same package in different repos
        # But also keep a simple name-only lookup for RDB matching
        repology_dict: Dict[tuple, PackageInfo] = {
            (pkg.name, pkg.repo): pkg for pkg in repology_packages
        }
        repology_by_name: Dict[str, List[PackageInfo]] = {}
        for pkg in repology_packages:
            if pkg.name not in repology_by_name:
                repology_by_name[pkg.name] = []
            repology_by_name[pkg.name].append(pkg)

        # Deduplicate RDB packages - keep the one with the newest version
        rdb_dedup: Dict[str, RDBPackageInfo] = {}
        for rdb_pkg in rdb_packages:
            name = rdb_pkg.repology_name
            if name not in rdb_dedup:
                rdb_dedup[name] = rdb_pkg
            else:
                # Compare versions - keep the newer one
                # Simple string comparison should work for most cases
                if rdb_pkg.new_version > rdb_dedup[name].new_version:
                    rdb_dedup[name] = rdb_pkg
                    logger.debug(f"Replaced RDB duplicate {name}: {rdb_dedup[name].new_version} -> {rdb_pkg.new_version}")

        logger.info(f"Deduplicated RDB: {len(rdb_packages)} -> {len(rdb_dedup)} packages")

        # Enrich/add RDB packages
        for repology_name, rdb_pkg in rdb_dedup.items():
            # Find matching Repology packages (may be in multiple repos)
            matching_pkgs = repology_by_name.get(repology_name, [])

            if matching_pkgs:
                # Enrich all matching Repology packages with RDB data
                for pkg in matching_pkgs:
                    # ALWAYS save ALT package name for reference (even if not using RDB data)
                    pkg.rdb_pkg_name = rdb_pkg.pkg_name

                    # Only mark as RDB if Repology doesn't have newest_version
                    # or RDB version is newer, or package is not outdated in Repology
                    use_rdb = False
                    if pkg.status != 'outdated':
                        # Repology doesn't consider it outdated, but RDB does
                        use_rdb = True
                        logger.debug(f"RDB marks {repology_name} as outdated but Repology has status={pkg.status}")
                    elif not pkg.newest_version:
                        use_rdb = True
                    elif rdb_pkg.new_version > pkg.newest_version:
                        use_rdb = True
                        logger.debug(f"RDB has newer version for {repology_name}: RDB={rdb_pkg.new_version} > Repology={pkg.newest_version}")

                    # Save full RDB data
                    pkg.rdb_new_version = rdb_pkg.new_version
                    pkg.rdb_url = rdb_pkg.url
                    pkg.rdb_date = rdb_pkg.date_update
                    pkg.has_rdb_data = use_rdb  # Mark as RDB only if we prefer RDB data

                    # If using RDB data, mark as outdated
                    if use_rdb and pkg.status != 'outdated':
                        pkg.status = 'outdated'

                    # Update version if RDB has different info
                    if pkg.version != rdb_pkg.old_version:
                        logger.debug(
                            f"Version mismatch for {repology_name} ({pkg.repo}): "
                            f"Repology={pkg.version}, RDB={rdb_pkg.old_version}"
                        )

                    logger.debug(f"Enriched {repology_name} ({pkg.repo}) with RDB data (use_rdb={use_rdb}, alt_name={rdb_pkg.pkg_name})")
            else:
                # Create new package from RDB data (not in Repology)
                new_pkg = PackageInfo(
                    name=repology_name,
                    repo="altsisyphus",  # RDB is ALT Linux specific
                    version=rdb_pkg.old_version,
                    status="outdated",  # RDB only shows outdated packages
                    newest_version=None,  # Will use RDB version
                    has_rdb_data=True,
                    rdb_pkg_name=rdb_pkg.pkg_name,
                    rdb_new_version=rdb_pkg.new_version,
                    rdb_url=rdb_pkg.url,
                    rdb_date=rdb_pkg.date_update
                )
                repology_dict[(repology_name, "altsisyphus")] = new_pkg
                logger.debug(f"Added new package from RDB: {repology_name}")

        # Return all packages (enriched + original + RDB-only)
        return list(repology_dict.values())

    @staticmethod
    def split_by_source(packages: List[PackageInfo]) -> tuple[List[PackageInfo], List[PackageInfo]]:
        """
        Split packages into two lists: RDB-sourced and Repology-only.

        Args:
            packages: List of merged packages

        Returns:
            Tuple of (packages_with_rdb, packages_repology_only)
        """
        rdb_packages = [pkg for pkg in packages if pkg.has_rdb_data]
        repology_only = [pkg for pkg in packages if not pkg.has_rdb_data]

        return rdb_packages, repology_only
