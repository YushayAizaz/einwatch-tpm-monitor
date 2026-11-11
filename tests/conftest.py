"""Keep test databases inside the disposable project cache, including on first run."""


def pytest_configure(config):
    (config.rootpath / ".pytest_cache").mkdir(exist_ok=True)
