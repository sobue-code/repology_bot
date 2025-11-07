"""Message formatting utilities."""
import logging
from typing import List, Optional
from datetime import datetime

from models.package import PackageInfo, PackageStats

logger = logging.getLogger(__name__)


def format_package_list(
    packages: List[PackageInfo],
    email: str,
    page: int = 0,
    per_page: int = 20,
    show_all_statuses: bool = False
) -> tuple[str, int]:
    """
    Format package list for a single page with pagination.

    Args:
        packages: List of packages
        email: Maintainer email
        page: Current page (0-indexed)
        per_page: Packages per page
        show_all_statuses: Show all statuses or only outdated

    Returns:
        Tuple of (formatted message, total pages)
    """
    if not packages:
        return (
            f"✨ <b>Пакеты для {email}</b>\n\n"
            f"Пакетов не найдено",
            1
        )

    # Filter packages if needed
    if not show_all_statuses:
        packages = [pkg for pkg in packages if pkg.is_outdated]

    if not packages and not show_all_statuses:
        return (
            f"✅ <b>Все пакеты актуальны!</b>\n\n"
            f"<code>{email}</code>\n\n"
            f"Нет outdated пакетов",
            1
        )

    # Count by status
    total_packages = len(packages)
    outdated_count = sum(1 for pkg in packages if pkg.status == 'outdated')
    newest_count = sum(1 for pkg in packages if pkg.status == 'newest')
    other_count = total_packages - outdated_count - newest_count

    # Calculate pagination
    total_pages = (total_packages + per_page - 1) // per_page
    page = max(0, min(page, total_pages - 1))  # Clamp page

    start_idx = page * per_page
    end_idx = min(start_idx + per_page, total_packages)
    page_packages = packages[start_idx:end_idx]

    lines = []

    # Header
    if show_all_statuses:
        lines.append(f"📦 <b>Пакеты для {email}</b>\n")
    else:
        lines.append(f"⚠️ <b>Outdated пакеты</b>\n")
        lines.append(f"<code>{email}</code>\n")

    # Show packages with global numbering
    for i, pkg in enumerate(page_packages, start_idx + 1):
        status_emoji = _get_status_emoji(pkg.status)

        if pkg.is_outdated:
            newest = pkg.best_newest_version
            source = "🔴 RDB" if pkg.has_rdb_data else "🟣 Repology"

            if newest:
                # Build package info lines
                pkg_lines = [
                    f"<b>{i}.</b> <b>{pkg.name}</b> {status_emoji} {source}\n"
                ]

                # Show RDB package name if different from repology name
                if pkg.rdb_pkg_name and pkg.rdb_pkg_name != pkg.name:
                    pkg_lines.append(f"   ALT: <code>{pkg.rdb_pkg_name}</code>\n")

                pkg_lines.append(f"   <i>{pkg.repo}</i>\n")
                pkg_lines.append(f"   <code>{pkg.version}</code> ▸ <b>{newest}</b>\n")

                # Add appropriate link
                if pkg.has_rdb_data and pkg.rdb_pkg_name:
                    # Link to packages.altlinux.org
                    alt_url = f"https://packages.altlinux.org/ru/sisyphus/srpms/{pkg.rdb_pkg_name}/"
                    pkg_lines.append(f"   <a href='{alt_url}'>🔗 Пакет в ALT</a>")
                    if pkg.rdb_date:
                        pkg_lines.append(f" • <i>{pkg.rdb_date[:10]}</i>")
                    pkg_lines.append("\n")
                elif pkg.rdb_pkg_name:
                    # Has RDB data but prefer Repology - show both links
                    alt_url = f"https://packages.altlinux.org/ru/sisyphus/srpms/{pkg.rdb_pkg_name}/"
                    pkg_lines.append(f"   <a href='{pkg.repology_url}'>🔗 Repology</a> • <a href='{alt_url}'>ALT</a>\n")
                else:
                    pkg_lines.append(f"   <a href='{pkg.repology_url}'>🔗 Подробнее</a>\n")

                lines.append("".join(pkg_lines))
            else:
                lines.append(
                    f"<b>{i}.</b> <b>{pkg.name}</b> {status_emoji} {source}\n"
                    f"   <i>{pkg.repo}</i>\n"
                    f"   <code>{pkg.version}</code> (новая версия неизвестна)\n"
                    f"   <a href='{pkg.repology_url}'>🔗 Подробнее</a>\n"
                )
        else:
            lines.append(
                f"{i}. {pkg.name} {status_emoji} · <code>{pkg.version}</code> · <i>{pkg.repo}</i>\n"
            )

    # Footer with stats
    lines.append("\n" + "━" * 28)
    if show_all_statuses:
        lines.append(
            f"\n📊 <b>Итого:</b> {total_packages} {_plural_packages(total_packages)}\n"
            f"⚠️ Outdated: <b>{outdated_count}</b> · "
            f"✅ Newest: <b>{newest_count}</b>"
            + (f" · ℹ️ Другие: {other_count}" if other_count > 0 else "")
        )
    else:
        if outdated_count > 0:
            lines.append(f"\n⚠️ <b>{outdated_count}</b> {_plural_packages(outdated_count)} требуют обновления")
        else:
            lines.append(f"\n✅ Все пакеты актуальны!")

    # Page indicator if multiple pages
    if total_pages > 1:
        lines.append(f"\n📄 Страница {page + 1} из {total_pages}")

    return ("\n".join(lines), total_pages)


