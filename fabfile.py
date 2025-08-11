from pathlib import PosixPath
from typing import List

from fabric import Connection, Config, task


CONFIG_BASE_URL = "https://raw.githubusercontent.com/cluebotng/bot/refs/heads/main"
TOOL_BASE_DIR = PosixPath("/data/project")


def _get_target_tools() -> List[str]:
    return [
        config.name.split(".yaml")[0]
        for config in PosixPath(__file__).parent.glob("*.yaml")
    ]


def _has_no_component_configs(tool_name: str):
    c = Connection(
        "login.toolforge.org",
        config=Config(
            overrides={
                "sudo": {"user": f"tools.{tool_name}", "prefix": "/usr/bin/sudo -ni"}
            }
        ),
    )
    resp = c.sudo(
        f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / tool_name}' toolforge components config show",
        hide=True,
        warn=True,
    )
    return (
        resp.exited == 1
        and (
            f"Error: Unable to find namespace tool-{tool_name} or "
            f"config {tool_name}-config "
            f"for {tool_name}"
        )
        in resp.stderr
    )


def _setup_component_configs(tool_name: str):
    config_url = f'{CONFIG_BASE_URL.rstrip("/")}/{tool_name}.yaml'
    print(f'Applying to {tool_name}: {config_url}')

    c = Connection(
        "login.toolforge.org",
        config=Config(
            overrides={
                "sudo": {"user": f"tools.{tool_name}", "prefix": "/usr/bin/sudo -ni"}
            }
        ),
    )
    c.sudo(
        f"curl --fail {config_url} | "
        f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / tool_name}' toolforge components config create",
    )


@task()
def setup(_ctx):
    """Ensure the tool accounts have component configs setup."""
    for tool_name in _get_target_tools():
        print(f'Checking {tool_name}')
        if _has_no_component_configs(tool_name):
            _setup_component_configs(tool_name)
