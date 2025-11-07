"""Package search handlers."""
import asyncio
import html
import logging
from typing import List, Optional

from aiogram import Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot import keyboards
from services.rdb import RDBClient
from services.repology import RepologyClient
from services.package_checker import PackageChecker
from utils.formatting import format_package_details

logger = logging.getLogger(__name__)

# Create router
router = Router()


# FSM States
class SearchStates(StatesGroup):
    waiting_for_package_name = State()


# Helper functions
async def safe_answer_callback(callback: CallbackQuery, text: str = "", show_alert: bool = False):
    """Safely answer callback query, ignoring timeout errors."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except Exception as e:
        if "query is too old" in str(e):
            logger.debug(f"Callback query too old, ignoring: {e}")
        else:
            logger.warning(f"Error answering callback: {e}")


async def safe_edit_message(message: Message, text: str, **kwargs):
    """Safely edit message, ignoring 'message is not modified' errors."""
    try:
        from aiogram.exceptions import TelegramBadRequest
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            # Message is already in the correct state, ignore
            logger.debug("Message not modified, content is the same")
        else:
            raise


# ===== Search Command Handlers =====

@router.message(Command("search"))
async def cmd_search(
    message: Message,
    state: FSMContext,
    command: CommandObject = None,
    rdb_client: RDBClient = None,
    package_checker: PackageChecker = None
):
    """
    Handle /search command.
    If no arguments, start dialog. If arguments provided, perform search directly.
    """
    if command and command.args:
        # Direct search with argument
        query = command.args.strip()
        if query and rdb_client and package_checker:
            await perform_search(message, query, state, rdb_client, package_checker)
            return

    # Start dialog
    await message.answer(
        "🔍 <b>Поиск пакетов</b>\n\n"
        "Введите название пакета для поиска.\n"
        "Можно вводить полное или частичное название.\n\n"
        "<i>Например: python, nginx, gcc</i>",
        reply_markup=keyboards.cancel_search_keyboard()
    )
    await state.set_state(SearchStates.waiting_for_package_name)


@router.callback_query(F.data == "search")
async def callback_search(callback: CallbackQuery, state: FSMContext):
    """Handle search button from main menu."""
    await safe_edit_message(callback.message,
        "🔍 <b>Поиск пакетов</b>\n\n"
        "Введите название пакета для поиска.\n"
        "Можно вводить полное или частичное название.\n\n"
        "<i>Например: python, nginx, gcc</i>",
        reply_markup=keyboards.cancel_search_keyboard()
    )
    await state.set_state(SearchStates.waiting_for_package_name)
    await safe_answer_callback(callback)


@router.message(SearchStates.waiting_for_package_name)
async def process_search_input(
    message: Message,
    state: FSMContext,
    rdb_client: RDBClient,
    package_checker: PackageChecker
):
    """Process package name input from user."""
    query = message.text.strip()

    if not query:
        await message.answer(
            "❌ Пожалуйста, введите корректное название пакета.",
            reply_markup=keyboards.cancel_search_keyboard()
        )
        return

    # Validate length
    if len(query) > 100:
        await message.answer(
            "❌ Название пакета слишком длинное (максимум 100 символов).",
            reply_markup=keyboards.cancel_search_keyboard()
        )
        return

    await state.clear()
    await perform_search(message, query, state, rdb_client, package_checker)


@router.callback_query(F.data == "cancel_search")
async def callback_cancel_search(callback: CallbackQuery, state: FSMContext):
    """Cancel search and return to main menu."""
    await state.clear()
    await safe_edit_message(callback.message,
        "🏠 Главное меню",
        reply_markup=keyboards.main_menu_keyboard()
    )
    await safe_answer_callback(callback)


# ===== Search Functions =====

async def perform_search(
    message: Message,
    query: str,
    state: FSMContext,
    rdb_client: RDBClient,
    package_checker: PackageChecker
):
    """
    Main search function.
    Performs parallel search in RDB and Repology.
    """
    # Show loading indicator
    status_msg = await message.answer("🔍 Поиск пакетов...")

    try:
        # Parallel search in RDB and Repology
        async def empty_rdb_search():
            return []

        async def empty_repology_search():
            return []

        # Search RDB with the query as-is - RDB API will return correct results
        rdb_task = rdb_client.search_packages(query) if rdb_client else empty_rdb_search()
        repology_task = (
            search_in_repology(query, package_checker.repology)
            if package_checker and package_checker.repology
            else empty_repology_search()
        )

        rdb_results, repology_results = await asyncio.gather(
            rdb_task,
            repology_task,
            return_exceptions=True
        )

        # Handle errors
        if isinstance(rdb_results, Exception):
            logger.error(f"RDB search error: {rdb_results}")
            rdb_results = []

        if isinstance(repology_results, Exception):
            logger.error(f"Repology search error: {repology_results}")
            repology_results = []

        # Try to find RDB packages for Repology projects with prefixes
        if rdb_client and repology_results:
            additional_rdb_results = await find_rdb_packages_for_repology_projects(
                repology_results,
                rdb_client
            )
            # Merge additional results, avoiding duplicates
            existing_names = {pkg.get('name') for pkg in rdb_results}
            for pkg in additional_rdb_results:
                if pkg.get('name') not in existing_names:
                    rdb_results.append(pkg)

        # Merge and deduplicate results
        combined_results = merge_search_results(rdb_results, repology_results)

        if not combined_results:
            await status_msg.edit_text(
                f"❌ Пакеты по запросу '<code>{html.escape(query)}</code>' не найдены.\n\n"
                "Попробуйте изменить запрос или проверить написание.",
                reply_markup=keyboards.back_to_search_keyboard()
            )
            return

        # Save results in state for pagination
        await state.update_data(
            search_query=query,
            search_results=combined_results
        )

        # Show first page of results
        await show_search_results_page(
            status_msg,
            combined_results,
            query,
            page=0
        )

    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        await status_msg.edit_text(
            "❌ Произошла ошибка при поиске пакетов.\n"
            "Попробуйте позже.",
            reply_markup=keyboards.back_to_search_keyboard()
        )


async def search_in_repology(query: str, repology_client: Optional[RepologyClient]) -> List[str]:
    """
    Search projects in Repology.
    Uses get_project_info to check existence.
    Simply searches for the exact query name.
    """
    if not repology_client:
        return []

    results = []

    # Try exact match
    try:
        info = await repology_client.get_project_info(query)
        if info:
            results.append(query)
    except Exception as e:
        logger.debug(f"Failed to check project '{query}': {e}")

    return results


async def find_rdb_packages_for_repology_projects(
    repology_projects: List[str],
    rdb_client: RDBClient
) -> List[dict]:
    """
    Try to find RDB packages for Repology projects that have prefixes.
    For example, if Repology has "python:glpi-api", try to find "python3-module-glpi-api" or "glpi-api" in RDB.
    """
    additional_results = []
    
    for project in repology_projects:
        # Extract base name from prefixed projects
        base_name = None
        search_variants = []
        
        if ':' in project:
            # Handle prefixed projects like "python:glpi-api"
            prefix, base = project.split(':', 1)
            base_name = base
            
            if prefix == 'python':
                # Python packages in ALT are often named python3-module-<name>
                search_variants = [
                    f"python3-module-{base_name}",
                    base_name,
                    base_name.replace('-', '_'),
                    base_name.replace('_', '-')
                ]
            elif prefix == 'perl':
                # Perl packages in ALT are often named perl-<name>
                search_variants = [
                    f"perl-{base_name}",
                    base_name
                ]
            else:
                # For other prefixes, just try the base name
                search_variants = [base_name]
        else:
            # No prefix, use as-is
            base_name = project
            search_variants = [
                project,
                project.replace('-', '_'),
                project.replace('_', '-')
            ]
        
        # Try to find in RDB using variants
        for variant in search_variants:
            try:
                # Use find_alt_package_name which already has logic for finding packages
                alt_name = await rdb_client.find_alt_package_name(project, branch="sisyphus")
                if alt_name:
                    # Get details to add to results
                    details = await rdb_client.get_package_details(alt_name, branch="sisyphus")
                    if details:
                        additional_results.append({
                            "name": alt_name,
                            "version": details.get('version', ''),
                            "release": details.get('release', ''),
                            "arch": details.get('arch', ''),
                            "branch": details.get('branch', 'sisyphus'),
                            "maintainer": details.get('maintainer', '')
                        })
                        break  # Found, no need to try other variants
            except Exception as e:
                logger.debug(f"Failed to find RDB package for '{project}' variant '{variant}': {e}")
                continue
    
    return additional_results


def merge_search_results(rdb_results: List[dict], repology_projects: List[str]) -> List[dict]:
    """
    Merge and deduplicate results from RDB and Repology.
    Shows all Repology projects as separate entries, even if they match the same RDB package.
    """
    merged = {}
    rdb_by_name = {pkg.get('name'): pkg for pkg in rdb_results if pkg.get('name')}

    # Helper function to extract base name from prefixed projects
    def get_base_name(name: str) -> str:
        if ':' in name:
            return name.split(':', 1)[1]
        return name

    # Helper function to normalize name for comparison
    def normalize_name(name: str) -> str:
        name = name.lower()
        # Remove common prefixes
        for prefix in ['python3-module-', 'perl-', 'lib']:
            if name.startswith(prefix):
                name = name[len(prefix):]
        return name.replace('-', '_').replace('_', '-')

    # First, add all RDB results
    for pkg in rdb_results:
        name = pkg.get('name')
        if not name:
            continue
        merged[name] = {
            'name': name,
            'in_rdb': True,
            'in_repology': False,
            'rdb_info': pkg,
            'repology_project': None
        }

    # Then, add all Repology projects as separate entries
    # Try to match them with RDB packages, but keep them as separate entries
    for project in repology_projects:
        project_base = get_base_name(project)
        project_normalized = normalize_name(project_base)
        
        # Try to find matching RDB package
        matched_rdb_name = None
        for rdb_name, rdb_pkg in rdb_by_name.items():
            rdb_normalized = normalize_name(rdb_name)
            # Check if names match after normalization
            if rdb_normalized == project_normalized:
                matched_rdb_name = rdb_name
                break
            # Also check if RDB name contains project base name or vice versa
            if project_base.lower() in rdb_name.lower() or rdb_name.lower() in project_base.lower():
                matched_rdb_name = rdb_name
                break

        # Always add Repology project as separate entry
        # Use project name as key to ensure uniqueness
        if project not in merged:
            merged[project] = {
                'name': project,
                'in_rdb': False,
                'in_repology': True,
                'repology_project': project,
                'rdb_info': None
            }
        
        # If matched with RDB package, mark the connection
        if matched_rdb_name and matched_rdb_name in merged:
            merged[project]['in_rdb'] = True
            merged[project]['rdb_info'] = rdb_by_name[matched_rdb_name]
            # Also mark the RDB entry as having Repology match
            merged[matched_rdb_name]['in_repology'] = True
            if not merged[matched_rdb_name]['repology_project']:
                merged[matched_rdb_name]['repology_project'] = project

    # Convert to list and sort
    results = list(merged.values())
    results.sort(key=lambda x: x['name'])

    return results


async def show_search_results_page(
    message: Message,
    results: List[dict],
    query: str,
    page: int
):
    """
    Display a page of search results.
    """
    ITEMS_PER_PAGE = 10
    total_pages = (len(results) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_results = results[start_idx:end_idx]

    # Format message
    safe_query = html.escape(query)
    text = f"🔍 <b>Результаты поиска:</b> <code>{safe_query}</code>\n\n"
    text += f"Найдено пакетов: <b>{len(results)}</b>\n"
    text += f"Страница {page + 1} из {total_pages}\n\n"

    for i, pkg in enumerate(page_results, start=start_idx + 1):
        name = html.escape(pkg['name'])
        status_icons = []

        if pkg.get('in_rdb'):
            status_icons.append("📦 ALT")
        if pkg.get('in_repology'):
            status_icons.append("🌐 Repology")

        status = " | ".join(status_icons) if status_icons else "❓"
        text += f"{i}. <code>{name}</code>\n   {status}\n\n"

    # Create keyboard
    keyboard = keyboards.search_results_keyboard(
        page_results,
        query,
        page,
        total_pages
    )

    await message.edit_text(text, reply_markup=keyboard)


# ===== Result Selection Handlers =====

@router.callback_query(F.data.startswith("search_result:"))
async def callback_search_result(
    callback: CallbackQuery,
    state: FSMContext,
    rdb_client: RDBClient,
    package_checker: PackageChecker
):
    """Handle selection of a package from search results."""
    package_name = callback.data.split(":", 1)[1]

    await safe_edit_message(callback.message,"⏳ Загрузка информации о пакете...")

    try:
        # Get search results from state to find Repology project name
        data = await state.get_data()
        search_results = data.get('search_results', [])
        
        # Find the result entry to get Repology project name if available
        repology_project = None
        for result in search_results:
            if result.get('name') == package_name:
                repology_project = result.get('repology_project')
                break
        
        await show_package_details(
            callback.message,
            package_name,
            repology_project,
            state,
            rdb_client,
            package_checker
        )
    except Exception as e:
        logger.error(f"Error showing package details: {e}", exc_info=True)
        await safe_edit_message(callback.message,
            "❌ Ошибка при загрузке информации о пакете.",
            reply_markup=keyboards.back_to_search_keyboard()
        )

    await safe_answer_callback(callback)


async def show_package_details(
    message: Message,
    package_name: str,
    repology_project: Optional[str],
    state: FSMContext,
    rdb_client: RDBClient,
    package_checker: PackageChecker
):
    """
    Display detailed information about a package.
    
    Args:
        message: Message to edit
        package_name: Package name (from RDB or search results)
        repology_project: Repology project name (if different from package_name)
        state: FSM context
        rdb_client: RDB client
        package_checker: Package checker
    """
    # Parallel loading from RDB and Repology
    async def empty_rdb_details():
        return None

    async def empty_repology_info():
        return None

    # Use Repology project name if available, otherwise use package_name
    repology_query = repology_project if repology_project else package_name

    rdb_task = (
        rdb_client.get_package_details(package_name)
        if rdb_client
        else empty_rdb_details()
    )
    repology_task = (
        package_checker.repology.get_project_info(repology_query)
        if package_checker and package_checker.repology
        else empty_repology_info()
    )

    rdb_info, repology_info = await asyncio.gather(rdb_task, repology_task, return_exceptions=True)

    # Handle errors
    if isinstance(rdb_info, Exception):
        logger.error(f"RDB error: {rdb_info}")
        rdb_info = None

    if isinstance(repology_info, Exception):
        logger.error(f"Repology error: {repology_info}")
        repology_info = None

    if not rdb_info and not repology_info:
        await message.edit_text(
            f"❌ Информация о пакете '<code>{html.escape(package_name)}</code>' не найдена.",
            reply_markup=keyboards.back_to_search_keyboard()
        )
        return

    # Use Repology project name for display if available, otherwise use package_name
    display_name = repology_project if repology_project else package_name

    # Format detailed information
    text = format_package_details(display_name, rdb_info, repology_info)

    # Get search data from state for back button
    data = await state.get_data()
    search_query = data.get('search_query', '')

    keyboard = keyboards.package_details_keyboard(package_name, search_query)

    await message.edit_text(text, reply_markup=keyboard, disable_web_page_preview=True)


# ===== Pagination Handlers =====

@router.callback_query(F.data.startswith("search_page:"))
async def callback_search_page(callback: CallbackQuery, state: FSMContext):
    """Handle pagination of search results."""
    parts = callback.data.split(":")
    query = parts[1]
    page = int(parts[2])

    # Get results from state
    data = await state.get_data()
    results = data.get('search_results', [])

    if not results:
        await callback.answer("❌ Результаты поиска устарели", show_alert=True)
        return

    await show_search_results_page(
        callback.message,
        results,
        query,
        page
    )
    await safe_answer_callback(callback)


@router.callback_query(F.data.startswith("back_to_search:"))
async def callback_back_to_search(callback: CallbackQuery, state: FSMContext):
    """Return to search results."""
    query = callback.data.split(":", 1)[1]

    # Get results from state
    data = await state.get_data()
    results = data.get('search_results', [])

    if not results:
        # If results are lost, suggest new search
        await safe_edit_message(callback.message,
            "❌ Результаты поиска устарели.\n"
            "Выполните новый поиск.",
            reply_markup=keyboards.back_to_search_keyboard()
        )
        await safe_answer_callback(callback)
        return

    # Show first page of results
    await show_search_results_page(
        callback.message,
        results,
        query,
        page=0
    )
    await safe_answer_callback(callback)