def format_package_stats(stats: PackageStats) -> str:
    """
    Format package statistics.

    Args:
        stats: PackageStats object

    Returns:
        Formatted message
    """
    # Create progress bar for outdated percentage
    bar_length = 10
    filled = int(bar_length * stats.outdated_percentage / 100)
    bar = "█" * filled + "░" * (bar_length - filled)

    lines = [
        f"📊 <b>Статистика</b>\n",
        f"<code>{stats.email}</code>\n",
        f"┌ Всего пакетов: <b>{stats.total}</b>",
        f"├ ⚠️ Outdated: <b>{stats.outdated}</b> ({stats.outdated_percentage:.1f}%)",
        f"│ {bar}",
        f"├ ✅ Newest: {stats.newest}",
        f"└ ℹ️ Другие: {stats.other}",
    ]

    if stats.last_check:
        lines.append(f"\n🕐 Обновлено: <i>{format_datetime(stats.last_check)}</i>")

    return "\n".join(lines)


def format_datetime(dt) -> str:
    """
    Format datetime in Russian locale.

    Args:
        dt: Datetime object or string

    Returns:
        Formatted string
    """
    if isinstance(dt, str):
        # Parse SQLite datetime string format
        try:
            from datetime import datetime as dt_class
            # Try ISO format first (e.g., "2025-01-07 15:30:00")
            parsed_dt = dt_class.fromisoformat(dt.replace('Z', '+00:00'))
            return parsed_dt.strftime("%d.%m.%Y %H:%M")
        except (ValueError, AttributeError):
            # If parsing fails, return as-is
            return dt
    elif isinstance(dt, datetime):
        return dt.strftime("%d.%m.%Y %H:%M")
    else:
        return str(dt)


def format_user_info(name: str, telegram_id: int, emails: List[str]) -> str:
    """
    Format user information.

    Args:
        name: User name
        telegram_id: Telegram ID
        emails: List of emails

    Returns:
        Formatted message
    """
    lines = [
        f"👤 <b>Профиль</b>\n",
        f"<b>{name}</b>",
        f"<code>ID: {telegram_id}</code>\n",
        f"📧 <b>Отслеживаемые email:</b>",
    ]

    for i, email in enumerate(emails, 1):
        if i == len(emails):
            lines.append(f"└ <code>{email}</code>")
        else:
            lines.append(f"├ <code>{email}</code>")

    return "\n".join(lines)


