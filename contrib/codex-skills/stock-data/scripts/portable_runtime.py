"""OpenBB V4 adapter with project-local state and environment-only credentials.

No author-specific bootstrap or private configuration is loaded. State contains
empty credentials; supplied environment credentials remain in memory.
"""
import argparse
import json
import os
from pathlib import Path


def configure():
    state = Path(os.environ.get('STOCK_SKILLS_STATE_DIR', '.stock-skills-state')).expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True)
    config = state / 'config'
    config.mkdir(exist_ok=True)
    import openbb_core.app.constants as constants
    constants.HOME_DIRECTORY = state
    constants.OPENBB_DIRECTORY = config
    constants.USER_SETTINGS_PATH = config / 'user_settings.json'
    constants.SYSTEM_SETTINGS_PATH = config / 'system_settings.json'
    # Explicit build keeps first-run initialization predictable on V4.
    os.environ.setdefault('OPENBB_AUTO_BUILD', 'false')
    if not constants.USER_SETTINGS_PATH.exists():
        constants.USER_SETTINGS_PATH.write_text(json.dumps({
            'credentials': {},
            'preferences': {
                'data_directory': str(state / 'data'),
                'cache_directory': str(state / 'cache'),
                'export_directory': str(state / 'exports'),
                'user_styles_directory': str(state / 'styles'),
                'request_timeout': 30,
            },
            'defaults': {'commands': {}},
        }, indent=2), encoding='utf-8')
    return state


def get_obb():
    configure()
    from openbb import obb
    from openbb_akshare.models.equity_historical import AKShareEquityHistoricalFetcher
    from openbb_akshare.models.company_news import AKShareCompanyNewsFetcher
    from openbb_akshare.models.equity_search import AKShareEquitySearchFetcher
    for fetcher in (AKShareEquityHistoricalFetcher, AKShareCompanyNewsFetcher, AKShareEquitySearchFetcher):
        fetcher.require_credentials = False
    if not hasattr(obb, 'equity'):
        raise RuntimeError('Build V4 extensions first: python stock-data/scripts/portable_runtime.py --build')
    from openbb_core.app.service.user_service import UserService
    for name in ('tushare_api_key', 'akshare_api_key'):
        value = os.environ.get(name.upper())
        if value:
            if not hasattr(obb.user.credentials, name):
                raise RuntimeError('Optional provider extension is missing; install the documented matching V4 extension')
            setattr(obb.user.credentials, name, value)
            setattr(UserService().default_user_settings.credentials, name, value)
    return obb


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--build', action='store_true')
    args = parser.parse_args()
    configure()
    if args.build:
        import openbb
        openbb.build()
        print('V4 extensions built; no credentials were saved.')
    else:
        get_obb()
        print('V4 SDK initialized; no credentials were displayed.')
