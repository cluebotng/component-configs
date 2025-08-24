import os
from pathlib import PosixPath
from typing import List, Optional

import requests
from fabric import Connection, Config, task


TARGET_USER = os.environ.get("TARGET_USER")
TOOL_BASE_DIR = PosixPath("/data/project")


def _get_head_ref() -> Optional[str]:
    r = requests.get("https://api.github.com/repos/cluebotng/component-configs/git/refs")
    r.raise_for_status()
    for branch in r.json():
        if branch["ref"] == 'refs/heads/main':
            return branch["object"]["sha"]
    return None


def _get_config_url(config_url: str, latest_sha: Optional[str]):
    if not latest_sha:
        latest_sha = 'refs/heads/main'
    return f'https://raw.githubusercontent.com/cluebotng/component-configs/{latest_sha}/{config_url}.yaml'


def _get_target_tools() -> List[str]:
    return [
        config.name.split(".yaml")[0]
        for config in PosixPath(__file__).parent.glob("*.yaml")
    ]


def _setup_component_configs(tool_name: str, latest_sha: Optional[str]):
    config_url = _get_config_url(tool_name, latest_sha)
    print(f'[{tool_name}] applying {config_url}')

    c = Connection(
        "login.toolforge.org",
        config=Config(
            overrides={
                "sudo": {"user": f"tools.{tool_name}", "prefix": "/usr/bin/sudo -ni"}
            }
        ),
    )
    c.sudo(
        f"bash -c 'curl -s --fail {config_url} | "
        f"XDG_CONFIG_HOME='{TOOL_BASE_DIR / tool_name}' toolforge components config create'",
    )


def _generate_workflow(tool_name: str):
    # We do this to avoid `yaml` as a dep, it's simple enough
    config = f'name: \'Trigger deploy for {tool_name}\'\n'
    config += f'on: {{ push: {{ branches: [ main ], paths: [ \'{tool_name}.yaml\' ] }} }}\n'
    config += 'jobs:\n'
    config += '  deploy:\n'
    config += '    runs-on: ubuntu-latest\n'
    config += f'    environment: \'{tool_name}\'\n'
    config += '    steps:\n'

    # Until T401868 is resolved, update the tool with the config we want deployed
    # Note: the config will not be re-fetched as `source_url` cannot be rewritten on the same sha... so this is '1 off'
    config += '      - uses: cluebotng/ci-execute-fabric@main\n'
    config += '        with:\n'
    config += f'          user: \'{tool_name}\'\n'
    config += '          task: setup\n'
    config += '          ssh_key: ${{ secrets.CI_SSH_KEY }}\n'

    config += '      - uses: cluebotng/ci-toolforge-deploy@main\n'
    config += '        with:\n'
    config += f'          tool: \'{tool_name}\'\n'
    config += '          token: \'${{ secrets.TOOLFORGE_DEPLOY_TOKEN }}\'\n'
    return config


@task()
def create_workflows(_ctx):
    """Generate Github workflows for each tool"""
    for tool_name in _get_target_tools():
        workflow_file = PosixPath(__file__).parent / ".github" / "workflows" / f"{tool_name}.yaml"
        with workflow_file.open('w') as fh:
            fh.write(_generate_workflow(tool_name))


@task()
def setup(_ctx):
    """Ensure the tool accounts have component configs setup."""
    latest_sha = _get_head_ref()
    for tool_name in _get_target_tools():
        if TARGET_USER is None or tool_name == TARGET_USER:
            _setup_component_configs(tool_name, latest_sha)