def _get_status_emoji(status: str) -> str:
    """Get emoji for package status."""
    emoji_map = {
        'outdated': '⚠️',
        'newest': '✅',
        'devel': '🔧',
        'unique': '⭐',
        'legacy': '📦',
        'incorrect': '❌',
        'untrusted': '⚠️',
        'noscheme': '❓',
        'rolling': '🔄'
    }
    return emoji_map.get(status, 'ℹ️')


def _plural_packages(count: int) -> str:
    """Get correct plural form for 'пакет'."""
    if count % 10 == 1 and count % 100 != 11:
        return "пакет"
    elif 2 <= count % 10 <= 4 and (count % 100 < 10 or count % 100 >= 20):
        return "пакета"
    else:
        return "пакетов"


def split_message(text: str, max_length: int = 4096) -> List[str]:
    """
    Split long message into chunks.
    
    Args:
        text: Message text
        max_length: Maximum length per message
        
    Returns:
        List of message chunks
    """
    if len(text) <= max_length:
        return [text]
    
    chunks = []
    lines = text.split('\n')
    current_chunk = []
    current_length = 0
    
    for line in lines:
        line_length = len(line) + 1  # +1 for newline
        
        if current_length + line_length > max_length:
            # Save current chunk
            if current_chunk:
                chunks.append('\n'.join(current_chunk))
            current_chunk = [line]
            current_length = line_length
        else:
            current_chunk.append(line)
            current_length += line_length
    
    # Add remaining chunk
    if current_chunk:
        chunks.append('\n'.join(current_chunk))
    
    return chunks


# ===== Package Search Formatting =====

def format_package_details(
    package_name: str,
    rdb_info: Optional[dict],
    repology_info: Optional[dict]
) -> str:
    """
    Format detailed package information.

    Args:
        package_name: Package name
        rdb_info: Information from RDB
        repology_info: Information from Repology

    Returns:
        Formatted text for Telegram (HTML)
    """
    import html
    
    # Escape package name for HTML
    safe_name = html.escape(package_name)
    lines = [f"📦 <b>{safe_name}</b>\n"]

    # === Information from RDB (ALT Linux) ===
    if rdb_info:
        lines.append("🐧 <b>ALT Linux</b>")

        version = rdb_info.get('version', 'N/A')
        release = rdb_info.get('release', '')
        full_version = f"{version}-{release}" if release else version
        lines.append(f"  • Версия: <code>{html.escape(full_version)}</code>")

        branch = rdb_info.get('branch', 'sisyphus')
        lines.append(f"  • Ветка: <b>{html.escape(branch)}</b>")

        maintainer = rdb_info.get('maintainer', {})
        if isinstance(maintainer, dict):
            maint_name = maintainer.get('name', 'Unknown')
            maint_nick = maintainer.get('nickname', '')
            if maint_nick:
                lines.append(f"  • Мантейнер: {html.escape(maint_name)} (<code>@{html.escape(maint_nick)}</code>)")
            else:
                lines.append(f"  • Мантейнер: {html.escape(maint_name)}")
        elif isinstance(maintainer, str):
            lines.append(f"  • Мантейнер: {html.escape(maintainer)}")

        # Description
        summary = rdb_info.get('summary', '')
        if summary:
            lines.append(f"  • Описание: <i>{html.escape(summary)}</i>")

        # License
        license_info = rdb_info.get('license', '')
        if license_info:
            lines.append(f"  • Лицензия: <code>{html.escape(license_info)}</code>")

        # Homepage
        url = rdb_info.get('url', '')
        if url:
            lines.append(f"  • Сайт: {url}")

        # Build date
        build_time = rdb_info.get('build_time', '')
        if build_time:
            formatted_time = format_datetime(build_time)
            lines.append(f"  • Дата сборки: {formatted_time}")

        # Link to packages.altlinux.org
        alt_link = f"https://packages.altlinux.org/en/sisyphus/srpms/{package_name}/"
        lines.append(f"  • Ссылка: {alt_link}")

        lines.append("")

    # === Information from Repology ===
    if repology_info:
        lines.append("🌐 <b>Repology</b>")

        # Find newest version
        newest_version = find_newest_version(repology_info)
        if newest_version:
            lines.append(f"  • Новейшая версия: <code>{html.escape(newest_version)}</code>")

        # Check status in ALT Linux
        alt_status = get_altlinux_status(repology_info)
        if alt_status:
            status_emoji = {
                'newest': '✅',
                'outdated': '⚠️',
                'legacy': '🔴',
                'unique': '🔵',
                'devel': '🔧',
                'noscheme': '❓'
            }.get(alt_status, '❓')
            lines.append(f"  • Статус ALT: {status_emoji} {alt_status}")

        # RPM-based distributions
        rpm_distros = filter_rpm_distros(repology_info)
        if rpm_distros:
            lines.append(f"\n📊 <b>RPM дистрибутивы</b> (найдено: {len(rpm_distros)})")

            # Sort by distribution name
            sorted_distros = sorted(rpm_distros.items(), key=lambda x: x[0])

            for repo, packages in sorted_distros[:15]:  # Limit to 15
                distro_name = format_distro_name(repo)

                # Take first package from list
                pkg = packages[0] if packages else {}
                version = pkg.get('version', 'N/A')
                status = pkg.get('status', '')

                status_emoji = {
                    'newest': '✅',
                    'outdated': '⚠️',
                    'legacy': '🔴',
                    'unique': '🔵',
                    'devel': '🔧'
                }.get(status, '')

                lines.append(f"  • {distro_name}: <code>{html.escape(version)}</code> {status_emoji}")

            if len(rpm_distros) > 15:
                lines.append(f"  <i>... и ещё {len(rpm_distros) - 15}</i>")

        # Link to Repology
        repology_link = f"https://repology.org/project/{package_name}/versions"
        lines.append(f"\n  • Подробнее: {repology_link}")

    # If no information from any source
    if not rdb_info and not repology_info:
        lines.append("❌ Информация о пакете не найдена.")

    result = "\n".join(lines)
    
    # Ensure message is not too long
    if len(result) > 4096:
        # Truncate if needed
        result = result[:4000] + "\n\n<i>... (сообщение обрезано)</i>"
    
    return result


def find_newest_version(repology_info: dict) -> Optional[str]:
    """
    Find the newest version from all distributions.
    """
    newest = None
    for repo, packages in repology_info.items():
        for pkg in packages:
            if pkg.get('status') == 'newest':
                version = pkg.get('version')
                if version:
                    return version
    return newest


def get_altlinux_status(repology_info: dict) -> Optional[str]:
    """
    Get package status in ALT Linux.
    """
    for repo, packages in repology_info.items():
        if repo.startswith('altlinux_'):
            if packages:
                return packages[0].get('status')
    return None


def filter_rpm_distros(repology_info: dict) -> dict:
    """
    Filter only RPM-based distributions.
    """
    rpm_prefixes = [
        'altlinux_',
        'fedora_',
        'opensuse_',
        'mageia_',
        'rosa_',
        'openmandriva_',
        'pclinuxos',
        'centos_',
        'rhel_',
        'oracle_linux_',
        'amazon_linux_'
    ]

    filtered = {}
    for repo, packages in repology_info.items():
        if any(repo.startswith(prefix) for prefix in rpm_prefixes):
            filtered[repo] = packages

    return filtered


def format_distro_name(repo: str) -> str:
    """
    Format distribution name for readability.
    """
    # Dictionary of replacements
    replacements = {
        'altlinux_sisyphus': 'ALT Sisyphus',
        'altlinux_p10': 'ALT P10',
        'altlinux_p9': 'ALT P9',
        'fedora_rawhide': 'Fedora Rawhide',
        'opensuse_tumbleweed': 'openSUSE Tumbleweed',
        'opensuse_leap': 'openSUSE Leap',
        'pclinuxos': 'PCLinuxOS',
    }

    if repo in replacements:
        return replacements[repo]

    # General processing
    parts = repo.split('_')
    if len(parts) >= 2:
        distro = parts[0].capitalize()
        version = ' '.join(parts[1:]).upper()
        return f"{distro} {version}"

    return repo.capitalize()
